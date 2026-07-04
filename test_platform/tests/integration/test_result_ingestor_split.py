"""VS-07 Block D1: ResultIngestor terminal-state split.

The parallel executor must NOT mark a lane/run terminal per-episode (the first
episode to finish would otherwise complete the whole run). ``ingest_episode_attempt``
only inserts an episode_attempts row; ``finalize_lane_run`` is the single place
that transitions lane/run/run_attempt to a terminal state.
"""
from __future__ import annotations

from typing import Any

import pytest

from test_platform.persistence.database import Database
from test_platform.services.execution import (
    ResultIngestor,
    _episode_artifact_name,
    _episode_artifact_root,
)
from test_platform.tests.integration.test_single_lane_materialization import (
    _create_run,
    _settings,
)


def _multi_episode_run(database: Database, settings: PlatformSettings, *, repeat_n: int = 4):
    """Create a run via the shared helper with repeat_n>1 (multiple episodes, 1 lane).

    Each call uses a fresh idempotency key so multiple runs can coexist in one
    database during a test.
    """
    return _create_run(database, settings, repeat_n=repeat_n)


class _FakeResult:
    """Minimal EpisodeResult-like object for the ingestor path."""

    def __init__(self, *, task_id: str = "fake.SampleTask", trial_id: int = 0, success: bool = True):
        self.task_id = task_id
        self.task_name = task_id
        self.suite = "fake"
        self.trial_id = trial_id
        self.success = success
        self.error: str | None = None
        self.apps: list[str] = ["fake"]
        self.max_steps = 15
        self.episode_key: str | None = None
        self.goal_success = success
        self.no_unexpected_changes = True
        self.steps = 1
        self.goal_mismatches: list = []
        self.unexpected_changes: list = []
        self.progress = 1.0
        self.false_complete = False
        self.overdue_termination = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "trial_id": self.trial_id,
            "is_success": self.success,
            "is_error": not self.success,
        }


def _running_lane_attempt(database: Database, run_id: str) -> dict[str, str]:
    """Force the run/lane/run_attempt into 'running' so finalize can be tested."""
    now = "2026-07-04T00:00:00.000Z"
    database.connection.execute(
        "UPDATE runs SET state = 'running', started_at = ?, updated_at = ? WHERE id = ?",
        (now, now, run_id),
    )
    database.connection.execute(
        "UPDATE run_attempts SET state = 'running', started_at = ? WHERE run_id = ?",
        (now, run_id),
    )
    database.connection.execute(
        "UPDATE lane_attempts SET state = 'running', started_at = ? "
        "WHERE lane_id IN (SELECT id FROM lanes WHERE run_id = ?)",
        (now, run_id),
    )
    database.connection.commit()
    row = database.connection.execute(
        "SELECT la.id, la.artifact_root FROM lane_attempts la "
        "JOIN lanes l ON l.id = la.lane_id WHERE l.run_id = ? ORDER BY l.lane_key LIMIT 1",
        (run_id,),
    ).fetchone()
    return {"id": str(row["id"]), "artifact_root": str(row["artifact_root"])}


def _episode_keys(database: Database, run_id: str) -> list[str]:
    rows = database.connection.execute(
        "SELECT episode_key FROM episodes WHERE run_id = ? ORDER BY trial_id",
        (run_id,),
    ).fetchall()
    return [str(r["episode_key"]) for r in rows]


def test_ingest_episode_attempt_inserts_row_without_changing_lane_run_state(tmp_path):
    """ingest_episode_attempt only inserts an episode_attempts row; lane/run/run_attempt
    states are NOT touched (they stay 'running')."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _multi_episode_run(database, settings, repeat_n=4)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)
        assert len(keys) == 4

        ingestor = ResultIngestor(database)
        result = _FakeResult(task_id="fake.SampleTask", trial_id=0, success=True)
        summary = ingestor.ingest_episode_attempt(
            run_id=run.id,
            lane_attempt_id=lane_attempt["id"],
            episode_key=keys[0],
            result=result,
            artifact_root="lanes/candidate/trajectory/ep0",
        )

        assert summary["state"] == "completed"
        # One episode_attempt row inserted.
        n_attempts = database.connection.execute(
            "SELECT COUNT(*) FROM episode_attempts WHERE lane_attempt_id = ?",
            (lane_attempt["id"],),
        ).fetchone()[0]
        assert n_attempts == 1

        # CRITICAL: lane/run/run_attempt are STILL running (not terminal).
        run_state = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()["state"]
        run_attempt_state = database.connection.execute(
            "SELECT state FROM run_attempts WHERE run_id = ?", (run.id,)
        ).fetchone()["state"]
        lane_attempt_state = database.connection.execute(
            "SELECT state FROM lane_attempts WHERE id = ?", (lane_attempt["id"],)
        ).fetchone()["state"]
        assert run_state == "running"
        assert run_attempt_state == "running"
        assert lane_attempt_state == "running"
    finally:
        database.close()


def test_finalize_lane_run_transitions_lane_run_run_attempt_to_terminal(tmp_path):
    """finalize_lane_run sets lane/run/run_attempt to the given terminal state."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _multi_episode_run(database, settings, repeat_n=4)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)

        ingestor = ResultIngestor(database)
        # Ingest all 4 episodes (none of them touch lane/run state).
        for i, key in enumerate(keys):
            ingestor.ingest_episode_attempt(
                run_id=run.id,
                lane_attempt_id=lane_attempt["id"],
                episode_key=key,
                result=_FakeResult(trial_id=i),
                artifact_root=f"lanes/candidate/trajectory/ep{i}",
            )
        # Still running before finalize.
        assert database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()["state"] == "running"

        ingestor.finalize_lane_run(
            run_id=run.id,
            lane_attempt_id=lane_attempt["id"],
            terminal_state="completed",
        )

        assert database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()["state"] == "completed"
        assert database.connection.execute(
            "SELECT state FROM run_attempts WHERE run_id = ?", (run.id,)
        ).fetchone()["state"] == "completed"
        assert database.connection.execute(
            "SELECT state FROM lane_attempts WHERE id = ?", (lane_attempt["id"],)
        ).fetchone()["state"] == "completed"
    finally:
        database.close()


