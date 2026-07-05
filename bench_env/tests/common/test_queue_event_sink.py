"""VS-08 P2: QueueEventSink bounded/coalesce contract.

The sink wraps a bounded queue. Critical events (episode.completed/error,
worker.started/stopped, fatal, result, lifecycle) must NEVER be dropped — on a
full queue they block (backpressure). Step/metric events are coalesced (dropped
+ counted). This test uses a real ``queue.Queue(maxsize=1)`` so the bounded
behavior is exercised, not just the unbounded in-process asyncio.Queue.

This contract applies to the cross-process mp.Queue path; the in-process
asyncio.Queue is intentionally unbounded (single-process, critical events are
synchronous). This test pins the sink's policy regardless of queue flavor.
"""
from __future__ import annotations

import queue as queue_mod
import threading
from typing import Any

import pytest

from bench_env.runner.events import ExecutionEvent
from bench_env.runner.multiprocess import QueueEventSink


def _evt(type: str, **payload: Any) -> ExecutionEvent:
    return ExecutionEvent(type=type, timestamp="", worker_id="W0", payload=payload)


def test_step_event_coalesced_when_queue_full():
    """A full queue + step event → dropped + coalesce counter incremented (not blocked)."""
    q: queue_mod.Queue = queue_mod.Queue(maxsize=1)
    sink = QueueEventSink(q, rank=0)
    # Fill the queue.
    q.put_nowait("blocker")
    # Step event should be coalesced, not block.
    sink.emit(_evt("episode.step_recorded", step=1))
    sink.emit(_evt("episode.step_recorded", step=2))
    assert sink.coalesced == 2
    # Queue still holds only the original blocker.
    assert q.qsize() == 1


def test_critical_event_backpressures_when_queue_full():
    """A full queue + critical event → blocking put (backpressure), NOT dropped.

    The drainer removes exactly ONE item (the blocker) to free space; the
    critical emit's blocking put then completes and the envelope remains on the
    queue for the main thread to verify.
    """
    q: queue_mod.Queue = queue_mod.Queue(maxsize=1)
    sink = QueueEventSink(q, rank=0)
    q.put_nowait("blocker")

    def free_one():
        import time

        time.sleep(0.1)  # let the critical emit block first
        q.get_nowait()  # free exactly one slot

    t = threading.Thread(target=free_one)
    t.start()
    # Critical event → must block until the drainer frees space.
    sink.emit(_evt("episode.completed", outcome="PASS"))
    t.join(timeout=2.0)

    assert sink.coalesced == 0  # critical was NOT coalesced
    # The critical envelope is now on the queue.
    from bench_env.runner.events import ShardEventEnvelope

    envelope = q.get_nowait()
    assert isinstance(envelope, ShardEventEnvelope)
    assert envelope.event.type == "episode.completed"


def test_critical_event_passes_when_queue_has_space():
    """Normal case: critical event lands on the queue without coalescing."""
    q: queue_mod.Queue = queue_mod.Queue(maxsize=10)
    sink = QueueEventSink(q, rank=2)
    sink.emit(_evt("worker.started"))
    sink.emit(_evt("episode.completed", outcome="PASS"))
    assert sink.coalesced == 0
    assert q.qsize() == 2


def test_non_critical_non_step_event_also_coalesced_when_full():
    """Any non-critical event (e.g. metric.sample) is coalesced on a full queue."""
    q: queue_mod.Queue = queue_mod.Queue(maxsize=1)
    sink = QueueEventSink(q, rank=0)
    q.put_nowait("blocker")
    sink.emit(_evt("metric.sample", domain="host"))
    assert sink.coalesced == 1
