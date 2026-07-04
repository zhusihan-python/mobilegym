from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from test_platform.domain.canonical_json import canonical_json
from test_platform.domain.events import PAYLOAD_VERSION, PersistedEvent
from test_platform.domain.ids import new_id

if TYPE_CHECKING:
    from test_platform.execution.sse_broker import SSEBroker
    from test_platform.persistence.database import Database

logger = logging.getLogger(__name__)


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# VS-06 NOTE: this writer writes events synchronously inside emit(). This is a
# deliberate, temporary deviation from IMPLEMENTATION_DESIGN §13.2, which calls
# for a buffered single-writer task. Synchronous writing is acceptable at VS-06
# scale (single serial episode, low event volume) and keeps the concurrency model
# simple. VS-07 (parallel lanes) will reintroduce the bounded queue + writer task.
#
# Two invariants are preserved regardless:
#   1. emit() never raises into the runner — event failures cannot affect run
#      success (TECHNICAL_ARCHITECTURE §7 EventWriter responsibility).
#   2. Each write is atomic with respect to sequence allocation: a failure rolls
#      the transaction back so no sequence hole or dangling lock is left behind.


class EventWriter:
    """Persists events with per-run monotonic sequence numbers and fans them out.

    Thread-safe via the shared Database RLock; safe to call from worker threads
    as well as the asyncio loop thread.
    """

    def __init__(self, database: "Database", broker: "SSEBroker | None" = None) -> None:
        self._database = database
        self._broker = broker
        # Reuse the database's RLock to serialize all write transactions. This
        # guarantees sequence allocation is gap-free even under concurrent emit.
        self._lock: threading.RLock = database._lock  # noqa: SLF100 (intentional shared lock)

    def emit(
        self,
        run_id: str,
        type: str,
        payload: dict[str, Any],
        *,
        run_attempt_id: str | None = None,
        lane_id: str | None = None,
        lane_attempt_id: str | None = None,
        episode_id: str | None = None,
        episode_attempt_id: str | None = None,
        worker_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        occurred_at: str | None = None,
    ) -> PersistedEvent | None:
        """Persist one event, bump next_event_sequence, and publish to the broker.

        Returns the persisted event on success, or None if persistence failed
        (the failure is logged and never propagates to the caller).
        """
        timestamp = occurred_at or _utc_timestamp()
        event_id = new_id()
        try:
            # canonical_json can raise on non-serializable payloads; keep it
            # inside the try so a bad payload degrades to a logged None rather
            # than propagating into the runner.
            payload_json = canonical_json(payload)
            persisted = self._write(
                run_id=run_id,
                event_id=event_id,
                type=type,
                timestamp=timestamp,
                payload_json=payload_json,
                run_attempt_id=run_attempt_id,
                lane_id=lane_id,
                lane_attempt_id=lane_attempt_id,
                episode_id=episode_id,
                episode_attempt_id=episode_attempt_id,
                worker_id=worker_id,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        except Exception:  # noqa: BLE001 — never propagate into the runner
            logger.exception("Failed to persist event type=%s run_id=%s", type, run_id)
            return None

        if persisted is not None and self._broker is not None:
            try:
                self._broker.publish(persisted)
            except Exception:  # noqa: BLE001 — broker failure must not propagate
                logger.exception("Failed to publish event type=%s run_id=%s", type, run_id)
        return persisted

    def _write(
        self,
        *,
        run_id: str,
        event_id: str,
        type: str,
        timestamp: str,
        payload_json: str,
        run_attempt_id: str | None,
        lane_id: str | None,
        lane_attempt_id: str | None,
        episode_id: str | None,
        episode_attempt_id: str | None,
        worker_id: str | None,
        entity_type: str | None,
        entity_id: str | None,
    ) -> PersistedEvent | None:
        with self._lock:
            connection = self._database.connection
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT next_event_sequence FROM runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if row is None:
                    connection.execute("ROLLBACK")
                    logger.warning("Event for unknown run_id=%s dropped", run_id)
                    return None
                sequence = int(row["next_event_sequence"])
                connection.execute(
                    """
                    INSERT INTO events (
                      id, run_id, sequence, type, entity_type, entity_id,
                      occurred_at, payload_json, payload_version,
                      run_attempt_id, lane_id, lane_attempt_id,
                      episode_id, episode_attempt_id, worker_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        run_id,
                        sequence,
                        type,
                        entity_type,
                        entity_id,
                        timestamp,
                        payload_json,
                        PAYLOAD_VERSION,
                        run_attempt_id,
                        lane_id,
                        lane_attempt_id,
                        episode_id,
                        episode_attempt_id,
                        worker_id,
                    ),
                )
                connection.execute(
                    "UPDATE runs SET next_event_sequence = ? WHERE id = ?",
                    (sequence + 1, run_id),
                )
                connection.execute("COMMIT")
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    logger.exception("Rollback failed for event type=%s", type)
                raise

        return PersistedEvent(
            id=event_id,
            run_id=run_id,
            sequence=sequence,
            type=type,
            occurred_at=timestamp,
            payload=_loads(payload_json),
            payload_version=PAYLOAD_VERSION,
            run_attempt_id=run_attempt_id,
            lane_id=lane_id,
            lane_attempt_id=lane_attempt_id,
            episode_id=episode_id,
            episode_attempt_id=episode_attempt_id,
            worker_id=worker_id,
            entity_type=entity_type,
            entity_id=entity_id,
        )


def _loads(payload_json: str) -> dict[str, Any]:
    import json

    return json.loads(payload_json)
