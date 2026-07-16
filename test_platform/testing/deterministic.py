from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Callable

from bench_env.env.base import Action, ActionType, Observation, StepResult
from bench_env.task.judge import JudgeResult
from PIL import Image, ImageDraw

from test_platform.adapters.targets import SimulatorAdapter, TargetAdapterRegistry
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.services.execution import SerialRunExecutor


def build_deterministic_executor_resolver(
    database: Database,
    settings: PlatformSettings,
    *,
    enabled: bool,
) -> Callable[[Any], Any]:
    """Return the test adapter only after an explicit opt-in at composition."""

    if not enabled:
        raise RuntimeError(
            "The deterministic adapter requires explicit test composition."
        )

    def resolve(lane: Any) -> Any:
        _deterministic_subject(lane)
        return SerialRunExecutor(
            database,
            settings,
            task_factory=DeterministicTaskFactory(),
            env_factory=lambda resolved_lane: DeterministicEnvironment(
                slow=_deterministic_subject(resolved_lane).slow,
            ),
            agent_factory=_deterministic_agent,
        )

    return resolve


@dataclass(frozen=True)
class _DeterministicSubject:
    slow: bool
    fail_all: bool


def _deterministic_subject(lane: Any) -> _DeterministicSubject:
    runner_config = getattr(lane, "runner_config", {}) or {}
    agent_name = str(runner_config.get("agent") or "")
    model_name = str(runner_config.get("model_name") or "")
    if agent_name in {"deterministic", "deterministic-slow"}:
        return _DeterministicSubject(
            slow=agent_name == "deterministic-slow",
            fail_all=False,
        )
    if agent_name == "generic_v2" and model_name in {
        "deterministic-profile-pass",
        "deterministic-profile-fail",
    }:
        return _DeterministicSubject(
            slow=False,
            fail_all=model_name == "deterministic-profile-fail",
        )
    raise RuntimeError(
        "Explicit deterministic composition only accepts deterministic subjects."
    )


def _deterministic_agent(lane: Any) -> "DeterministicAgent":
    subject = _deterministic_subject(lane)
    return DeterministicAgent(
        slow=subject.slow,
        failing_task_id=str(
            getattr(lane, "runner_config", {}).get("failing_task_id") or ""
        ) or None,
        fail_all=subject.fail_all,
    )


def build_deterministic_target_registry() -> TargetAdapterRegistry:
    return TargetAdapterRegistry(
        simulator_adapter=SimulatorAdapter(metadata_probe=_DeterministicMetadataProbe())
    )


class _DeterministicMetadataProbe:
    def read_metadata(self, env_url: str, timeout_seconds: float) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "simulator": {
                "product": "mobile-gym-deterministic",
                "version": "1.0.0",
                "buildId": "tp-h06-test-only",
            },
            "apps": [
                {
                    "id": "fake",
                    "packageName": "dev.mobilegym.fake",
                    "displayName": "Deterministic Fake",
                    "version": "1.0.0",
                    "versionCode": 1,
                    "type": "test",
                }
            ],
            "data": {"revision": "tp-h06-data-v1"},
            "capabilities": ["deterministic-test"],
        }


class _Stopwatch:
    total = 0.0

    def reset(self) -> None:
        return None

    def phase(self, _name: str):
        return nullcontext()

    def record(self, _name: str, _value: float) -> None:
        return None

    def to_flat(self) -> dict[str, float]:
        return {}

    def to_tree(self) -> list[dict[str, Any]]:
        return []

    def summary(self) -> str:
        return "deterministic"


