from __future__ import annotations

import json
from typing import Any

from test_platform.domain.runs import RunNotFound
from test_platform.persistence.database import Database


class BaselineEligibility:
    """Evaluate strict eligibility for one selected lane without mutating state."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def evaluate(
        self,
        run_id: str,
        *,
        lane_key: str | None = None,
    ) -> dict[str, Any]:
        run = self.database.connection.execute(
            "SELECT id FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if run is None:
            raise RunNotFound(run_id)

        report_row = self.database.connection.execute(
            """
            SELECT report_json
            FROM reports
            WHERE run_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if report_row is None:
            return self._result(
                run_id,
                lane_key=lane_key,
                run_attempt_id=None,
                counts=_empty_counts(),
                reasons=[
                    _reason(
                        "REPORT_NOT_PERSISTED",
                        "A durable report is required for strict baseline promotion.",
                    )
                ],
            )
        report = json.loads(report_row["report_json"])

        provenance = report.get("provenance")
        provenance = provenance if isinstance(provenance, dict) else {}
        target_revision_ids = provenance.get("target_revision_ids")
        target_revision_ids = (
            target_revision_ids if isinstance(target_revision_ids, dict) else {}
        )
        selected_lane_key = lane_key or _default_lane_key(target_revision_ids)
        run_attempt_id = str(report.get("run_attempt_id") or "")
        reasons: list[dict[str, Any]] = []

        attempt = self.database.connection.execute(
            "SELECT state FROM run_attempts WHERE id = ? AND run_id = ?",
            (run_attempt_id, run_id),
        ).fetchone()
        if attempt is None or attempt["state"] != "completed":
            reasons.append(
                _reason(
                    "RUN_ATTEMPT_NOT_COMPLETED",
                    "The report run attempt must be completed.",
                    {"state": attempt["state"] if attempt is not None else None},
                )
            )

        missing_provenance = _missing_provenance(provenance, selected_lane_key)
        if missing_provenance:
            reasons.append(
                _reason(
                    "STRICT_PROVENANCE_INCOMPLETE",
                    "Strict baseline provenance is incomplete.",
                    {"missing": missing_provenance},
                )
            )

        if selected_lane_key not in target_revision_ids:
            reasons.append(
                _reason(
                    "SELECTED_LANE_NOT_FOUND",
                    "The selected lane is not present in report provenance.",
                    {"lane_key": selected_lane_key},
                )
            )
            return self._result(
                run_id,
                lane_key=selected_lane_key,
                run_attempt_id=run_attempt_id,
                counts=_empty_counts(),
                reasons=reasons,
            )

        counts = _empty_counts()
        if attempt is not None and attempt["state"] == "completed":
            selected = self._selected_lane_grid(
                run_id,
                run_attempt_id=run_attempt_id,
                lane_key=selected_lane_key,
            )
            counts = _outcome_counts(selected)
            if not selected:
                reasons.append(
                    _reason(
                        "SELECTED_LANE_EMPTY",
                        "The selected lane has no planned episodes.",
                    )
                )
            if counts["incomplete"]:
                reasons.append(
                    _reason(
                        "SELECTED_LANE_INCOMPLETE",
                        "Every planned selected-lane episode must have a terminal attempt.",
                        {"incomplete": counts["incomplete"]},
                    )
                )
            non_pass = {
                key: counts[key]
                for key in ("fail", "error", "cancelled")
                if counts[key]
            }
            if non_pass:
                reasons.append(
                    _reason(
                        "SELECTED_LANE_OUTCOME_NOT_PASS",
                        "Every selected-lane episode must have outcome PASS.",
                        non_pass,
                    )
                )

        return self._result(
            run_id,
            lane_key=selected_lane_key,
            run_attempt_id=run_attempt_id,
            counts=counts,
            reasons=reasons,
        )

    @staticmethod
    def _result(
        run_id: str,
        *,
        lane_key: str | None,
        run_attempt_id: str | None,
        counts: dict[str, int],
        reasons: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "run_attempt_id": run_attempt_id,
            "lane_key": lane_key,
            "eligible": not reasons,
            "counts": counts,
            "reasons": reasons,
        }

    def _selected_lane_grid(
        self,
        run_id: str,
        *,
        run_attempt_id: str,
        lane_key: str,
    ) -> list[dict[str, Any]]:
        rows = self.database.connection.execute(
            """
            SELECT ea.state, ea.outcome
            FROM episodes AS e
            JOIN lanes AS l
              ON l.run_id = e.run_id AND l.lane_key = ?
            LEFT JOIN lane_attempts AS la
              ON la.lane_id = l.id AND la.run_attempt_id = ?
            LEFT JOIN episode_attempts AS ea
              ON ea.id = (
                SELECT latest.id
                FROM episode_attempts AS latest
                WHERE latest.episode_id = e.id
                  AND latest.lane_attempt_id = la.id
                ORDER BY latest.attempt_no DESC, latest.id DESC
                LIMIT 1
              )
            WHERE e.run_id = ?
            ORDER BY e.sequence_index, e.id
            """,
            (lane_key, run_attempt_id, run_id),
        ).fetchall()
        return [
            {
                "status": row["state"] if row["state"] is not None else "incomplete",
                "outcome": row["outcome"],
            }
            for row in rows
        ]


def _outcome_counts(planned: list[dict[str, Any]]) -> dict[str, int]:
    counts = _empty_counts()
    counts["planned"] = len(planned)
    for item in planned:
        outcome = str(item.get("outcome") or "").upper()
        status = str(item.get("status") or "").lower()
        if status not in {"completed", "cancelled", "failed"} or not outcome:
            counts["incomplete"] += 1
        elif outcome == "PASS":
            counts["pass"] += 1
        elif outcome == "ERROR":
            counts["error"] += 1
        elif outcome == "CANCELLED":
            counts["cancelled"] += 1
        else:
            counts["fail"] += 1
    return counts


def _missing_provenance(provenance: dict[str, Any], lane_key: str | None) -> list[str]:
    required = (
        "project_id",
        "workflow_version_id",
        "run_plan_hash",
        "task_source_digest",
    )
    missing = [key for key in required if not provenance.get(key)]
    imported = provenance.get("imported")
    if isinstance(imported, dict) and imported.get("provenance_missing"):
        missing.append("imported.provenance_missing")
    target_revision_ids = provenance.get("target_revision_ids")
    if not isinstance(target_revision_ids, dict) or not target_revision_ids.get(lane_key):
        missing.append(f"target_revision_ids.{lane_key}")
    return missing


def _default_lane_key(target_revision_ids: dict[str, Any]) -> str | None:
    if "candidate" in target_revision_ids:
        return "candidate"
    if len(target_revision_ids) == 1:
        return str(next(iter(target_revision_ids)))
    if target_revision_ids:
        return str(sorted(target_revision_ids)[0])
    return None


def _empty_counts() -> dict[str, int]:
    return {
        "planned": 0,
        "pass": 0,
        "fail": 0,
        "error": 0,
        "cancelled": 0,
        "incomplete": 0,
    }


def _reason(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}
