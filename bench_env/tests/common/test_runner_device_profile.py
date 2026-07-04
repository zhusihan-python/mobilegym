"""VS-07 Block A: RunnerConfig viewport/DPR fields + propagation to EnvPool.

The platform carries the full target device profile in PlannedLane.runner_config;
RunnerConfig must surface viewport_size/device_scale_factor so the profile flows
into MobileGymEnv (serial) and EnvPool (parallel) instead of being dropped or
hardcoded.
"""
from __future__ import annotations

import pytest

from bench_env.config import RunnerConfig


def test_runner_config_has_viewport_and_dpr_defaults():
    """New fields exist with Pixel-7-equivalent defaults (CLI parity)."""
    config = RunnerConfig(agent="x", model_name="m")
    assert config.viewport_size == (360, 800)
    assert config.device_scale_factor == 3.0


def test_old_meta_loads_default_viewport_and_dpr():
    """Old meta.json (pre-VS-07) has no viewport_size/device_scale_factor; from_meta
    must tolerate their absence and fall back to defaults."""
    old_meta = {
        "agent": "x",
        "model_name": "m",
        "device": "sim",
        # No viewport_size / device_scale_factor
    }
    config = RunnerConfig.from_meta(old_meta)
    assert config.viewport_size == (360, 800)
    assert config.device_scale_factor == 3.0


def test_from_meta_coerces_viewport_size_list_to_tuple():
    """meta.json stores tuples as JSON lists; from_meta must coerce back."""
    meta = {
        "agent": "x",
        "model_name": "m",
        "viewport_size": [393, 852],
        "device_scale_factor": 2.75,
        "physical_size": [1080, 2400],
    }
    config = RunnerConfig.from_meta(meta)
    assert config.viewport_size == (393, 852)
    assert isinstance(config.viewport_size, tuple)
    assert config.device_scale_factor == 2.75
    assert config.physical_size == (1080, 2400)


def test_to_dict_includes_viewport_and_dpr():
    """to_dict (used for meta.json + provenance) must persist the new fields."""
    config = RunnerConfig(
        agent="x", model_name="m", viewport_size=(393, 852), device_scale_factor=2.75,
    )
    d = config.to_dict()
    assert d["viewport_size"] == (393, 852)
    assert d["device_scale_factor"] == 2.75


def test_parallel_from_config_propagates_profile_to_env_pool(monkeypatch):
    """ParallelRunner.from_config must pass the target profile to EnvPool, not
    rely on EnvPool defaults. We capture the EnvPool kwargs."""
    captured: dict = {}

    class _FakeEnvPool:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.n = kwargs.get("n", 1)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    # Stub the factory pieces from_config calls.
    from bench_env import factory

    monkeypatch.setattr(factory, "load_tasks", lambda config: [])
    monkeypatch.setattr(factory, "create_recorder", lambda config: _FakeRecorder())
    monkeypatch.setattr(factory, "create_llm", lambda config: None)
    monkeypatch.setattr(factory, "create_agent", lambda config, llm=None: object())
    monkeypatch.setattr(factory, "create_evaluator", lambda config, default_llm=None: None)
    monkeypatch.setattr(factory, "get_agent_name", lambda config: "probe")

    import bench_env.env as env_mod

    monkeypatch.setattr(env_mod, "EnvPool", _FakeEnvPool)

    from bench_env.runner.parallel import ParallelRunner

    config = RunnerConfig(
        agent="probe", model_name="probe-model", quiet=True,
        parallel=2, env_url="http://localhost:5173",
        viewport_size=(393, 852), device_scale_factor=2.75,
        physical_size=(1080, 2400),
    )

    import asyncio

    runner = asyncio.run(ParallelRunner.from_config(config))
    assert captured["viewport_size"] == (393, 852)
    assert captured["device_scale_factor"] == 2.75
    assert captured["physical_size"] == (1080, 2400)


class _FakeRecorder:
    run_dir = None

    def start_run(self, **kwargs):
        return None

    def finish_run(self, **kwargs):
        return None