class DeterministicEnvironment:
    supports_state_injection = True

    def __init__(self, *, slow: bool = False) -> None:
        self.slow = slow
        self.stopwatch = _Stopwatch()
        self.step_count = 0
        self.closed = False
        self.marker = "clean"
        self._agent_message: str | None = None

    def reset_episode(self) -> None:
        """Reset per-episode mutable state.

        ``PairedSerialRunExecutor`` reuses one environment across episodes, so
        the marker / step counter / agent message must be cleared at the
        episode boundary (called from ``DeterministicTask.setup``, before the
        executor's integrity ``get_state`` check). ``SerialRunExecutor`` builds
        a fresh environment per episode, where this is a no-op.
        """
        self.marker = "clean"
        self.step_count = 0
        self._agent_message = None

    async def get_state(self, required_apps: list[str] | None = None) -> dict[str, Any]:
        return {
            "apps": {"fake": {"marker": self.marker}},
            "os": {"time": {"mode": "fixed", "value": "2026-07-11T00:00:00Z"}},
        }

    async def get_observation(self) -> Observation:
        return Observation(
            screenshot_bytes=_screenshot_bytes(self.step_count, self.marker),
            route={"app": "fake", "path": f"/step/{self.step_count}"},
            state=await self.get_state(),
            step_idx=self.step_count,
        )

    async def step(self, action: Action) -> StepResult:
        self.step_count += 1
        await asyncio.sleep(0.1 if self.slow else 0.5)
        if action.action_type == ActionType.COMPLETE:
            message = str(action.data.get("return") or "")
            self._agent_message = message
            self.marker = (
                "deterministic_failure"
                if message == "deterministic_failure"
                else "mutated"
            )
            return StepResult(
                observation=await self.get_observation(),
                done=True,
                info={"stop_reason": ActionType.COMPLETE},
            )
        return StepResult(
            observation=await self.get_observation(),
            done=False,
            info={"stop_reason": action.action_type},
        )

    async def close(self) -> None:
        self.closed = True

    @property
    def agent_message(self) -> str | None:
        return self._agent_message

    @property
    def agent_answer(self) -> None:
        return None


class DeterministicAgent:
    name = "deterministic-test-agent"

    def __init__(
        self,
        *,
        slow: bool = False,
        failing_task_id: str | None = None,
        fail_all: bool = False,
    ) -> None:
        self.slow = slow
        self.failing_task_id = failing_task_id
        self.fail_all = fail_all
        self.history: list[Any] = []
        self._instruction: str = ""
        self._current_task_id: str = ""

    def reset(self, instruction: str) -> None:
        self._instruction = instruction
        # Instruction format: "Deterministic sequence step N: {task_base_id}".
        self._current_task_id = instruction.rsplit(": ", 1)[-1] if instruction else ""
        self.history = [{"instruction": instruction}]

    def act(self, observation: Observation) -> Action:
        if self.slow:
            return Action(ActionType.CLICK, {"point": [10, 10]})
        if self._should_fail():
            return Action.complete("deterministic_failure")
        return Action.complete("deterministic completion")

    def _should_fail(self) -> bool:
        if self.fail_all:
            return True
        if self.failing_task_id is None:
            # TP-H06 single-lane Manual Sequence: fail the second step.
            return "sequence step 2" in self._instruction
        # TP-H07 paired candidate: fail the lane's nominated task.
        return self._current_task_id == self.failing_task_id

    def reset_history(self) -> None:
        self.history.clear()


class DeterministicTaskFactory:
    def instantiate(self, template: Any, params: dict[str, Any] | None = None) -> Any:
        return DeterministicTask(
            task_base_id=str(template.task_base_id),
            sequence_index=getattr(template, "sequence_index", None),
            instance_seed=getattr(template, "instance_seed", None),
            params=params or {},
        )


class DeterministicTask:
    apps = ["fake"]
    answer_fields = None
    suite = "deterministic"

    def __init__(
        self,
        *,
        task_base_id: str,
        sequence_index: int | None,
        instance_seed: int | None,
        params: dict[str, Any],
    ) -> None:
        self.task_base_id = task_base_id
        self.sequence_index = sequence_index
        self._seed = instance_seed
        self.params = dict(params)
        self._user_params = set(params)
        self.initial_marker: str | None = None

    @property
    def id(self) -> str:
        return self.task_base_id

    @property
    def description(self) -> str:
        step = 1 if self.sequence_index is None else self.sequence_index + 1
        return f"Deterministic sequence step {step}: {self.task_base_id}"

    async def setup(self, env: DeterministicEnvironment) -> Observation:
        env.reset_episode()
        observation = await env.get_observation()
        self.initial_marker = str(
            observation.state.get("apps", {}).get("fake", {}).get("marker")
        )
        return observation

    def teardown(self, env: DeterministicEnvironment) -> None:
        return None

    def evaluate(self, judge_input: Any) -> JudgeResult:
        if self.initial_marker != "clean":
            return JudgeResult.error("deterministic isolation violation")
        final_marker = str(
            judge_input.apps.get("fake", {}).get("marker")
        )
        if final_marker == "deterministic_failure":
            return JudgeResult.fail("deterministic failure at sequence step 2")
        return JudgeResult.ok()


def _screenshot_bytes(step: int, marker: str) -> bytes:
    image = Image.new("RGB", (160, 320), color=(24, 32, 48))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 32, 144, 288), outline=(94, 234, 212), width=4)
    draw.text((28, 120), f"step {step}", fill=(255, 255, 255))
    draw.text((28, 148), marker, fill=(255, 196, 96))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()
