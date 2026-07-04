"""Adapter that bridges bench_env's `EventSink` to the platform `EventWriter`.

bench_env runners emit low-level `ExecutionEvent`s (no run_id / sequence). The
platform persists events via `EventWriter` (which assigns run_id-aware sequence
numbers). This adapter wraps an `EventWriter` so a runner's `event_sink` hook
flows into the platform event table and SSE stream.

The adapter is non-throwing: a failure to persist one event never propagates
into the runner (the EventWriter already enforces this, but the adapter guards
its own mapping logic too).
"""
from __future__ import annotations

import logging
from typing import Any

from bench_env.runner.events import ExecutionEvent

logger = logging.getLogger(__name__)


class PlatformRunnerEventSink:
    """Adapt bench_env ExecutionEvent -> platform EventWriter.emit.

    Construct per run/lane attempt so episode/step events carry the right
    identity columns.
    """

    def __init__(
        self,
        writer: Any,
        *,
        run_id: str,
        run_attempt_id: str | None = None,
        lane_id: str | None = None,
        lane_attempt_id: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._writer = writer
        self._run_id = run_id
        self._run_attempt_id = run_attempt_id
        self._lane_id = lane_id
        self._lane_attempt_id = lane_attempt_id
        self._default_worker_id = worker_id

    def emit(self, event: ExecutionEvent) -> None:
        try:
            # Map bench_env's ExecutionEvent type namespace to platform event
            # types. The runner emits types like "episode.started",
            # "episode.step_recorded", "episode.completed", "episode.cancelled".
            payload = dict(event.payload)
            payload.setdefault("task_id", event.task_id) if event.task_id else None
            self._writer.emit(
                self._run_id,
                event.type,
                payload,
                run_attempt_id=self._run_attempt_id,
                lane_id=self._lane_id,
                lane_attempt_id=self._lane_attempt_id,
                worker_id=event.worker_id or self._default_worker_id,
                entity_type="episode" if event.type.startswith("episode.") else "run",
            )
        except Exception:  # noqa: BLE001 — never propagate into the runner
            logger.debug("PlatformRunnerEventSink dropped event %s", event.type, exc_info=True)
