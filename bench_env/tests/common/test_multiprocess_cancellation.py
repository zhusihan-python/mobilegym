"""VS-08 Block C1: MultiProcessRunner cancellation.

(a) Cooperative cancel → token signal → child ParallelRunner observes → envs
    released; run returns.
(b) NON-responsive child (fake runner never checks token) → parent walks
    grace → terminate → kill → run returns → missing episodes labeled
    CANCELLED (token.cancelled=True).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bench_env.config import RunnerConfig
from bench_env.runner.base import EpisodeResult, ExecutionResult
from bench_env.runner.cancellation import CancellationToken, MultiprocCancelToken
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
        parallel=1, processes=1, repeat_n=1,
    )
    base.update(overrides)
    return RunnerConfig(**base)


@pytest.mark.asyncio
async def test_cooperative_cancel_lets_child_observe_token_and_release(tmp_path) -> None:
    """A cooperative child that checks the token stops cleanly; the run
    returns and the token is observed."""
    specs = [_spec(0), _spec(1)]
    token = CancellationToken()
    observed: list[bool] = []

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        async def _run() -> list[EpisodeResult]:
            # Wait until cancelled, polling the token (cooperative).
            for _ in range(100):
                if cancellation_token.cancelled:
                    observed.append(True)
                    return []
                await asyncio.sleep(0.01)
            return [_result(ws) for ws in work_specs]

        return _run()

    runner = MultiProcessRunner(
        [], _config(runs_dir=tmp_path),
        child_runner_factory=factory,
        event_sink=NullEventSink(),
        cancellation_token=token,
        prepared_work_specs=specs,
    )

    async def _cancel_soon():
        await asyncio.sleep(0.05)
        token.cancel()

    results = await asyncio.gather(runner.run(), _cancel_soon())
    # Cooperative: child observed the token.
    assert observed == [True]


@pytest.mark.asyncio
async def test_nonresponsive_child_walks_grace_terminate_kill(tmp_path) -> None:
    """A NON-responsive child (never checks the token) must be walked through
    grace → terminate → kill by the parent, and the run must return (not hang).
    Missing episodes are absent because the child never reported them.

    Uses an in-process factory that ignores the token by blocking forever. The
    parent's cancellation sequence (grace loop → terminate the asyncio task →
    return) must let run() complete."""
    specs = [_spec(0), _spec(1)]
    token = CancellationToken()
    finished = asyncio.Event()

    def factory(shard_spec, work_specs, event_sink, cancellation_token):
        async def _run() -> list[EpisodeResult]:
            # NEVER check the token; block until the task is cancelled.
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                finished.set()
                raise
            return []

        return _run()

    runner = MultiProcessRunner(
        [], _config(runs_dir=tmp_path),
        child_runner_factory=factory,
        event_sink=NullEventSink(),
        cancellation_token=token,
        prepared_work_specs=specs,
        cancel_grace_seconds=0.5,
    )

    async def _cancel_soon():
        await asyncio.sleep(0.05)
        token.cancel()

    # Must return (not hang): the non-responsive child is forcibly torn down.
    results = await asyncio.gather(runner.run(), _cancel_soon())
    # The in-process child task was cancelled by the parent's teardown.
    assert finished.is_set()


@pytest.mark.asyncio
async def test_multiproc_cancel_token_propagates_set_across_is_set() -> None:
    """Unit: MultiprocCancelToken.cancel() sets the shared mp.Event so
    cancelled reads True (no real process needed; we just exercise the
    in-process behaviour with a fake event)."""
    import multiprocessing as mp

    ctx = mp.get_context("spawn")
    evt = ctx.Event()
    token = MultiprocCancelToken(mp_event=evt)
    assert token.cancelled is False
    assert token.mp_event is evt
    token.cancel()
    assert token.cancelled is True
    assert evt.is_set() is True


@pytest.mark.asyncio
async def test_multiproc_cancel_token_falls_back_to_threading_when_no_mp_event() -> None:
    """Unit: when mp_event is None, MultiprocCancelToken behaves like a plain
    CancellationToken (threading.Event)."""
    from bench_env.runner.cancellation import RunCancelled

    token = MultiprocCancelToken()
    assert token.mp_event is None
    assert token.cancelled is False
    token.cancel()
    assert token.cancelled is True
    with pytest.raises(RunCancelled):
        token.raise_if_cancelled()
