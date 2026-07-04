from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest

from bench_env.env.base import Action, ActionType, Observation, StepResult
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.services.execution import SerialRunExecutor
from test_platform.services.runs import RunService
from test_platform.tests.integration.test_single_lane_materialization import (
    _catalog,
    _create_run,
    _settings,
)


class _Stopwatch:
    total = 0.0

    def reset(self) -> None:
        return None

    def phase(self, name: str):
        class _Ctx:
            def __enter__(self_):
                return self_

            def __exit__(self_, *_):
                return False

        return _Ctx()

    def record(self, name: str, value: float) -> None:
        return None

    def to_flat(self) -> dict[str, float]:
        return {}

    def to_tree(self) -> list[dict[str, Any]]:
        return []

    def summary(self) -> str:
        return "fake"


class _CancellableFakeEnv:
    """Fake env whose step sleeps briefly so cancellation lands mid-loop.

    The step returns done=False after a short delay, letting the Controller
    loop iterate and observe the cancellation token at the next agent.act
    boundary. Blocking indefinitely would hide the cancellation from the
    cooperative check points.
    """

    supports_state_injection = True

    def __init__(self, *, step_delay: float = 0.1) -> None:
        self.stopwatch = _Stopwatch()
        self.step_count = 0
        self.closed = False
        self._step_delay = step_delay

    async def get_state(self, required_apps: list[str] | None = None) -> dict[str, Any]:
        return {"apps": {"fake": {}}, "os": {"time": {"mode": "fixed"}}}

    async def get_observation(self) -> Observation:
        return Observation(
            route={"app": "fake", "path": "/"},
            state=await self.get_state(),
            step_idx=self.step_count,
        )

    async def step(self, action: Action) -> StepResult:
        self.step_count += 1
        # Brief delay so the cancel lands while the run is "running", but the
        # loop still iterates and the cooperative check point fires.
        await asyncio.sleep(self._step_delay)
        return StepResult(observation=await self.get_observation(), done=False, info={})

    async def close(self) -> None:
        self.closed = True

    @property
    def agent_message(self) -> str | None:
        return None

    @property
    def agent_answer(self) -> str | None:
        return None


class _SlowAgent:
    name = "fake-agent"

    def __init__(self) -> None:
        self.history: list[Any] = []
        self.act_count = 0

    def reset(self, instruction: str) -> None:
        return None

    def act(self, obs: Observation) -> Action:
        self.act_count += 1
        return Action(action_type=ActionType.CLICK, data={"point": [0, 0]})

    def reset_history(self) -> None:
        self.history.clear()


class _FakeTask:
    apps = ["fake"]
    answer_fields = None
    suite = "fake"

    def __init__(self, _seed: int | None = None, **params: Any) -> None:
        self._seed = _seed
        self.params = {"choice": "default", **params}
        self._user_params = set(params.keys())

    @property
    def id(self) -> str:
        return "fake.SampleTask"

    @property
    def description(self) -> str:
        return f"Choose {self.params['choice']}"

    async def setup(self, env: Any) -> Observation:
        return await env.get_observation()

    def teardown(self, env: Any) -> None:
        return None

    def evaluate(self, input: Any) -> Any:
        from bench_env.task.judge import JudgeResult

        return JudgeResult.ok()


class _FakeTaskFactory:
    def instantiate(self, template, params: dict[str, Any] | None = None) -> _FakeTask:
        return _FakeTask(_seed=template.instance_seed, **(params or {}))


def _create_running_run(database: Database, settings: PlatformSettings):
    """Create a queued run via the service, returning (run, env, agent)."""
    run = _create_run(database, settings, repeat_n=1)
    return run


# ---------------------------------------------------------------------------
# The tests below drive the supervisor + executor directly. They do NOT go
# through HTTP (the cancel route is covered by the green implementation), but
# they verify the state-machine and event semantics the route depends on.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_queued_run_reaches_cancelled_state(tmp_path) -> None:
    """Cancelling before execution starts produces a cancelled run + event."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)

        # Build a supervisor with a slow executor, cancel before it runs.
        from test_platform.services.runs import RunSupervisor

        env = _CancellableFakeEnv()
        agent = _SlowAgent()
        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: env,
            agent_factory=lambda lane: agent,
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        await supervisor.start()

        # Cancel before the executor's task gets CPU. token is registered
        # synchronously in submit(), so request_cancel finds it immediately.
        cancelled = supervisor.request_cancel(run.id)
        supervisor.submit(run.id)
        # Let the loop drain the cancellation.
        await asyncio.sleep(0.3)
        await supervisor.stop()

        row = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert row["state"] == "cancelled"
        assert cancelled is True
    finally:
        database.close()


@pytest.mark.asyncio
async def test_cancel_running_run_runs_teardown_and_closes_env(tmp_path) -> None:
    """Cancelling mid-step cancels the run, runs teardown, and closes the env."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)
        from test_platform.services.runs import RunSupervisor

        env = _CancellableFakeEnv()
        agent = _SlowAgent()
        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: env,
            agent_factory=lambda lane: agent,
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        await supervisor.start()
        supervisor.submit(run.id)
        # Give the executor time to enter the blocking step.
        await asyncio.sleep(0.3)

        supervisor.request_cancel(run.id)
        await asyncio.sleep(0.5)
        await supervisor.stop()

        assert env.closed is True
        row = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert row["state"] == "cancelled"
    finally:
        database.close()


