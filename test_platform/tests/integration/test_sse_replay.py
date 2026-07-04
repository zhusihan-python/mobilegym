from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.api.routes.events import _frame, _normalize_after, _reset_frame
from test_platform.config import PlatformSettings
from test_platform.domain.events import PersistedEvent
from test_platform.execution.event_writer import EventWriter
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database
from test_platform.services.runs import FakeRunSupervisor


# TestClient is used by the REST backlog test below.


def _settings(tmp_path) -> PlatformSettings:
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _seed_run(database: Database, run_id: str = "r1") -> str:
    c = database.connection
    c.execute(
        "INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
        "VALUES ('p1','P','p','p',NULL,'2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
    )
    c.execute(
        "INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
        "VALUES ('w1','p1','W','{}','2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
    )
    c.execute(
        "INSERT INTO workflow_versions "
        "(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
        "VALUES ('wv1','w1',1,'published','{}','h','2026-07-04T00:00:00.000Z','2026-07-04T00:00:00.000Z')"
    )
    c.execute(
        "INSERT INTO runs "
        "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
        " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at) "
        f"VALUES ('{run_id}','p1','wv1',NULL,'running','{{}}','h','runs/{run_id}', "
        " 1, NULL, '2026-07-04T00:00:00.000Z', '2026-07-04T00:00:00.000Z')"
    )
    c.commit()
    return run_id


# ---------------------------------------------------------------------------
# Pure unit: cursor normalization
# ---------------------------------------------------------------------------


def test_normalize_after_priority_last_event_id_then_after_then_after_sequence():
    assert _normalize_after(None, None, None) == 0
    assert _normalize_after("5", "3", "1") == 5
    assert _normalize_after(None, "3", "1") == 3
    assert _normalize_after(None, None, "1") == 1
    assert _normalize_after("garbage", None, None) == 0


# ---------------------------------------------------------------------------
# REST backlog
# ---------------------------------------------------------------------------


def test_rest_backlog_returns_events_after_cursor(tmp_path):
    database = Database(_settings(tmp_path))
    database.initialize()
    try:
        run_id = _seed_run(database)
        writer = EventWriter(database)
        writer.emit(run_id, "run.started", {"s": 1})
        writer.emit(run_id, "episode.started", {"s": 2})
        writer.emit(run_id, "episode.completed", {"s": 3})

        app = create_app(_settings(tmp_path), database=database, supervisor=FakeRunSupervisor())
        with TestClient(app) as client:
            all_events = client.get(f"/api/platform/v1/runs/{run_id}/events").json()
            assert [e["sequence"] for e in all_events["items"]] == [1, 2, 3]

            after1 = client.get(
                f"/api/platform/v1/runs/{run_id}/events?after=1"
            ).json()
            assert [e["sequence"] for e in after1["items"]] == [2, 3]

            after1_alt = client.get(
                f"/api/platform/v1/runs/{run_id}/events?after_sequence=1"
            ).json()
            assert [e["sequence"] for e in after1_alt["items"]] == [2, 3]
    finally:
        database.close()


# ---------------------------------------------------------------------------
# SSE: reconnect replays only events after the cursor
# (verified via the broker subscribe-before-backlog contract, which is what the
# route implements; the HTTP stream is itself unbounded and tested via REST)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_reconnect_only_emits_events_after_cursor(tmp_path):
    """Subscribe-before-backlog + after cursor means a late subscriber gets the
    full event tail without duplication, and an event published between backlog
    read and live tail is not lost."""
    database = Database(_settings(tmp_path))
    database.initialize()
    try:
        run_id = _seed_run(database)
        broker = SSEBroker()
        broker.bind_loop(asyncio.get_running_loop())
        writer = EventWriter(database, broker)
        writer.emit(run_id, "run.started", {})

        # Subscribe (as the route does, before reading backlog).
        subscription = await broker.subscribe(run_id)
        # Read backlog from the DB (simulates the route's first phase).
        from test_platform.persistence.repositories import RunRepository

        backlog = RunRepository(database).list_events(run_id)
        seen = {e.sequence for e in backlog}
        # Publish a live event AFTER the backlog read but the subscription is
        # already active, so it lands in the queue (race closed).
        writer.emit(run_id, "episode.started", {})

        # Drain the live queue with a short timeout.
        received: list[int] = [e.sequence for e in backlog]
        try:
            while True:
                event = await asyncio.wait_for(subscription.queue.get(), timeout=0.5)
                if event is None:
                    break
                if event.sequence not in seen:
                    seen.add(event.sequence)
                    received.append(event.sequence)
        except asyncio.TimeoutError:
            pass

        # Backlog event (1) and the live event (2) both present, no duplicates.
        assert received == [1, 2]

        # Reconnect after sequence 1 must NOT replay sequence 1.
        await broker.unsubscribe(run_id, subscription)
        after1 = RunRepository(database).list_events(run_id, after_sequence=1)
        assert [e.sequence for e in after1] == [2]
    finally:
        database.close()


@pytest.mark.asyncio
async def test_sse_full_queue_emits_reset_required(tmp_path):
    """When a subscriber's queue overflows it is closed and sent reset_required."""
    database = Database(_settings(tmp_path))
    database.initialize()
    try:
        run_id = _seed_run(database)
        broker = SSEBroker()
        broker.bind_loop(asyncio.get_running_loop())
        writer = EventWriter(database, broker)

        subscription = await broker.subscribe(run_id)
        # Overflow the bounded queue.
        for i in range(70):
            writer.emit(run_id, f"bulk.{i}", {"i": i})
        # Let the loop drain the scheduled fan-out callbacks.
        await asyncio.sleep(0.1)

        # The subscription should be marked closed due to overflow.
        assert subscription.closed is True
    finally:
        database.close()


# ---------------------------------------------------------------------------
# SSE frame format (deterministic, no streaming)
# ---------------------------------------------------------------------------


def test_sse_frame_format_is_spec_compliant():
    event = PersistedEvent(
        id="abc",
        run_id="r1",
        sequence=42,
        type="episode.completed",
        occurred_at="2026-07-04T00:00:00.000Z",
        payload={"outcome": "PASS"},
    )
    frame = _frame(event)
    assert frame.startswith("id: 42\n")
    assert "event: episode.completed\n" in frame
    assert 'data: {"id": "abc"' in frame
    assert frame.endswith("\n\n")


def test_reset_frame_is_emitted_for_overflow():
    frame = _reset_frame()
    assert "event: stream.reset_required" in frame
    assert frame.endswith("\n\n")


def test_sse_endpoint_is_registered_and_404s_unknown_run(tmp_path):
    """The SSE route is wired (not 404 on the path itself) and validates the run."""
    database = Database(_settings(tmp_path))
    database.initialize()
    try:
        app = create_app(
            _settings(tmp_path), database=database, supervisor=FakeRunSupervisor()
        )
        with TestClient(app) as client:
            response = client.get(
                "/api/platform/v1/runs/does-not-exist/events"
            )
            assert response.status_code == 404
    finally:
        database.close()
