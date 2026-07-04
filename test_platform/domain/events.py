from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Bumped when the persisted payload shape of any event family changes. Existing
# events inherit version 1; new events are written with the current value.
PAYLOAD_VERSION = 1


@dataclass(frozen=True)
class PersistedEvent:
    """A row of the `events` table, as returned to API clients and the broker.

    The typed identity columns (run_attempt_id/lane_id/...) are nullable because
    VS-05's `run.created` rows predate them and because not every event family
    carries every id (e.g. `run.started` has no episode_id).
    """

    id: str
    run_id: str
    sequence: int
    type: str
    occurred_at: str
    payload: dict[str, Any]
    payload_version: int = PAYLOAD_VERSION
    run_attempt_id: str | None = None
    lane_id: str | None = None
    lane_attempt_id: str | None = None
    episode_id: str | None = None
    episode_attempt_id: str | None = None
    worker_id: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None

    def to_api(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "type": self.type,
            "occurred_at": self.occurred_at,
            "payload": self.payload,
            "payload_version": self.payload_version,
            "run_attempt_id": self.run_attempt_id,
            "lane_id": self.lane_id,
            "lane_attempt_id": self.lane_attempt_id,
            "episode_id": self.episode_id,
            "episode_attempt_id": self.episode_attempt_id,
            "worker_id": self.worker_id,
        }
