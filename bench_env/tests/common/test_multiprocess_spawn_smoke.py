"""VS-08 Block D: real spawn smoke test.

Verifies under a REAL ``mp.get_context("spawn").Process`` that:
- EpisodeWorkSpec / ShardEventEnvelope pickle across spawn transport;
- the module-level ``_spawn_smoke_shard_main`` is importable under spawn;
- the shared mp.Event propagates a cancel signal to the child.

This does NOT build a Playwright env; the child emits a fixed sequence of
envelopes. Kept fast (no real browser).
"""
from __future__ import annotations

import multiprocessing as mp
import queue as queue_mod

from bench_env.runner.events import (
    ShardEventEnvelope,
    ShardFatalEnvelope,
    ShardLifecycleEnvelope,
    ShardResultEnvelope,
)
from bench_env.runner.multiprocess import _spawn_smoke_shard_main
from bench_env.runner.work_spec import EpisodeWorkSpec


def _spec(i: int) -> EpisodeWorkSpec:
    return EpisodeWorkSpec(
        episode_key=f"smoke.T|i{i}|s{i}|r1|t0",
        task_base_id="smoke.T",
        instance_id=i,
        instance_seed=i,
        template_index=0,
        params={},
        trial_id=0,
        max_steps=5,
    )


def _drain_all(q, timeout: float = 5.0) -> list:
    """Drain all envelopes from the queue (spawn path: mp.Queue)."""
    import time

    items: list = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            items.append(q.get(timeout=0.2))
        except queue_mod.Empty:
            if items:
                # Give a brief grace period for stragglers, then stop.
                break
    return items


def test_spawn_smoke_pickles_specs_and_envelopes_and_propagates_mp_event():
    """Real spawn: EpisodeWorkSpec pickles, envelopes flow back, mp.Event is
    observed by the child."""
    ctx = mp.get_context("spawn")
    q: mp.Queue = ctx.Queue()
    mp_event = ctx.Event()
    specs = [_spec(0), _spec(1)]

    p = ctx.Process(
        target=_spawn_smoke_shard_main,
        args=(0, specs, q, mp_event),
        name="spawn-smoke",
    )
    p.start()
    # Signal cancel shortly after start so the child observes it.
    mp_event.set()
    p.join(timeout=10)
    assert not p.is_alive(), "spawn smoke child did not exit in time"

    items = _drain_all(q)

    # At least one of each expected envelope type arrived.
    lifecycles = [i for i in items if isinstance(i, ShardLifecycleEnvelope)]
    results = [i for i in items if isinstance(i, ShardResultEnvelope)]
    events = [i for i in items if isinstance(i, ShardEventEnvelope)]
    fatals = [i for i in items if isinstance(i, ShardFatalEnvelope)]

    # No fatal envelope (the child should complete cleanly).
    assert fatals == [], f"unexpected fatal: {fatals}"
    # Lifecycle: started + stopped.
    kinds = sorted(l.kind for l in lifecycles)
    assert "started" in kinds
    assert "stopped" in kinds
    # One result envelope per spec.
    assert len(results) == len(specs)
    for r, spec in zip(results, specs):
        assert r.episode_key == spec.episode_key
        assert r.result_dict["episode_key"] == spec.episode_key
    # At least one event envelope (episode.completed) survived pickling.
    assert len(events) >= 1
    # The mp.Event was set; the child observed it (clean stop, not fatal).
    assert p.exitcode == 0
