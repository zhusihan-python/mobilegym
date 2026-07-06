from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from test_platform.domain.canonical_json import canonical_sha256


DIAGNOSTIC_INPUT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class DiagnosticInput:
    """Stable, canonical input consumed by VS-12 diagnostic builders."""

    run_id: str
    run_attempt_id: str
    provenance: dict[str, Any]
    planned_lane_episodes: list[dict[str, Any]]
    episode_attempts: list[dict[str, Any]]
    comparison: dict[str, Any] | None
    report: dict[str, Any] | None
    gate_results: list[dict[str, Any]]
    input_hash: str
    schema_version: int = DIAGNOSTIC_INPUT_SCHEMA_VERSION

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
        report: dict[str, Any] | None,
        gate_results: list[dict[str, Any]],
    ) -> DiagnosticInput:
        payload = {
            "schema_version": DIAGNOSTIC_INPUT_SCHEMA_VERSION,
            "run_id": run_id,
            "run_attempt_id": run_attempt_id,
            "provenance": provenance,
            "planned_lane_episodes": planned_lane_episodes,
            "episode_attempts": episode_attempts,
            "comparison": comparison,
            "report": report,
            "gate_results": gate_results,
        }
        return cls(
            run_id=run_id,
            run_attempt_id=run_attempt_id,
            provenance=provenance,
            planned_lane_episodes=planned_lane_episodes,
            episode_attempts=episode_attempts,
            comparison=comparison,
            report=report,
            gate_results=gate_results,
            input_hash=canonical_sha256(payload),
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
            "report": self.report,
            "gate_results": self.gate_results,
        }
