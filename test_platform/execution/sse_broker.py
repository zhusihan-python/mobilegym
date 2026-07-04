from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

from test_platform.domain.events import PersistedEvent

logger = logging.getLogger(__name__)

# A subscriber whose queue fills up is forcibly disconnected: it receives a
# `stream.reset_required` sentinel and is then removed. We never block `publish`
# on a slow client (the database remains the source of truth for replay).
_SUBSCRIBER_QUEUE_SIZE = 64


class Subscription:
    """A single SSE subscriber's bounded queue + control handle."""

    __slots__ = ("queue", "closed")

    def __init__(self) -> None:
        self.queue: asyncio.Queue[PersistedEvent | None] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_SIZE
        )
        # When True the subscriber must stop reading, refetch a snapshot via REST
        # and reconnect. Set when its queue overflowed or when it unsubscribed.
        self.closed = False


class SSEBroker:
    """In-memory fan-out of persisted events to SSE subscribers.

    The broker is NOT authoritative — SQLite (events table) is. The broker only
    bridges committed events to live HTTP streams; on reconnect or overflow the
    client replays from REST using `Last-Event-ID` / `after`.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[Subscription]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Capture the running loop so publish() is safe from worker threads."""
        self._loop = loop

    async def subscribe(self, run_id: str) -> Subscription:
        subscription = Subscription()
        async with self._lock:
            self._subscribers[run_id].add(subscription)
        return subscription

    async def unsubscribe(self, run_id: str, subscription: Subscription) -> None:
        async with self._lock:
            self._subscribers[run_id].discard(subscription)
            subscription.closed = True
            # Wake a pending get() so the stream loop exits promptly.
            try:
                subscription.queue.put_nowait(None)
            except asyncio.QueueFull:
                pass

    def publish(self, event: PersistedEvent) -> None:
        """Fan out a committed event. Thread-safe via loop binding.

        Called from the event writer (possibly a worker thread). If the loop is
        bound, the actual fan-out is scheduled onto it; otherwise it is a no-op
        (no live subscribers can exist without a running loop).
        """
        if self._loop is None:
            return
        # call_soon_threadsafe is safe from any thread, including the loop thread.
        self._loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: PersistedEvent) -> None:
        subscribers = self._subscribers.get(event.run_id)
        if not subscribers:
            return
        for subscription in list(subscribers):
            if subscription.closed:
                continue
            try:
                subscription.queue.put_nowait(event)
            except asyncio.QueueFull:
                # Slow subscriber: disconnect it. The client will refetch a
                # snapshot and reconnect via REST.
                subscription.closed = True
                logger.warning(
                    "SSE queue full for run %s; sending reset_required",
                    event.run_id,
                )
