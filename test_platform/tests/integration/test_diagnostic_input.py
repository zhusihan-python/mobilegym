from __future__ import annotations

import json

from test_platform.domain.canonical_json import canonical_json
from test_platform.domain.diagnostics.builder import build_diagnostics
from test_platform.persistence.repositories import (
    DiagnosticInputRepository,
    ReportRepository,
)
from test_platform.tests.integration.test_report_input import (
    _database,
    _seed_reportable_paired_run,
)


def test_diagnostic_input_joins_report_context_and_builds_stable_diagnostics(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_reportable_paired_run(database)
        row = database.connection.execute(
            "SELECT run_plan_json FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        run_plan = json.loads(row["run_plan_json"])
        run_plan["gates"] = {"max_candidate_errors": 0}
        database.connection.execute(
            "UPDATE runs SET run_plan_json = ? WHERE id = ?",
            (json.dumps(run_plan, sort_keys=True), run_id),
        )
        database.connection.commit()

        report = ReportRepository(database).get_or_create(run_id)
        assert report["gate"]["verdict"] == "failed"

        diagnostic_input = DiagnosticInputRepository(database).get_for_run(run_id)
        same_input = DiagnosticInputRepository(database).get_for_run(run_id)

        assert diagnostic_input.run_attempt_id == "attempt3"
        assert diagnostic_input.input_hash == same_input.input_hash
        assert diagnostic_input.provenance["run_plan_hash"] == "sha256:plan"
        assert {attempt["id"] for attempt in diagnostic_input.episode_attempts} == {
            "ea_base_ep0",
            "ea_cand_ep0",
        }
        assert diagnostic_input.comparison is not None
        pair = diagnostic_input.comparison["pairs"][0]
        assert pair["classification"] == "candidate_error"
        assert pair["candidate_attempt"]["id"] == "ea_cand_ep0"
        assert diagnostic_input.report["id"] == report["id"]
        assert diagnostic_input.gate_results[0]["verdict"] == "failed"

        diagnostics = build_diagnostics(diagnostic_input)
        same_diagnostics = build_diagnostics(same_input)

        assert canonical_json(diagnostics) == canonical_json(same_diagnostics)
        assert diagnostics["input_hash"] == diagnostic_input.input_hash
        records = diagnostics["items"]
        by_entity = {(record["entity_type"], record["code"]): record for record in records}
        assert ("episode_attempt", "EXECUTION_ERROR") in by_entity
        assert by_entity[("episode_attempt", "EXECUTION_ERROR")][
            "episode_attempt_id"
        ] == "ea_cand_ep0"
        assert ("comparison_pair", "CANDIDATE_ERROR") in by_entity
        assert by_entity[("comparison_pair", "CANDIDATE_ERROR")]["pair_key"] == "pair0"
        assert ("gate", "QUALITY_GATE_FAILED") in by_entity
        assert by_entity[("gate", "QUALITY_GATE_FAILED")]["raw"]["thresholds"] == {
            "max_candidate_errors": 0
        }
        assert diagnostics["summary"]["total"] == 3
    finally:
        database.close()
