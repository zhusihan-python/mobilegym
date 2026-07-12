from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from test_platform.domain.canonical_json import canonical_sha256


REPORT_INPUT_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class ReportInput:
    """Stable, canonical input consumed by VS-11 report builders."""

    run_id: str
    run_attempt_id: str
    provenance: dict[str, Any]
    planned_lane_episodes: list[dict[str, Any]]
    episode_attempts: list[dict[str, Any]]
    comparison: dict[str, Any] | None
    input_hash: str
    monitor_sources: list[dict[str, Any]] | None = None
    schema_version: int = REPORT_INPUT_SCHEMA_VERSION

    @classmethod
    def from_payload(
        cls,
        *,
        run_id: str,
        run_attempt_id: str,
        provenance: dict[str, Any],
        planned_lane_episodes: list[dict[str, Any]],
        episode_attempts: list[dict[str, Any]],
        comparison: dict[str, Any] | None,
        monitor_sources: list[dict[str, Any]] | None = None,
    ) -> ReportInput:
        payload = {
            "schema_version": REPORT_INPUT_SCHEMA_VERSION,
            "run_id": run_id,
            "run_attempt_id": run_attempt_id,
            "provenance": provenance,
            "planned_lane_episodes": planned_lane_episodes,
            "episode_attempts": episode_attempts,
            "comparison": comparison,
            "monitor_sources": monitor_sources,
        }
        return cls(
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            provenance=provenance,
            planned_lane_episodes=planned_lane_episodes,
            episode_attempts=episode_attempts,
            comparison=comparison,
            input_hash=canonical_sha256(payload),
            monitor_sources=monitor_sources,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "run_attempt_id": self.run_attempt_id,
            "input_hash": self.input_hash,
            "provenance": self.provenance,
            "planned_lane_episodes": self.planned_lane_episodes,
            "episode_attempts": self.episode_attempts,
            "comparison": self.comparison,
            "monitor_sources": self.monitor_sources,
        }
