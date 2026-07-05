"""VS-08 Block B: shard envelopes are pickle-safe + EpisodeResult.to_dict carries episode_key."""
from __future__ import annotations

import pickle
from typing import Any

import pytest

from bench_env.runner.base import EpisodeResult, ExecutionResult
from bench_env.runner.events import (
    ExecutionEvent,
    ShardEventEnvelope,
    ShardFatalEnvelope,
    ShardLifecycleEnvelope,
    ShardResultEnvelope,
)


def _evt(**kw: Any) -> ExecutionEvent:
    base = dict(type="episode.started", timestamp="", worker_id="W0")
    base.update(kw)
    return ExecutionEvent(**base)


def test_shard_event_envelope_picklable():
    env = ShardEventEnvelope(rank=2, event=_evt(episode_key="k1"))
    restored = pickle.loads(pickle.dumps(env))
    assert restored == env
    assert restored.rank == 2
    assert restored.event.episode_key == "k1"


def test_shard_result_envelope_picklable():
    env = ShardResultEnvelope(rank=0, episode_key="k0", result_dict={"id": "t", "is_success": True})
    restored = pickle.loads(pickle.dumps(env))
    assert restored == env
    assert restored.result_dict["is_success"] is True


def test_shard_fatal_envelope_picklable():
    env = ShardFatalEnvelope(rank=1, exitcode=137, error="OOMKilled")
    restored = pickle.loads(pickle.dumps(env))
    assert restored == env
    assert restored.exitcode == 137


def test_shard_lifecycle_envelope_picklable():
    env = ShardLifecycleEnvelope(rank=3, kind="stopped", exitcode=0)
    restored = pickle.loads(pickle.dumps(env))
    assert restored == env
    assert restored.kind == "stopped"
    assert restored.exitcode == 0


def test_episode_result_to_dict_includes_episode_key_when_set():
    """VS-08: result dicts must carry episode_key so multi-process reconciliation
    can join by key without a live EpisodeResult object."""
    result = EpisodeResult(
        task_id="fake.T", task_name="T", suite="fake",
        execution=ExecutionResult(steps=1, trace=[], runtime_s=0.1, finished=True, truncated=False),
        trial_id=0, apps=["fake"], max_steps=10,
        episode_key="fake.T|i0|s1|r1|t0",
    )
    d = result.to_dict()
    assert d["episode_key"] == "fake.T|i0|s1|r1|t0"


def test_episode_result_to_dict_omits_episode_key_when_none():
    """CLI parity: when episode_key is None (CLI runs), to_dict omits it rather
    than emitting a null key."""
    result = EpisodeResult(
        task_id="fake.T", task_name="T", suite="fake",
        execution=ExecutionResult(steps=1, trace=[], runtime_s=0.1, finished=True, truncated=False),
        trial_id=0, apps=["fake"], max_steps=10,
        episode_key=None,
    )
    d = result.to_dict()
    assert "episode_key" not in d
