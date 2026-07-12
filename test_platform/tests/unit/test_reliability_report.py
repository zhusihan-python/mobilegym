"""Unit tests for the reliability report builder (Pass@k and flakiness).

Uses ``planned_lane_episodes`` as the authoritative trial universe, verifying
planned/attempted/valid/success/failure/error/cancelled/missing counts,
Pass@k values, paired-lane separation, SUCCESS outcome handling, and
missing-trial detection.
"""

from __future__ import annotations

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.reliability import build_reliability_report


def _make_input(planned: list[dict]) -> ReportInput:
    return ReportInput(
        run_id="run-1",
        run_attempt_id="attempt-1",
        provenance={},
        planned_lane_episodes=planned,
        episode_attempts=[],
        comparison=None,
        input_hash="sha256:test",
    )


def _planned(
    mat_key: str,
    trial_id: int,
    *,
    lane_key: str = "candidate",
    outcome: str | None = "PASS",
    status: str = "completed",
    episode_key: str | None = None,
) -> dict:
    ek = episode_key or f"{mat_key}|t{trial_id}"
    return {
        "episode_id": f"ep-{mat_key}-t{trial_id}-{lane_key}",
        "episode_key": ek,
        "pair_key": ek,
        "materialization_key": mat_key,
        "task_base_id": mat_key.split("|")[0],
        "task_id": mat_key.split("|")[0],
        "lane_id": f"lane-{lane_key}",
        "lane_key": lane_key,
        "role": lane_key,
        "lane_attempt_id": f"la-{lane_key}",
        "episode_attempt_id": f"ea-{mat_key}-t{trial_id}-{lane_key}",
        "status": status,
        "outcome": outcome,
        "error_code": None,
    }


class TestReliabilityCounts:
    def test_all_pass_stable(self):
        planned = [_planned("task.A|i0|s1|r1", t) for t in range(3)]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["counts"]["valid"] == 3
        assert task["counts"]["success"] == 3
        assert task["flakiness"] == "stable"
        assert task["pass_at_k"]["1"] == 1.0

    def test_all_fail_stable(self):
        planned = [_planned("task.A|i0|s1|r1", t, outcome="FAIL") for t in range(3)]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["counts"]["valid"] == 3
        assert task["counts"]["failure"] == 3
        assert task["flakiness"] == "stable"
        assert task["pass_at_k"]["1"] == 0.0

    def test_mixed_pass_fail_flaky(self):
        planned = [
            _planned("task.A|i0|s1|r1", 0, outcome="PASS"),
            _planned("task.A|i0|s1|r1", 1, outcome="FAIL"),
            _planned("task.A|i0|s1|r1", 2, outcome="PASS"),
        ]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["counts"]["valid"] == 3
        assert task["flakiness"] == "flaky"
        assert abs(task["pass_at_k"]["1"] - round(2 / 3, 4)) < 0.001

    def test_single_trial_insufficient(self):
        planned = [_planned("task.A|i0|s1|r1", 0, outcome="PASS")]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["flakiness"] == "insufficient_trials"
        assert task["counts"]["valid"] == 1

    def test_no_valid_trials_flakiness_none(self):
        planned = [_planned("task.A|i0|s1|r1", t, outcome="ERROR") for t in range(3)]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["flakiness"] is None
        assert task["counts"]["valid"] == 0
        assert task["counts"]["error"] == 3

    def test_errors_excluded_from_valid_denominator(self):
        planned = [
            _planned("task.A|i0|s1|r1", 0, outcome="PASS"),
            _planned("task.A|i0|s1|r1", 1, outcome="ERROR"),
            _planned("task.A|i0|s1|r1", 2, outcome="FAIL"),
        ]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["counts"]["valid"] == 2  # only PASS + FAIL
        assert task["counts"]["error"] == 1
        assert task["flakiness"] == "flaky"

    def test_cancelled_reported_separately(self):
        planned = [
            _planned("task.A|i0|s1|r1", 0, outcome="PASS"),
            _planned("task.A|i0|s1|r1", 1, outcome="CANCELLED", status="cancelled"),
        ]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["counts"]["valid"] == 1
        assert task["counts"]["cancelled"] == 1

    def test_missing_trial_detected_from_planned_rows(self):
        """A planned trial with no attempt row must be counted as missing,
        not silently dropped."""
        planned = [
            _planned("task.A|i0|s1|r1", 0, outcome="PASS"),
            _planned("task.A|i0|s1|r1", 1, outcome=None, status="incomplete"),
        ]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["counts"]["planned"] == 2
        assert task["counts"]["missing"] == 1
        assert task["counts"]["valid"] == 1

    def test_multiple_materializations_independent(self):
        planned = [
            _planned("task.A|i0|s1|r1", 0, outcome="PASS"),
            _planned("task.A|i0|s1|r1", 1, outcome="PASS"),
            _planned("task.B|i0|s2|r1", 0, outcome="FAIL"),
            _planned("task.B|i0|s2|r1", 1, outcome="FAIL"),
        ]
        report = build_reliability_report(_make_input(planned))
        assert len(report["tasks"]) == 2

    def test_pass_at_k_matches_canonical_estimator(self):
        from bench_env.metrics import _compute_single_pass_k

        planned = [
            _planned("task.A|i0|s1|r1", t, outcome="PASS" if t % 2 == 0 else "FAIL")
            for t in range(4)
        ]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        n, c = 4, 2
        for k in [1, 2, 5]:
            expected = round(_compute_single_pass_k(n, c, k), 4)
            assert task["pass_at_k"][str(k)] == expected

    def test_summary_aggregates(self):
        planned = [
            _planned("task.A|i0|s1|r1", 0, outcome="PASS"),
            _planned("task.A|i0|s1|r1", 1, outcome="FAIL"),
            _planned("task.B|i0|s2|r1", 0, outcome="PASS"),
        ]
        report = build_reliability_report(_make_input(planned))
        summary = report["summary"]
        assert summary["total_materializations"] == 2
        assert summary["flaky_tasks"] == 1
        assert summary["insufficient_trials_tasks"] == 1

    def test_empty_planned(self):
        report = build_reliability_report(_make_input([]))
        assert report["tasks"] == []
        assert report["summary"]["total_materializations"] == 0


