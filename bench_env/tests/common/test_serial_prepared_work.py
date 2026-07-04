from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from bench_env.config import RunnerConfig
from bench_env.runner.events import NullEventSink
from bench_env.runner.serial import PreparedWorkItem, SerialRunner


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
    goal_mismatches: list[dict[str, Any]] | None = None
    unexpected_changes: list[dict[str, Any]] | None = None


class _Recorder:
    run_dir = Path("fake-run")

    def finish_run(self, *, repeat_n: int, pass_k: list[int] | None) -> Path:
        return self.run_dir


class _Env:
    async def close(self) -> None:
        return None


class _ProbeSerialRunner(SerialRunner):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls: list[dict[str, Any]] = []

    async def run_episode(
        self,
        env: Any,
        agent: Any,
        task: Any,
        max_steps: int,
        recorder: Any,
        *,
        trial_id: int = 0,
        evaluator: Any = None,
        loop_threshold: int = 0,
        cancellation_token: Any = None,
        event_sink: Any = None,
    ) -> _Result:
        self.calls.append(
            {
                "task": task,
                "trial_id": trial_id,
                "max_steps": max_steps,
                "params": dict(task.params),
            }
        )
        return _Result(task_id=task.id, trial_id=trial_id, max_steps=max_steps)

    def print_summary(self, results: list[_Result], run_dir: Path | None = None) -> None:
        return None


def _config(**overrides: Any) -> RunnerConfig:
    return RunnerConfig(
        agent="probe",
        model_name="probe-model",
        quiet=True,
        **overrides,
    )


@pytest.mark.asyncio
async def test_null_event_sink_preserves_existing_serial_results() -> None:
    runner = _ProbeSerialRunner(
        _Env(),
        object(),
        [_Task(_seed=123)],
        _config(repeat_n=2),
        recorder=_Recorder(),
        event_sink=NullEventSink(),
    )

    results = await runner.run()

    assert [(result.task_id, result.trial_id, result.max_steps) for result in results] == [
        ("fake.Sample", 0, 15),
        ("fake.Sample", 1, 15),
    ]
    assert [call["params"] for call in runner.calls] == [
        {"value": "sampled"},
        {"value": "sampled"},
    ]


@pytest.mark.asyncio
async def test_prepared_work_items_execute_once_with_supplied_trial_and_max_steps() -> None:
    prepared_task = _Task(_seed=999, value="prepared")
    runner = _ProbeSerialRunner(
        _Env(),
        object(),
        [_Task(_seed=1), _Task(_seed=2)],
        _config(repeat_n=5),
        recorder=_Recorder(),
        prepared_work_items=[
            PreparedWorkItem(
                episode_key="fake.Sample|i0|s999|r1|t7",
                task=prepared_task,
                trial_id=7,
                max_steps=45,
            )
        ],
    )

    results = await runner.run()

    assert [(result.task_id, result.trial_id, result.max_steps) for result in results] == [
        ("fake.Sample", 7, 45)
    ]
    assert runner.calls == [
        {
            "task": prepared_task,
            "trial_id": 7,
            "max_steps": 45,
            "params": {"value": "prepared"},
        }
    ]
