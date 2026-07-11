from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from test_platform.domain.runs import RunDomainError
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ReportRepository, RunRepository


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class RunCompletionResult:
    run_id: str
    run_attempt_id: str
    report_id: str
    gate_verdict: str
    outcome_counts: dict[str, int]
    state: str


class RunCompletionPipeline:
    """Create durable completion facts before transitioning a run to completed."""

    def __init__(
        self,
        database: Database,
        *,
        event_writer: Any,
    ) -> None:
        self.database = database
        self.event_writer = event_writer

    def complete(self, run_id: str) -> RunCompletionResult:
        detail = RunRepository(self.database).get(run_id)
        if detail.state == "completed":
            return self._result(
                run_id,
                ReportRepository(self.database).get(run_id),
                outcome_counts=detail.outcome_counts,
            )
        if detail.state not in {"evaluating", "reporting"}:
            raise RunDomainError(
                "RUN_COMPLETION_INVALID_STATE",
                "The run is not ready for completion.",
                status_code=409,
                details=[{"run_id": run_id, "state": detail.state}],
            )

        try:
            report = ReportRepository(self.database).create_for_completion(run_id)
        except RunDomainError as exc:
            if exc.code in {
                "REPORT_PERSISTENCE_FAILED",
                "QUALITY_GATE_PERSISTENCE_FAILED",
            }:
                self._fail(run_id, error_code=exc.code)
            raise
        self.event_writer.emit(
            run_id,
            "report.completed",
            {
                "report_id": report["id"],
                "input_hash": report["input_hash"],
            },
            run_attempt_id=str(report["run_attempt_id"]),
            entity_type="report",
            entity_id=str(report["id"]),
        )
        verdict = str(report["gate"]["verdict"])
        self.event_writer.emit(
            run_id,
            f"gate.{verdict}",
            {
                "report_id": report["id"],
                "verdict": verdict,
            },
            run_attempt_id=str(report["run_attempt_id"]),
            entity_type="quality_gate",
            entity_id=str(report["id"]),
        )
        now = _utc_timestamp()
        connection = self.database.connection
        with self.database._lock:  # noqa: SLF100 - one terminal state transition
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE run_attempts
                    SET state = 'reporting'
                    WHERE run_id = ? AND state = 'evaluating'
                    """,
                    (run_id,),
                )
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'reporting', updated_at = ?
                    WHERE id = ? AND state = 'evaluating'
                    """,
                    (now, run_id),
                )
                connection.execute(
                    """
                    UPDATE run_attempts
                    SET state = 'completed', ended_at = COALESCE(ended_at, ?)
                    WHERE run_id = ? AND state = 'reporting'
                    """,
                    (now, run_id),
                )
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'completed', ended_at = COALESCE(ended_at, ?),
                        updated_at = ?
                    WHERE id = ? AND state = 'reporting'
                    """,
                    (now, now, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return self._result(
            run_id,
            report,
            outcome_counts=RunRepository(self.database).get(run_id).outcome_counts,
        )

    def _fail(self, run_id: str, *, error_code: str) -> None:
        now = _utc_timestamp()
        connection = self.database.connection
        with self.database._lock:  # noqa: SLF100 - durable failure before event
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE run_attempts
                    SET state = 'failed', error_code = ?,
                        ended_at = COALESCE(ended_at, ?)
                    WHERE run_id = ? AND state IN ('evaluating', 'reporting')
                    """,
                    (error_code, now, run_id),
                )
                connection.execute(
                    """
                    UPDATE runs
                    SET state = 'failed', ended_at = COALESCE(ended_at, ?),
                        updated_at = ?
                    WHERE id = ? AND state IN ('evaluating', 'reporting')
                    """,
                    (now, now, run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        event_type = (
            "gate.failed"
            if error_code == "QUALITY_GATE_PERSISTENCE_FAILED"
            else "report.failed"
        )
        self.event_writer.emit(
            run_id,
            event_type,
            {"error_code": error_code},
            entity_type="run",
            entity_id=run_id,
        )

    @staticmethod
    def _result(
        run_id: str,
        report: dict[str, Any],
        *,
        outcome_counts: dict[str, int],
    ) -> RunCompletionResult:
        return RunCompletionResult(
            run_id=run_id,
            run_attempt_id=str(report["run_attempt_id"]),
            report_id=str(report["id"]),
            gate_verdict=str(report["gate"]["verdict"]),
            outcome_counts=outcome_counts,
            state="completed",
        )