class TestPairedLaneSeparation:
    """Paired runs share materialization_key across lanes. The builder must
    group by (lane_key, materialization_key) to avoid mixing baseline/candidate."""

    def test_paired_lanes_produce_separate_tasks(self):
        planned = [
            _planned("task.A|i0|s1|r1", 0, lane_key="baseline", outcome="PASS"),
            _planned("task.A|i0|s1|r1", 0, lane_key="candidate", outcome="FAIL"),
        ]
        report = build_reliability_report(_make_input(planned))
        assert len(report["tasks"]) == 2
        baseline = next(t for t in report["tasks"] if t["lane_key"] == "baseline")
        candidate = next(t for t in report["tasks"] if t["lane_key"] == "candidate")
        assert baseline["counts"]["success"] == 1
        assert candidate["counts"]["failure"] == 1
        # Neither should be flaky (single trial each).
        assert baseline["flakiness"] == "insufficient_trials"
        assert candidate["flakiness"] == "insufficient_trials"

    def test_task_includes_lane_key(self):
        planned = [_planned("task.A|i0|s1|r1", 0, lane_key="candidate")]
        report = build_reliability_report(_make_input(planned))
        assert report["tasks"][0]["lane_key"] == "candidate"


class TestSuccessOutcome:
    """The builder must treat SUCCESS the same as PASS."""

    def test_success_counted_as_success(self):
        planned = [_planned("task.A|i0|s1|r1", t, outcome="SUCCESS") for t in range(3)]
        report = build_reliability_report(_make_input(planned))
        task = report["tasks"][0]
        assert task["counts"]["success"] == 3
        assert task["counts"]["valid"] == 3
        assert task["counts"]["missing"] == 0
        assert task["flakiness"] == "stable"
