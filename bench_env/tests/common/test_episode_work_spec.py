"""VS-08 Block A: EpisodeWorkSpec is pickle-safe and reconstructs tasks correctly."""
from __future__ import annotations

import pickle
from typing import Any

import pytest

from bench_env.runner.work_spec import EpisodeWorkSpec, reconstruct_task, spec_is_picklable


class _FakeTask:
    """Minimal task mirroring BaseTask's constructor + identity attributes."""
    suite = "fake"
    apps: list[str] = []

    def __init__(self, _seed: int | None = None, **params: Any) -> None:
        self._seed = _seed
        self.params = {"value": "default", **params}
        self._user_params = set(params.keys())
        self._instance_id = -1
        self._template_index = None

    @property
    def id(self) -> str:
        return "fake.Sample"


class _FakeRegistry:
    def get_by_id(self, task_id: str):
        assert task_id == "fake.Sample"
        return _FakeTask


def _sample_spec(**overrides: Any) -> EpisodeWorkSpec:
    base = dict(
        episode_key="fake.Sample|i0|s42|r1|t0",
        task_base_id="fake.Sample",
        instance_id=0,
        instance_seed=42,
        template_index=1,
        params={"choice": "prepared", "count": 3},
        trial_id=0,
        max_steps=30,
    )
    base.update(overrides)
    return EpisodeWorkSpec(**base)


def test_episode_work_spec_is_picklable():
    """Spec must survive pickle round-trip (spawn transport)."""
    spec = _sample_spec()
    assert spec_is_picklable(spec)
    restored = pickle.loads(pickle.dumps(spec))
    assert restored == spec


def test_episode_work_spec_with_none_template_index_picklable():
    spec = _sample_spec(template_index=None)
    assert spec_is_picklable(spec)


def test_reconstruct_task_restores_seed_params_and_identity():
    """reconstruct_task mirrors RegistryTaskFactory.instantiate: seed + explicit
    params (so sampling can't overwrite them) + _instance_id + _template_index."""
    spec = _sample_spec()
    task = reconstruct_task(spec, _FakeRegistry())

    assert task._seed == 42
    # params merge the spec's explicit values over the task's defaults.
    assert task.params["choice"] == "prepared"
    assert task.params["count"] == 3
    assert task._instance_id == 0
    assert task._template_index == 1
    # Explicit params are marked as user params (sampler cannot overwrite).
    assert task._user_params == {"choice", "count"}


def test_reconstruct_task_unknown_id_raises():
    """An unknown task_base_id surfaces the registry's error (no silent skip)."""
    spec = _sample_spec(task_base_id="does.not.exist")
    with pytest.raises(Exception):
        reconstruct_task(spec, _FakeRegistry())
