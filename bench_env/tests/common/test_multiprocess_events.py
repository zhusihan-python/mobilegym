"""VS-08 Block C1: MultiProcessRunner in-process event bridging + worker_id
normalization.

Drives MultiProcessRunner with an in-process ``child_runner_factory`` (no real
mp.Process) so the orchestration logic (shard assignment, envelope bridging,
worker_id normalization, dedup) is exercised deterministically without spawn
overhead. The fake children emit ExecutionEvents through the shared queue;
the parent must forward them to the external EventSink with NORMALIZED
worker_ids (``W0`` → ``p{rank:02d}-W0``) so two shards' ``W0`` never collide.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bench_env.config import RunnerConfig
from bench_env.runner.base import EpisodeResult, ExecutionResult
from bench_env.runner.cancellation import CancellationToken
from bench_env.runner.events import ExecutionEvent
from bench_env.runner.multiprocess import MultiProcessRunner
from bench_env.runner.work_spec import EpisodeWorkSpec


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


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
async def test_in_process_children_events_forwarded_with_normalized_worker_id(tmp_path) -> None:
    """Child emits episode.*/worker.* events; parent forwards them with
    normalized worker_id (p00-W0) and shard_rank in payload."""
    sink = _CollectingSink()
    specs = [_spec(i) for i in range(4)]

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        rank = shard_spec.rank

        async def _run() -> list[EpisodeResult]:
            # Emit a worker.started with bare W0 (must be normalized to p{rank}-W0).
            event_sink.emit(ExecutionEvent(
                type="worker.started", timestamp="", worker_id="W0",
                payload={},
            ))
            results: list[EpisodeResult] = []
            for ws in work_specs:
                event_sink.emit(ExecutionEvent(
                    type="episode.started", timestamp="", worker_id="W0",
                    task_id=ws.task_base_id, trial_id=ws.trial_id,
                    episode_key=ws.episode_key,
                ))
                event_sink.emit(ExecutionEvent(
                    type="episode.completed", timestamp="", worker_id="W0",
                    task_id=ws.task_base_id, trial_id=ws.trial_id,
                    episode_key=ws.episode_key, payload={"outcome": "PASS"},
                ))
                results.append(_result(ws))
            event_sink.emit(ExecutionEvent(
                type="worker.stopped", timestamp="", worker_id="W0",
            ))
            return results

        return _run()

    runner = MultiProcessRunner(
        [], _config(runs_dir=tmp_path),
        child_runner_factory=factory,
        event_sink=sink,
        prepared_work_specs=specs,
    )
    results = await runner.run()

    # Results returned (deduped by episode_key, plan order).
    assert len(results) == 4
    keys = [r.get("episode_key") for r in results]
    assert keys == [s.episode_key for s in specs]

    # External sink received the forwarded events.
    started = [e for e in sink.events if e.type == "worker.started"]
    assert len(started) == 2  # two shards
    worker_ids = sorted(e.worker_id for e in started)
    # NORMALIZED: W0 -> p00-W0 and p01-W0 (distinct across shards).
    assert worker_ids == ["p00-W0", "p01-W0"]
    # shard_rank carried in payload.
    ranks = sorted(e.payload.get("shard_rank") for e in started)
    assert ranks == [0, 1]

    # Episode events forwarded with normalized worker_id + original episode_key.
    ep_started = [e for e in sink.events if e.type == "episode.started"]
    assert len(ep_started) == 4
    for ev in ep_started:
        assert ev.worker_id in {"p00-W0", "p01-W0"}
        assert ev.episode_key is not None


@pytest.mark.asyncio
async def test_two_shards_emit_w0_distinct_in_external_sink(tmp_path) -> None:
    """Contract 6: two shards both emitting worker.started with W0 must produce
    distinct normalized ids (p00-W0 and p01-W0) so the frontend's activeWorkers
    Set does not collapse them."""
    sink = _CollectingSink()
    specs = [_spec(i) for i in range(2)]

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        async def _run() -> list[EpisodeResult]:
            event_sink.emit(ExecutionEvent(
                type="worker.started", timestamp="", worker_id="W0",
            ))
            for ws in work_specs:
                event_sink.emit(ExecutionEvent(
                    type="episode.completed", timestamp="", worker_id="W0",
                    task_id=ws.task_base_id, trial_id=ws.trial_id,
                    episode_key=ws.episode_key, payload={"outcome": "PASS"},
                ))
                event_sink.emit(ExecutionEvent(
                    type="worker.stopped", timestamp="", worker_id="W0",
                ))
            return [_result(ws) for ws in work_specs]

        return _run()

    runner = MultiProcessRunner(
        [], _config(processes=2, parallel=2, runs_dir=tmp_path),
        child_runner_factory=factory,
        event_sink=sink,
        prepared_work_specs=specs,
    )
    await runner.run()

    started_ids = {e.worker_id for e in sink.events if e.type == "worker.started"}
    # DISTINCT normalized ids — no collision.
    assert started_ids == {"p00-W0", "p01-W0"}