@pytest.mark.asyncio
async def test_cancel_emits_request_and_terminal_events(tmp_path) -> None:
    """Cancel must emit run.cancel_requested then run.cancelled."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)
        from test_platform.services.runs import RunSupervisor

        env = _CancellableFakeEnv()
        agent = _SlowAgent()
        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: env,
            agent_factory=lambda lane: agent,
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        await supervisor.start()
        supervisor.submit(run.id)
        await asyncio.sleep(0.2)
        supervisor.request_cancel(run.id)
        await asyncio.sleep(0.5)
        await supervisor.stop()

        event_types = [
            row["type"]
            for row in database.connection.execute(
                "SELECT type FROM events WHERE run_id = ? ORDER BY sequence",
                (run.id,),
            )
        ]
        assert "run.cancel_requested" in event_types
        assert "run.cancelled" in event_types
    finally:
        database.close()


@pytest.mark.asyncio
async def test_cancel_run_idempotent_returns_current_state(tmp_path) -> None:
    """Repeated cancel returns the same run state and does not error."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)
        from test_platform.services.runs import RunSupervisor

        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: _CancellableFakeEnv(),
            agent_factory=lambda lane: _SlowAgent(),
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        await supervisor.start()

        first = supervisor.request_cancel(run.id)
        second = supervisor.request_cancel(run.id)
        await asyncio.sleep(0.2)
        await supervisor.stop()

        # Both succeed (idempotent); the second is a no-op that returns the
        # current state rather than an error.
        assert first is True
        # Second cancel: run was already cancel-requested; returns False (no-op)
        # but does NOT raise.
        assert second is False
    finally:
        database.close()


