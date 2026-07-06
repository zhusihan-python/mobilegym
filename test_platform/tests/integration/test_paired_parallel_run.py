"""VS-10 Block C: PairedParallelRunExecutor end-to-end behavior.

Covers the full paired-parallel lifecycle for a 2-lane run:
- both lanes run CONCURRENTLY (asyncio.gather) after materialization;
- results still pair by pair_key (not arrival order) — Contract C;
- per-lane projected state is captured BEFORE discard and the integrity report
  carries a path-level diff (Contract 4);
- sibling failure cancel/drain (Contract 8): one lane throwing cancels the
  other, the env is still closed, no duplicate terminal events;
- worker.started/stopped emitted per lane (Contract 9);
- the parallel executor routes through _execute_paired by the execution axis
  (Contract 1: execution="parallel").

The fakes mirror test_paired_serial_run.py but force ``execution: parallel`` in
the compare node and use a parallel-aware run builder.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.task.judge import JudgeResult
from test_platform.persistence.database import Database
from test_platform.services.execution import PairedParallelRunExecutor

# Reuse the 2-lane run builder + fakes from the materializer test.
from test_platform.tests.integration.test_materializer import (
    _create_paired_run,
    _make_target,
    _settings,
)
from test_platform.tests.integration.test_paired_serial_run import (
    _PairedEnv,
    _PairedTask,
    _PairedTaskFactory,
    _ScriptedAgent,
)
from test_platform.persistence.repositories import ProjectRepository


def _build_paired_parallel_run(database: Database, settings, *, repeat_n: int = 2):
    """Create a 2-lane run with two distinct targets and execution=parallel."""
    ProjectRepository(database).create("PairedParallel")
    baseline_target = _make_target(database, name="baseline", revision="seed-v1")
    candidate_target = _make_target(database, name="candidate", revision="seed-v1")
    return _create_paired_run(
        database,
        settings,
        baseline_target_id=baseline_target,
        candidate_target_id=candidate_target,
        repeat_n=repeat_n,
    )


def _make_executor(
    database,
    settings,
    *,
    baseline_agent: _ScriptedAgent,
    candidate_agent: _ScriptedAgent,
    baseline_env: _PairedEnv,
    candidate_env: _PairedEnv,
):
    """Build a PairedParallelRunExecutor with deterministic fakes.

    env_factory is called per-lane; agent_factory is called per-lane. Each lane
    may run multiple episodes concurrently — the fake envs handle that."""
    materialize_env = _PairedEnv(label="materialize")
    env_by_lane = {"baseline": baseline_env, "candidate": candidate_env}
    # env_factory is called for materialize first, then per-lane.
    call_count = {"n": 0}

    def env_factory(lane):
        # The first call is the materialize phase (SingleLaneMaterializer).
        call_count["n"] += 1
        if call_count["n"] == 1:
            return materialize_env
        return env_by_lane[lane.lane_key]

    agent_by_lane = {"baseline": baseline_agent, "candidate": candidate_agent}
    return PairedParallelRunExecutor(
        database,
        settings,
        task_factory=_PairedTaskFactory(),
        env_factory=env_factory,
        agent_factory=lambda lane: agent_by_lane[lane.lane_key],
    )


@pytest.mark.asyncio
async def test_paired_parallel_run_pairs_by_key_not_arrival_order(tmp_path):
    """Both lanes run concurrently; results still pair by pair_key. With
    repeat_n=2 there are 2 pairs; both are STABLE_PASS."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _build_paired_parallel_run(database, settings, repeat_n=2)

        detail = await _make_executor(
            database,
            settings,
            baseline_agent=_ScriptedAgent(succeed=True),
            candidate_agent=_ScriptedAgent(succeed=True),
            baseline_env=_PairedEnv(label="baseline"),
            candidate_env=_PairedEnv(label="candidate"),
        ).execute_run(run.id)

        assert detail.state == "completed"
        # 2 episodes × 2 lanes = 4 episode_attempts.
        attempts = database.connection.execute(
            "SELECT COUNT(*) AS n FROM episode_attempts"
        ).fetchone()
        assert attempts["n"] == 4

        # 2 comparison_pairs (one per pair_key), both STABLE_PASS.
        pairs = database.connection.execute(
            "SELECT pair_key, classification FROM comparison_pairs ORDER BY pair_key"
        ).fetchall()
        assert len(pairs) == 2
        for row in pairs:
            assert row["classification"] == "stable_pass"
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_parallel_run_records_path_level_integrity(tmp_path):
    """Contract 4: the integrity report carries a path-level diff (path_diffs)
  in the comparison_pairs integrity_json when the fixture diverges."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _build_paired_parallel_run(database, settings, repeat_n=1)
        # Candidate env returns a DIFFERENT stable state → projection mismatch.
        candidate_env = _PairedEnv(
            label="candidate",
            force_state={
                "apps": {"fake": {"label": "candidate", "tampered": True}},
                "os": {"time": {"mode": "fixed"}},
            },
        )

        await _make_executor(
            database,
            settings,
            baseline_agent=_ScriptedAgent(succeed=True),
            candidate_agent=_ScriptedAgent(succeed=True),
            baseline_env=_PairedEnv(label="baseline"),
            candidate_env=candidate_env,
        ).execute_run(run.id)

        comp = database.connection.execute(
            "SELECT classification, integrity_json FROM comparison_pairs"
        ).fetchone()
        assert comp["classification"] == "pairing_violation"
        integrity = json.loads(comp["integrity_json"])
        assert integrity["status"] == "projection_mismatch"
        # Contract 4: path-level diff present.
        assert "path_diffs" in integrity
        assert isinstance(integrity["path_diffs"], list)
        assert len(integrity["path_diffs"]) >= 1
        assert any(
            "apps.fake" in d.get("path", "") for d in integrity["path_diffs"]
        )
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_parallel_sibling_failure_cancels_other_lane(tmp_path):
    """Contract 8: if one lane throws a non-cancel exception, the other lane is
    cancelled (token.cancel), awaited to completion, and its env is closed. No
    duplicate terminal events. The executor re-raises the sibling exception so
    the supervisor can emit run.failed; the run is NOT left running."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _build_paired_parallel_run(database, settings, repeat_n=1)

        class _CrashingEnv(_PairedEnv):
            async def get_state(self, required_apps=None):
                raise RuntimeError("lane crashed during setup")

        crashing_env = _CrashingEnv(label="candidate")
        baseline_env = _PairedEnv(label="baseline")

        executor = _make_executor(
            database,
            settings,
            baseline_agent=_ScriptedAgent(succeed=True),
            candidate_agent=_ScriptedAgent(succeed=True),
            baseline_env=baseline_env,
            candidate_env=crashing_env,
        )

        # The executor re-raises the sibling exception (so the supervisor emits
        # run.failed). The other lane was drained and its env closed.
        with pytest.raises(RuntimeError, match="lane crashed"):
            await executor.execute_run(run.id)

        # Contract 8: the baseline (non-crashing) env was closed — no leak.
        assert baseline_env.closed is True
        # The candidate (crashing) env was also closed (its finally block ran).
        assert crashing_env.closed is True

        # P1 fix: sibling failure → lane_attempts and run_attempts finalized as
        # 'failed' (NOT 'cancelled'). The supervisor will mark runs as 'failed'.
        lane_rows = database.connection.execute(
            "SELECT state FROM lane_attempts "
            "JOIN lanes ON lanes.id = lane_attempts.lane_id "
            "WHERE lanes.run_id = ?",
            (run.id,),
        ).fetchall()
        for row in lane_rows:
            assert row["state"] == "failed", (
                f"lane_attempt state={row['state']} (expected 'failed' for sibling failure, "
                "not 'cancelled')"
            )
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_parallel_emits_worker_started_stopped_per_lane(tmp_path):
    """Contract 9: worker.started/stopped emitted per lane task (since
    _run_lane calls Controller.run directly, no worker lifecycle from
    ParallelRunner). Uses lane-scoped worker_id."""
    from test_platform.execution.event_writer import EventWriter

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _build_paired_parallel_run(database, settings, repeat_n=1)
        writer = EventWriter(database)

        await _make_executor(
            database,
            settings,
            baseline_agent=_ScriptedAgent(succeed=True),
            candidate_agent=_ScriptedAgent(succeed=True),
            baseline_env=_PairedEnv(label="baseline"),
            candidate_env=_PairedEnv(label="candidate"),
        ).execute_run(run.id, run_event_writer=writer)

        worker_events = database.connection.execute(
            "SELECT type, worker_id FROM events "
            "WHERE type IN ('worker.started','worker.stopped') ORDER BY sequence"
        ).fetchall()
        worker_ids = {row["worker_id"] for row in worker_events}
        # One worker per lane (baseline + candidate).
        assert "baseline" in worker_ids
        assert "candidate" in worker_ids
        # Each lane has exactly one started + one stopped.
        started = [e for e in worker_events if e["type"] == "worker.started"]
        stopped = [e for e in worker_events if e["type"] == "worker.stopped"]
        assert len(started) == 2
        assert len(stopped) == 2
    finally:
        database.close()


@pytest.mark.asyncio
async def test_paired_parallel_regression_classification(tmp_path):
    """Baseline PASS, candidate FAIL → REGRESSION even when run in parallel."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _build_paired_parallel_run(database, settings, repeat_n=1)

        await _make_executor(
            database,
            settings,
            baseline_agent=_ScriptedAgent(succeed=True),
            candidate_agent=_ScriptedAgent(succeed=False),
            baseline_env=_PairedEnv(label="baseline"),
            candidate_env=_PairedEnv(label="candidate"),
        ).execute_run(run.id)

        comp = database.connection.execute(
            "SELECT classification FROM comparison_pairs"
        ).fetchone()
        assert comp["classification"] == "regression"
    finally:
        database.close()
