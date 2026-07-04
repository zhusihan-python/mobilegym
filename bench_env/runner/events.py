from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ExecutionEvent:
    type: str
    timestamp: str
    phase: str | None = None
    worker_id: str | None = None
    task_id: str | None = None
    trial_id: int | None = None
    episode_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    def emit(self, event: ExecutionEvent) -> None:
        ...


class NullEventSink:
    def emit(self, event: ExecutionEvent) -> None:
        return None
