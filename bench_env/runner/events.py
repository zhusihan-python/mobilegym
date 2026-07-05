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


# ---------------------------------------------------------------------------
# VS-08: shard event envelopes (child → parent multiprocessing queue transport)
#
# These frozen dataclasses are all-primitive (pickle-safe for spawn). They form
# a discriminated union flowing child→parent over the shared mp.Queue. The
# parent drains and forwards/collects them. Worker IDs are normalized on the
# parent side (W0 → p{rank:02d}-W0) so multi-shard workers never collide.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShardEventEnvelope:
    """A live ExecutionEvent forwarded from a child shard."""
    rank: int
    event: ExecutionEvent


@dataclass(frozen=True)
class ShardResultEnvelope:
    """A terminal episode result dict from a child shard (authoritative source
    for platform reconciliation; carries episode_key so the parent can join)."""
    rank: int
    episode_key: str
    result_dict: dict[str, Any]


@dataclass(frozen=True)
class ShardFatalEnvelope:
    """A fatal shard error (uncaught exception in _shard_main)."""
    rank: int
    exitcode: int
    error: str


@dataclass(frozen=True)
class ShardLifecycleEnvelope:
    """Shard lifecycle transition (started/stopped). ``exitcode`` is set on
    stopped when the shard process exited with a non-zero code."""
    rank: int
    kind: str  # "started" | "stopped"
    exitcode: int | None = None
    error: str | None = None
