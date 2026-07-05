"""VS-08 Block C1: MultiProcessRunner prepared work specs → plan-ordered,
deduped results.

4 EpisodeWorkSpecs across 2 shards (in-process factory). Results must retain
PLAN order and carry episode_key. If a duplicate episode_key somehow arrives,
the runner keeps the first and warns (never raises).
"""
from __future__ import annotations

from typing import Any

import pytest

from bench_env.config import RunnerConfig
from bench_env.runner.base import EpisodeResult, ExecutionResult
from bench_env.runner.events import ExecutionEvent, NullEventSink
from bench_env.runner.multiprocess import MultiProcessRunner
from bench_env.runner.work_spec import EpisodeWorkSpec


def _spec(i: int) -> EpisodeWorkSpec:
    return EpisodeWorkSpec(
        episode_key=f"fake.T|i{i}|s{i}|r1|t0",
        task_base_id="fake.T",
        instance_id=i,
        instance_seed=i,
        template_index=0,
        params={},
        trial_id=0,
        max_steps=5,
    )


def _result(spec: EpisodeWorkSpec) -> EpisodeResult:
    return EpisodeResult(
        task_id=spec.task_base_id,
        task_name=spec.task_base_id,
        suite="fake",
        execution=ExecutionResult(
            steps=1, trace=[], runtime_s=0.01, finished=True, truncated=False,
            stop_reason="complete",
        ),
        trial_id=spec.trial_id,
        apps=["fake"],
        max_steps=spec.max_steps,
        episode_key=spec.episode_key,
    )


def _config(**overrides: Any) -> RunnerConfig:
    base = dict(
        agent="probe", model_name="probe", quiet=True,
        parallel=2, processes=2, repeat_n=1,
    )
    base.update(overrides)
    return RunnerConfig(**base)


@pytest.mark.asyncio
async def test_prepared_work_specs_results_in_plan_order_with_episode_key(tmp_path) -> None:
    """4 specs across 2 shards: results returned in PLAN order with episode_key."""
    specs = [_spec(i) for i in range(4)]

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        # Deliberately return results in REVERSE order per shard so we can
        # assert the parent reconstructs plan order.
        async def _run() -> list[EpisodeResult]:
            return [_result(ws) for ws in reversed(work_specs)]

        return _run()

    runner = MultiProcessRunner(
        [], _config(runs_dir=tmp_path),
        child_runner_factory=factory,
        event_sink=NullEventSink(),
        prepared_work_specs=specs,
    )
    results = await runner.run()

    assert len(results) == 4
    # PLAN order is the input order of prepared_work_specs.
    assert [r["episode_key"] for r in results] == [s.episode_key for s in specs]


@pytest.mark.asyncio
async def test_run_dedups_duplicate_episode_key_keeping_first(tmp_path) -> None:
    """If a duplicate episode_key arrives (e.g. a buggy child re-emits), the
    runner keeps the FIRST occurrence and warns — it does NOT raise. The
    platform's reconciliation already raises on dup; the runner hands back a
    clean deduped list."""
    specs = [_spec(i) for i in range(2)]

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        async def _run() -> list[EpisodeResult]:
            results = [_result(ws) for ws in work_specs]
            # Duplicate the first result (same episode_key).
            if work_specs:
                results.append(_result(work_specs[0]))
            return results

        return _run()

    runner = MultiProcessRunner(
        [], _config(processes=1, parallel=1, runs_dir=tmp_path),
        child_runner_factory=factory,
        event_sink=NullEventSink(),
        prepared_work_specs=specs,
    )
    results = await runner.run()

    # Deduped: only 2 distinct episode_keys remain.
    keys = [r["episode_key"] for r in results]
    assert len(results) == 2
    assert set(keys) == {specs[0].episode_key, specs[1].episode_key}
    # No duplicate keys in the returned list.
    assert len(keys) == len(set(keys))


@pytest.mark.asyncio
async def test_prepared_work_carries_episode_key_in_result_dict(tmp_path) -> None:
    """Each returned result dict carries episode_key (Contract 3)."""
    specs = [_spec(0), _spec(1)]

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        async def _run() -> list[EpisodeResult]:
            return [_result(ws) for ws in work_specs]

        return _run()

    runner = MultiProcessRunner(
        [], _config(processes=1, parallel=1, runs_dir=tmp_path),
        child_runner_factory=factory,
        prepared_work_specs=specs,
    )
    results = await runner.run()
    for r in results:
        assert "episode_key" in r
        assert r["episode_key"] is not None