def test_finalize_lane_run_is_idempotent_for_terminal_states(tmp_path):
    """finalize_lane_run only updates rows still in cancellable states (idempotent)."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _multi_episode_run(database, settings, repeat_n=2)
        lane_attempt = _running_lane_attempt(database, run.id)

        ingestor = ResultIngestor(database)
        ingestor.finalize_lane_run(
            run_id=run.id,
            lane_attempt_id=lane_attempt["id"],
            terminal_state="completed",
        )
        # Capture ended_at; a second finalize must not change it (terminal → no-op).
        ended_before = database.connection.execute(
            "SELECT ended_at FROM runs WHERE id = ?", (run.id,)
        ).fetchone()["ended_at"]
        ingestor.finalize_lane_run(
            run_id=run.id,
            lane_attempt_id=lane_attempt["id"],
            terminal_state="cancelled",
        )
        ended_after = database.connection.execute(
            "SELECT ended_at FROM runs WHERE id = ?", (run.id,)
        ).fetchone()["ended_at"]
        assert ended_before == ended_after
        # State stays completed (the second call is a no-op since already terminal).
        assert database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()["state"] == "completed"
    finally:
        database.close()


def test_error_code_override_is_used_instead_of_derived_code(tmp_path):
    """When error_code_override is provided, it replaces the derived error_code."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _multi_episode_run(database, settings, repeat_n=2)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)

        ingestor = ResultIngestor(database)
        # A success result but with WORKER_CRASH override.
        summary = ingestor.ingest_episode_attempt(
            run_id=run.id,
            lane_attempt_id=lane_attempt["id"],
            episode_key=keys[0],
            result=_FakeResult(success=True),
            artifact_root="lanes/candidate/trajectory/ep0",
            error_code_override="WORKER_CRASH",
        )
        assert summary["error_code"] == "WORKER_CRASH"

        row = database.connection.execute(
            "SELECT error_code FROM episode_attempts WHERE lane_attempt_id = ?",
            (lane_attempt["id"],),
        ).fetchone()
        assert row["error_code"] == "WORKER_CRASH"
    finally:
        database.close()


def test_ingest_episode_result_backward_compat_wrapper_still_works(tmp_path):
    """The legacy ingest_episode_result wrapper composes the two new methods."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _multi_episode_run(database, settings, repeat_n=1)
        lane_attempt = _running_lane_attempt(database, run.id)
        keys = _episode_keys(database, run.id)

        ingestor = ResultIngestor(database)
        summary = ingestor.ingest_episode_result(
            run_id=run.id,
            lane_attempt_id=lane_attempt["id"],
            episode_key=keys[0],
            result=_FakeResult(success=True),
            artifact_root="lanes/candidate/trajectory/ep0",
        )
        assert summary["state"] == "completed"
        # Legacy wrapper finalizes too: run is now completed.
        assert database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()["state"] == "completed"
    finally:
        database.close()


# ---------------------------------------------------------------------------
# _episode_artifact_name / _episode_artifact_root
# ---------------------------------------------------------------------------


def test_episode_artifact_name_distinguishes_different_keys():
    """a|b and a/b sanitize to the same string but must produce DIFFERENT names
    (the hash suffix disambiguates them)."""
    name_pipe = _episode_artifact_name("fake.Task|i0|s1|r1|t0")
    name_slash = _episode_artifact_name("fake.Task/i0/s1/r1/t0")
    # Both sanitized to "fake.Task_i0_s1_r1_t0" but hashes differ.
    assert name_pipe != name_slash
    assert name_pipe.startswith("fake.Task_i0_s1_r1_t0")
    # Each ends with an 8-char hash suffix.
    assert len(name_pipe.split("_")[-1]) == 8


def test_episode_artifact_name_truncates_long_keys():
    """Very long keys are truncated to a readable prefix + hash suffix."""
    long_key = "x" * 200
    name = _episode_artifact_name(long_key)
    # Readable prefix capped at 64 chars + "_" + 8-char hash.
    prefix = name.rsplit("_", 1)[0]
    assert len(prefix) <= 64
    assert len(name.split("_")[-1]) == 8


def test_episode_artifact_name_safe_for_special_chars():
    """Special characters are replaced; empty sanitized result falls back."""
    name = _episode_artifact_name("a b|c@#.d")
    assert all(c not in name for c in ("|", "@", " "))


def test_episode_artifact_root_with_episode_key_uses_new_name():
    """When episode_key is provided, the artifact root uses _episode_artifact_name."""
    root = _episode_artifact_root(
        "lanes/candidate", result=None, repeat_n=1, episode_key="fake.Task|i0|s1|r1|t0"
    )
    expected_name = _episode_artifact_name("fake.Task|i0|s1|r1|t0")
    assert root == f"lanes/candidate/trajectory/{expected_name}"


def test_episode_artifact_root_without_episode_key_falls_back_to_task_trial():
    """Without episode_key, the legacy task_id+trial logic is used (CLI parity)."""

    class _R:
        task_id = "fake.Task"
        trial_id = 2

        def to_dict(self):
            return {"id": "fake.Task", "trial_id": 2}

    root = _episode_artifact_root("lanes/candidate", _R(), repeat_n=3)
    assert root == "lanes/candidate/trajectory/fake_Task_t2"
