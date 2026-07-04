"""VS-07 Block C: ParallelRunner prepared work items + events + cancellation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bench_env.config import RunnerConfig
from bench_env.runner.cancellation import CancellationToken
from bench_env.runner.events import ExecutionEvent, NullEventSink
from bench_env.runner.parallel import ParallelRunner
from bench_env.runner.serial import PreparedWorkItem


class _Task:
    suite = "fake"
    apps: list[str] = []
    max_steps = 15

    def __init__(self, _seed: int | None = None, **params: Any) -> None:
        self._seed = _seed
        self.params = {"value": "sampled", **params}

    @property
    def id(self) -> str:
        return "fake.Sample"

    @property
    def description(self) -> str:
        return f"Sample {self.params['value']}"


@dataclass
class _Result:
    task_id: str
    trial_id: int
    max_steps: int
    success: bool = True
    error: str | None = None
    goal_mismatches: list | None = None
    unexpected_changes: list | None = None
    episode_key: str | None = None
    progress: float = 1.0
    false_complete: bool = False
    overdue_termination: bool = False
    goal_success: bool = True
    no_unexpected_changes: bool = True
    steps: int = 1

    def to_dict(self) -> dict:
        return {"task_id": self.task_id, "trial_id": self.trial_id, "is_success": self.success}


class _ProbeRunnerBase(ParallelRunner):
    """Probe that no-ops print_summary (the real one needs ExecutionResult attrs)."""

    def print_summary(self, results, run_dir=None):
        return None


class _Recorder:
    run_dir = Path("fake-run")

    def finish_run(self, *, repeat_n: int, pass_k) -> Path:
        return self.run_dir


class _FakeEnv:
    """Env that records which worker used it and supports context-manager use."""

    def __init__(self, wid: int) -> None:
        self.wid = wid
        self.closed = False

    def set_browser_log_dir(self, *a, **kw):
        pass

    def set_current_task(self, *a, **kw):
        pass

    async def close(self):
        self.closed = True


class _FakeEnvPool:
    """Minimal async-context-manager env pool with N fake envs."""

    def __init__(self, n: int) -> None:
        self.n = n
        self._envs = [_FakeEnv(i) for i in range(n)]

    def __getitem__(self, idx: int) -> _FakeEnv:
        return self._envs[idx]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        for env in self._envs:
            await env.close()
        return False


class _CollectingSink:
    def __init__(self) -> None:
        self.events: list[ExecutionEvent] = []

    def emit(self, event: ExecutionEvent) -> None:
        self.events.append(event)


def _config(**overrides: Any) -> RunnerConfig:
    return RunnerConfig(agent="probe", model_name="probe-model", quiet=True, **overrides)


@pytest.mark.asyncio
async def test_prepared_work_results_retain_plan_order_despite_completion_order() -> None:
    """4 items, parallelism 2, deliberately out-of-order completion: results come
    back in plan (input) order, not completion order."""

    class _OrderedProbeRunner(_ProbeRunnerBase):
        """Overrides run_episode to record call order and return results that
        complete out of input order, so we can assert the returned list preserves
        plan order."""
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.call_episode_keys: list[str] = []

        async def run_episode(self, env, agent, task, max_steps, recorder, *,
                               trial_id=0, evaluator=None, loop_threshold=0,
                               cancellation_token=None, event_sink=None,
                               worker_id="serial", episode_key=None):
            self.call_episode_keys.append(episode_key)
            # Make item at idx 1 finish last by yielding control more.
            idx = int(episode_key.split("|")[-1].removeprefix("t"))
            if idx == 1:
                await asyncio.sleep(0.05)
            return _Result(task_id=task.id, trial_id=trial_id, max_steps=max_steps,
                           episode_key=episode_key)

    items = [
        PreparedWorkItem(episode_key=f"fake.Sample|i0|s1|r1|t{i}", task=_Task(_seed=i, value=f"p{i}"),
                         trial_id=i, max_steps=45)
        for i in range(4)
    ]
    pool = _FakeEnvPool(2)
    runner = _OrderedProbeRunner(
        pool, lambda: object(), [it.task for it in items], _config(parallel=2),
        recorder=_Recorder(), prepared_work_items=items,
    )

    results = await runner.run()

    # Returned list is in PLAN order (episode_key t0..t3), not completion order.
    assert [r.episode_key for r in results] == [
        "fake.Sample|i0|s1|r1|t0",
        "fake.Sample|i0|s1|r1|t1",
        "fake.Sample|i0|s1|r1|t2",
        "fake.Sample|i0|s1|r1|t3",
    ]
    assert len(results) == 4
    # All envs were closed (cleanup ran).
    assert all(env.closed for env in pool._envs)


@pytest.mark.asyncio
async def test_prepared_work_emits_worker_start_stop_per_worker() -> None:
    """Each worker emits worker.started and worker.stopped with W0..Wn ids."""

    class _ProbeRunner(_ProbeRunnerBase):
        async def run_episode(self, *a, **kw):
            return _Result(task_id="fake.Sample", trial_id=kw.get("trial_id", 0),
                           max_steps=10, episode_key=kw.get("episode_key"))

    items = [
        PreparedWorkItem(episode_key=f"k{i}", task=_Task(_seed=i), trial_id=i, max_steps=10)
        for i in range(3)
    ]
    pool = _FakeEnvPool(2)
    sink = _CollectingSink()
    runner = _ProbeRunner(
        pool, lambda: object(), [it.task for it in items], _config(parallel=2),
        recorder=_Recorder(), prepared_work_items=items, event_sink=sink,
    )

    await runner.run()

    started = [e for e in sink.events if e.type == "worker.started"]
    stopped = [e for e in sink.events if e.type == "worker.stopped"]
    assert len(started) == 2  # one per worker (n=2)
    assert len(stopped) == 2
    assert sorted(e.worker_id for e in started) == ["W0", "W1"]
    # Every start has a matching stop.
    assert sorted(e.worker_id for e in stopped) == ["W0", "W1"]


@pytest.mark.asyncio
async def test_prepared_work_episode_events_carry_worker_id_and_episode_key() -> None:
    """episode.started/completed events flow with worker_id + episode_key."""

    class _ProbeRunner(_ProbeRunnerBase):
        async def run_episode(self, env, agent, task, max_steps, recorder, *,
                               trial_id=0, evaluator=None, loop_threshold=0,
                               cancellation_token=None, event_sink=None,
                               worker_id="serial", episode_key=None):
            # Emit a started+completed pair manually to verify worker_id/episode_key
            # propagation through the run_episode kwargs.
            if event_sink is not None:
                event_sink.emit(ExecutionEvent(
                    type="episode.started", timestamp="", worker_id=worker_id,
                    task_id=task.id, trial_id=trial_id, episode_key=episode_key,
                ))
                event_sink.emit(ExecutionEvent(
                    type="episode.completed", timestamp="", worker_id=worker_id,
                    task_id=task.id, trial_id=trial_id, episode_key=episode_key,
                    payload={"outcome": "PASS"},
                ))
            return _Result(task_id=task.id, trial_id=trial_id, max_steps=max_steps,
                           episode_key=episode_key)

    items = [
        PreparedWorkItem(episode_key=f"k{i}", task=_Task(_seed=i), trial_id=i, max_steps=10)
        for i in range(2)
    ]
    pool = _FakeEnvPool(2)
    sink = _CollectingSink()
    runner = _ProbeRunner(
        pool, lambda: object(), [it.task for it in items], _config(parallel=2),
        recorder=_Recorder(), prepared_work_items=items, event_sink=sink,
    )

    await runner.run()

    episode_events = [e for e in sink.events if e.type.startswith("episode.")]
    assert len(episode_events) >= 4  # started+completed for 2 episodes
    # Every episode event has a non-None worker_id (W0 or W1) and episode_key.
    for ev in episode_events:
        assert ev.worker_id in {"W0", "W1"}
        assert ev.episode_key in {"k0", "k1"}


@pytest.mark.asyncio
async def test_cancellation_releases_all_fake_environments() -> None:
    """Cancelling mid-run must close every env in the pool (no leaks)."""

    class _ProbeRunner(_ProbeRunnerBase):
        async def run_episode(self, env, agent, task, max_steps, recorder, *,
                               trial_id=0, evaluator=None, loop_threshold=0,
                               cancellation_token=None, event_sink=None,
                               worker_id="serial", episode_key=None):
            # Block until cancelled so the token fires while running.
            await asyncio.sleep(30)

    items = [
        PreparedWorkItem(episode_key=f"k{i}", task=_Task(_seed=i), trial_id=i, max_steps=10)
        for i in range(4)
    ]
    pool = _FakeEnvPool(2)
    token = CancellationToken()
    runner = _ProbeRunner(
        pool, lambda: object(), [it.task for it in items], _config(parallel=2),
        recorder=_Recorder(), prepared_work_items=items, cancellation_token=token,
    )

    # Cancel shortly after starting.
    async def _cancel_soon():
        await asyncio.sleep(0.1)
        token.cancel()

    await asyncio.gather(runner.run(), _cancel_soon())

    # Every env was closed by EnvPool cleanup.
    assert all(env.closed for env in pool._envs)
