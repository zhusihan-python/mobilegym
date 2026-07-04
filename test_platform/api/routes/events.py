from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Header, Request
from starlette.responses import StreamingResponse

from test_platform.api.dependencies import get_database
from test_platform.api.errors import ApiError
from test_platform.domain.runs import RunDomainError
from test_platform.persistence.repositories import RunRepository

router = APIRouter(prefix="/api/platform/v1")

_SSE_HEARTBEAT_SECONDS = 15.0


def _normalize_after(
    last_event_id: str | None,
    after: str | None,
    after_sequence: str | None,
) -> int:
    """Normalize the three accepted resumption parameters into a sequence cursor.

    Priority: Last-Event-ID header > `after` query > `after_sequence` query.
    All are optional; missing means "from the beginning".
    """
    raw = last_event_id or after or after_sequence
    if raw is None or raw == "":
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


@router.get("/runs/{run_id}/events")
def list_run_events(
    request: Request,
    run_id: str,
    after: str | None = None,
    after_sequence: str | None = None,
) -> dict[str, object]:
    """REST backlog of committed events for a run (SSE reconnect fallback)."""
    database = get_database(request)
    try:
        RunRepository(database).get(run_id)
    except RunDomainError as exc:
        raise _to_api_error(exc) from exc
    cursor = _normalize_after(None, after, after_sequence)
    events = RunRepository(database).list_events(run_id, after_sequence=cursor)
    return {"items": [event.to_api() for event in events], "next_cursor": None}


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    request: Request,
    run_id: str,
    after: str | None = None,
    after_sequence: str | None = None,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Server-Sent Events stream for a run.

    Resumption cursor is read from `Last-Event-ID`, `after`, or
    `after_sequence` (in that priority order). The broker is subscribed BEFORE
    the backlog is read so events arriving during the backlog query are not lost.
    """
    database = get_database(request)
    try:
        RunRepository(database).get(run_id)
    except RunDomainError as exc:
        raise _to_api_error(exc) from exc

    broker = getattr(request.app.state, "sse_broker", None)
    cursor = _normalize_after(last_event_id, after, after_sequence)

    async def event_stream():
        # Subscribe before reading the backlog to close the race between a
        # committed event and backlog delivery.
        subscription = None
        if broker is not None:
            subscription = await broker.subscribe(run_id)
        sent_sequences: set[int] = set()
        try:
            # 1. Backlog.
            for event in RunRepository(database).list_events(
                run_id, after_sequence=cursor
            ):
                if event.sequence in sent_sequences:
                    continue
                sent_sequences.add(event.sequence)
                yield _frame(event)
            # 2. Live tail.
            if subscription is not None:
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await asyncio.wait_for(
                            subscription.queue.get(), timeout=_SSE_HEARTBEAT_SECONDS
                        )
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
                        continue
                    if event is None:
                        # Sentinel from unsubscribe / reset.
                        break
                    if subscription.closed:
                        yield _reset_frame()
                        break
                    if event.sequence in sent_sequences:
                        continue
                    sent_sequences.add(event.sequence)
                    yield _frame(event)
        finally:
            if broker is not None and subscription is not None:
                await broker.unsubscribe(run_id, subscription)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _frame(event: Any) -> str:
    data = json.dumps(event.to_api())
    return f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n"


def _reset_frame() -> str:
    return "event: stream.reset_required\ndata: {}\n\n"


def _to_api_error(error: RunDomainError) -> ApiError:
    return ApiError(
        error.code,
        error.message,
        status_code=error.status_code,
        details=error.details,
    )