@pytest.mark.asyncio
async def test_cancel_terminal_run_is_a_noop(tmp_path) -> None:
    """Cancelling an already-completed run does not set cancel_requested_at."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)
        # Force the run into a completed state directly.
        now = "2026-07-04T00:00:00.000Z"
        database.connection.execute(
            "UPDATE runs SET state = 'completed', ended_at = ?, updated_at = ? WHERE id = ?",
            (now, now, run.id),
        )
        database.connection.commit()

        from test_platform.services.runs import RunSupervisor

        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: _CancellableFakeEnv(),
            agent_factory=lambda lane: _SlowAgent(),
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        await supervisor.start()
        result = supervisor.request_cancel(run.id)
        await supervisor.stop()

        assert result is False
        row = database.connection.execute(
            "SELECT cancel_requested_at, state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert row["cancel_requested_at"] is None
        assert row["state"] == "completed"
    finally:
        database.close()


@pytest.mark.asyncio
async def test_normal_completion_reaches_completed_state(tmp_path) -> None:
    """A run that is not cancelled must reach 'completed', not 'cancelled'."""

    class _CompletingEnv(_CancellableFakeEnv):
        async def step(self, action: Action) -> StepResult:
            self.step_count += 1
            return StepResult(
                observation=await self.get_observation(),
                done=True,
                info={"stop_reason": ActionType.COMPLETE},
            )

    class _CompletingAgent(_SlowAgent):
        def act(self, obs: Observation) -> Action:
            self.act_count += 1
            return Action.complete("done")

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)
        from test_platform.services.runs import RunSupervisor

        env = _CompletingEnv()
        agent = _CompletingAgent()
        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: env,
            agent_factory=lambda lane: agent,
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        await supervisor.start()
        supervisor.submit(run.id)
        await asyncio.sleep(0.5)
        await supervisor.stop()

        row = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert row["state"] == "completed"
    finally:
        database.close()


# ---------------------------------------------------------------------------
# P1.2: HTTP cancel idempotency via the cancel route + idempotency_keys table
# ---------------------------------------------------------------------------


def test_cancel_route_idempotency_replays_first_response(tmp_path):
    """Repeated cancel with the same Idempotency-Key returns the first response."""
    from fastapi.testclient import TestClient

    from test_platform.api.app import create_app
    from test_platform.services.runs import FakeRunSupervisor

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)

        # FakeRunSupervisor keeps the run queued; request_cancel is a no-op on
        # it, so we need a real supervisor to set cancel_requested_at. Build one
        # without an executor (the run never executes).
        from test_platform.services.runs import RunSupervisor

        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: _CancellableFakeEnv(),
            agent_factory=lambda lane: _SlowAgent(),
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        app = create_app(settings, database=database, supervisor=supervisor)
        with TestClient(app) as client:
            key = "ci-cancel-1"
            first = client.post(
                f"/api/platform/v1/runs/{run.id}/cancel",
                headers={"Idempotency-Key": key},
            )
            second = client.post(
                f"/api/platform/v1/runs/{run.id}/cancel",
                headers={"Idempotency-Key": key},
            )

            assert first.status_code == 200
            assert second.status_code == 200
            # The replayed response is identical to the first (verbatim body).
            assert first.json() == second.json()
            assert first.json()["run_id"] == run.id

            # The idempotency row stored the response body.
            row = database.connection.execute(
                "SELECT response_json FROM idempotency_keys WHERE key = ? AND route = ?",
                (key, f"POST /api/platform/v1/runs/{run.id}/cancel"),
            ).fetchone()
            assert row is not None
            assert row["response_json"] is not None
    finally:
        database.close()


def test_cancel_idempotency_is_scoped_per_run_route(tmp_path):
    """The same Idempotency-Key reused for a different run does NOT conflict.

    Idempotency keys are scoped by (key, route), and the cancel route includes
    the run_id, so a key legitimately reused across runs is NOT a 409 — each
    run gets its own replay row. A 409 only occurs when the SAME (key, route) is
    reused with a conflicting request body, which cancel has no body for.
    """
    from fastapi.testclient import TestClient

    from test_platform.api.app import create_app
    from test_platform.services.runs import RunSupervisor

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run_a = _create_running_run(database, settings)
        import uuid

        run_b_id = uuid.uuid4().hex
        database.connection.execute(
            "INSERT INTO runs "
            "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
            " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at) "
            "SELECT ?, project_id, workflow_version_id, name, 'queued', run_plan_json, "
            " run_plan_hash, ?, 1, NULL, created_at, updated_at FROM runs WHERE id = ?",
            (run_b_id, f"runs/{run_b_id}", run_a.id),
        )
        database.connection.execute(
            "INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, created_at) "
            "SELECT ?, ?, 1, 'initial', 'queued', created_at FROM run_attempts WHERE run_id = ?",
            (uuid.uuid4().hex, run_b_id, run_a.id),
        )
        database.connection.commit()

        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: _CancellableFakeEnv(),
            agent_factory=lambda lane: _SlowAgent(),
        )
        supervisor = RunSupervisor(database, settings, executor=executor)
        app = create_app(settings, database=database, supervisor=supervisor)
        with TestClient(app) as client:
            key = "shared-key"
            first = client.post(
                f"/api/platform/v1/runs/{run_a.id}/cancel",
                headers={"Idempotency-Key": key},
            )
            second_run = client.post(
                f"/api/platform/v1/runs/{run_b_id}/cancel",
                headers={"Idempotency-Key": key},
            )
            # Both succeed: the routes differ (per-run), so no conflict.
            assert first.status_code == 200
            assert second_run.status_code == 200
            # Each run's response references its own run.
            assert first.json()["run_id"] == run_a.id
            assert second_run.json()["run_id"] == run_b_id
    finally:
        database.close()


@pytest.mark.asyncio
async def test_pre_execution_cancel_omits_run_started(tmp_path) -> None:
    """Cancelling before execution must not emit a misleading run.started.

    The event stream should show run.cancelled only, with NO run.started (the
    executor's queued-cancel check fires before it emits started).
    """
    import asyncio as _a
    from test_platform.services.runs import RunSupervisor as _RS

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_running_run(database, settings)

        # Pre-set cancel_requested_at BEFORE submitting, so the executor's
        # queued-cancel check fires immediately on execute_run entry.
        database.connection.execute(
            "UPDATE runs SET cancel_requested_at = ?, updated_at = ? WHERE id = ?",
            ("2026-07-04T00:00:00.000Z", "2026-07-04T00:00:00.000Z", run.id),
        )
        database.connection.commit()

        env = _CancellableFakeEnv()
        agent = _SlowAgent()
        executor = SerialRunExecutor(
            database,
            settings,
            task_factory=_FakeTaskFactory(),
            env_factory=lambda lane: env,
            agent_factory=lambda lane: agent,
        )
        supervisor = _RS(database, settings, executor=executor)
        await supervisor.start()
        sub = await supervisor._broker.subscribe(run.id)
        supervisor.submit(run.id)

        received: list[str] = []
        try:
            while True:
                ev = await _a.wait_for(sub.queue.get(), timeout=1.0)
                if ev is None:
                    break
                received.append(ev.type)
                if ev.type == "run.cancelled":
                    break
        except _a.TimeoutError:
            pass
        await supervisor.stop()

        assert "run.started" not in received, received
        assert "run.cancelled" in received
    finally:
        database.close()
