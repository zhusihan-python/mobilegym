"""VS-09 Block B: prepared task re-instantiation integrity.

When a prepared task is re-instantiated in each lane (with frozen params), the
instruction must match the prepared instruction. A mismatch indicates the task
factory is not honoring the frozen params and becomes a PAIRING_VIOLATION at
classification time.
"""
from __future__ import annotations

from typing import Any

from test_platform.domain.run_plans import EpisodeTemplate
from test_platform.services.execution import PreparedTaskInstance, RegistryTaskFactory


class _DeterministicTask:
    apps = ["fake"]
    answer_fields = None

    def __init__(self, _seed: int | None = None, **params: Any) -> None:
        self._seed = _seed
        self.params = {"choice": "default", **params}

    @property
    def id(self) -> str:
        return "fake.SampleTask"

    @property
    def description(self) -> str:
        return f"Choose {self.params['choice']}"


class _FrozenFactory(RegistryTaskFactory):
    """A task factory that uses _DeterministicTask instead of the registry."""

    def instantiate(
        self,
        template: EpisodeTemplate,
        params: dict[str, Any] | None = None,
    ) -> Any:
        task = _DeterministicTask(_seed=template.instance_seed, **(params or {}))
        task._instance_id = template.instance_id
        task._template_index = template.template_index
        return task


def _template() -> EpisodeTemplate:
    return EpisodeTemplate(
        episode_key="fake.SampleTask|i0|s100|r1|t0",
        materialization_key="fake.SampleTask|i0|s100|r1",
        pair_key="fake.SampleTask|i0|s100|r1|t0",
        task_base_id="fake.SampleTask",
        task_id="fake.SampleTask",
        instance_id=0,
        instance_seed=100,
        template_index=None,
        trial_id=0,
        max_steps=15,
    )


def _prepared(**overrides: Any) -> PreparedTaskInstance:
    base = dict(
        schema_version=1,
        materialization_key="fake.SampleTask|i0|s100|r1",
        task_base_id="fake.SampleTask",
        instance_id=0,
        instance_seed=100,
        template_index=None,
        params={"choice": "frozen-value"},
        instruction="Choose frozen-value",
        source_target_revision_id="rev-1",
        initial_state_relative_path="platform/initial-states/x.json",
        initial_state_hash="sha256:raw",
        projection_hash="sha256:proj",
        data_revision="seed-v1",
        scenario_hash="sha256:scenario",
        fingerprint="sha256:fp",
    )
    base.update(overrides)
    return PreparedTaskInstance(**base)


def test_reinstantiated_task_uses_frozen_params():
    """Re-instantiating with the prepared params reproduces the task state."""
    prepared = _prepared()
    factory = _FrozenFactory()
    task = factory.instantiate(_template(), params=prepared.params)
    assert task.params["choice"] == "frozen-value"
    assert task.description == prepared.instruction


def test_reinstantiated_instruction_matches_prepared_instruction():
    """The core VS-09 invariant: re-instantiated instruction == prepared.instruction.

    A mismatch here is what produces an INSTRUCTION_MISMATCH → PAIRING_VIOLATION
    classification at comparison time.
    """
    prepared = _prepared()
    factory = _FrozenFactory()
    task = factory.instantiate(_template(), params=prepared.params)
    assert str(task.description) == prepared.instruction


def test_instruction_mismatch_detection():
    """If the factory ignores params, the instruction diverges — detectable."""
    prepared = _prepared(params={"choice": "frozen-value"}, instruction="Choose frozen-value")
    factory = _FrozenFactory()
    # Instantiate WITHOUT the frozen params (simulating a misbehaving factory).
    task = factory.instantiate(_template(), params=None)
    # The default description differs from the prepared instruction.
    assert str(task.description) != prepared.instruction
    # This is exactly the check the paired executor performs to flag
    # INSTRUCTION_MISMATCH.
