from __future__ import annotations

from test_platform.domain.reports.comparison import build_comparison_report
from test_platform.domain.reports.input import ReportInput


def _attempt(attempt_id: str, runtime_s: float, *, load: float | None = None) -> dict:
    stopwatch_flat = {} if load is None else {"load": load}
    return {
        "id": attempt_id,
        "episode_id": attempt_id,
        "episode_key": attempt_id,
        "pair_key": attempt_id,
        "lane_attempt_id": f"lane_{attempt_id}",
        "lane_key": "baseline" if attempt_id.startswith("b") else "candidate",
        "attempt_no": 1,
        "state": "completed",
        "outcome": "PASS",
        "error_code": None,
        "result_json": {
            "is_success": True,
            "is_error": False,
            "execution": {
                "runtime_s": runtime_s,
                "stopwatch_flat": stopwatch_flat,
            },
        },
    }


def _report_input() -> ReportInput:
    attempts = [
        _attempt("b1", 10, load=1.0),
        _attempt("c1", 12, load=1.5),
        _attempt("b2", 0, load=0.0),
        _attempt("c2", 5, load=2.0),
        _attempt("b3", 3, load=1.0),
        _attempt("c3", 3, load=1.0),
    ]
    comparison = {
        "id": "cmp1",
        "pairs": [
            {
                "pair_key": "pair1",
                "baseline_episode_attempt_id": "b1",
                "candidate_episode_attempt_id": "c1",
                "classification": "regression",
                "delta": {"runtime_s": {"absolute": -999}},
            },
            {
                "pair_key": "pair2",
                "baseline_episode_attempt_id": "b2",
                "candidate_episode_attempt_id": "c2",
                "classification": "fixed",
                "delta": {},
            },
            {
                "pair_key": "pair3",
                "baseline_episode_attempt_id": None,
                "candidate_episode_attempt_id": "c3",
                "classification": "unpaired",
                "delta": {},
            },
            {
                "pair_key": "pair4",
                "baseline_episode_attempt_id": "b3",
                "candidate_episode_attempt_id": "c3",
                "classification": "stable_pass",
                "delta": {},
            },
        ],
    }
    return ReportInput.from_payload(
        run_id="run1",
        run_attempt_id="attempt1",
        provenance={"run_id": "run1", "run_attempt_id": "attempt1"},
        planned_lane_episodes=[],
        episode_attempts=attempts,
        comparison=comparison,
    )


def test_comparison_report_counts_classifications_and_joins_attempt_metrics():
    report = build_comparison_report(_report_input())

    assert report["classification_counts"] == {
        "total_pairs": 4,
        "regressions": 1,
        "fixed": 1,
        "stable_pass": 1,
        "stable_fail": 0,
        "baseline_errors": 0,
        "candidate_errors": 0,
        "pairing_violations": 0,
        "unpaired": 1,
    }
    assert report["coverage"] == {
        "total_pairs": 4,
        "paired_pairs": 3,
        "unpaired_pairs": 1,
        "coverage_rate": 0.75,
    }
    assert report["runtime_s"] == {
        "unit": "seconds",
        "sample_count": 3,
        "baseline_p95": 10.0,
        "candidate_p95": 12.0,
        "absolute_delta": 2.0,
        "percent_delta": 20.0,
    }
    assert report["phases"]["load"]["sample_count"] == 3
    assert report["phases"]["load"]["absolute_delta"] == 1.0
    assert report["pair_deltas"]["pair1"]["runtime_s"]["absolute"] == 2.0
    assert report["pair_deltas"]["pair2"]["runtime_s"]["percent"] is None
    assert "pair3" not in report["pair_deltas"]
