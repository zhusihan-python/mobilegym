from __future__ import annotations

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.performance import (
    build_performance_report,
    nearest_rank_percentile,
    percentage_delta,
)


def _attempt(
    attempt_id: str,
    episode_id: str,
    lane_attempt_id: str,
    runtime_s,
    *,
    stopwatch_flat=None,
    outcome: str = "PASS",
    state: str = "completed",
) -> dict:
    is_error = outcome == "ERROR"
    return {
        "id": attempt_id,
        "episode_id": episode_id,
        "episode_key": episode_id,
        "pair_key": episode_id,
        "lane_attempt_id": lane_attempt_id,
        "lane_key": "candidate",
        "attempt_no": 1,
        "state": state,
        "outcome": outcome,
        "error_code": "EXECUTION_ERROR" if is_error else None,
        "result_json": {
            "is_success": not is_error,
            "is_error": is_error,
            "execution": {
                "runtime_s": runtime_s,
                "stopwatch_flat": stopwatch_flat if stopwatch_flat is not None else {},
            },
        },
    }


def _report_input() -> ReportInput:
    planned = []
    attempts = []
    for index, runtime in enumerate([1, 2, 3, 4, 5, 6], start=1):
        episode_id = f"ep{index}"
        lane_attempt_id = f"la{index}"
        planned.append(
            {
                "episode_id": episode_id,
                "episode_key": episode_id,
                "lane_key": "candidate",
                "lane_attempt_id": lane_attempt_id,
                "status": "completed",
            }
        )
        stopwatch = {"load": runtime / 10, "judge": runtime / 5}
        if runtime == 6:
            stopwatch = {}
        attempts.append(_attempt(f"ea{index}", episode_id, lane_attempt_id, runtime, stopwatch_flat=stopwatch))

    planned.append(
        {
            "episode_id": "ep_error",
            "episode_key": "ep_error",
            "lane_key": "candidate",
            "lane_attempt_id": "la_error",
            "status": "completed",
        }
    )
    attempts.append(
        _attempt(
            "ea_error",
            "ep_error",
            "la_error",
            100,
            stopwatch_flat={"load": 100},
            outcome="ERROR",
        )
    )
    planned.append(
        {
            "episode_id": "ep_cancelled",
            "episode_key": "ep_cancelled",
            "lane_key": "candidate",
            "lane_attempt_id": "la_cancelled",
            "status": "cancelled",
        }
    )
    attempts.append(
        _attempt(
            "ea_cancelled",
            "ep_cancelled",
            "la_cancelled",
            100,
            state="cancelled",
        )
    )
    planned.append(
        {
            "episode_id": "ep_incomplete",
            "episode_key": "ep_incomplete",
            "lane_key": "candidate",
            "lane_attempt_id": "la_incomplete",
            "status": "incomplete",
        }
    )
    return ReportInput.from_payload(
        run_id="run1",
        run_attempt_id="attempt1",
        provenance={"run_id": "run1", "run_attempt_id": "attempt1"},
        planned_lane_episodes=planned,
        episode_attempts=attempts,
        comparison=None,
    )


def test_nearest_rank_percentile_matches_golden_values():
    values = [1, 2, 3, 4, 5, 6]

    assert nearest_rank_percentile(values, 50) == 3
    assert nearest_rank_percentile(values, 75) == 5
    assert nearest_rank_percentile(values, 90) == 6
    assert nearest_rank_percentile(values, 95) == 6
    assert nearest_rank_percentile(values, 99) == 6


def test_performance_report_samples_runtime_and_phases_independently():
    report = build_performance_report(_report_input())

    assert report["summary"]["unit"] == "seconds"
    assert report["summary"]["runtime_s"] == {
        "sample_count": 6,
        "p50": 3.0,
        "p75": 5.0,
        "p90": 6.0,
        "p95": 6.0,
        "p99": 6.0,
    }
    assert report["summary"]["excluded"] == {
        "incomplete": 1,
        "cancelled": 1,
        "errors": 1,
        "non_numeric_runtime": 0,
    }
    assert report["summary"]["phases"]["load"]["sample_count"] == 5
    assert report["summary"]["phases"]["load"]["p95"] == 0.5
    assert report["summary"]["phases"]["judge"]["sample_count"] == 5


def test_zero_baseline_suppresses_percentage_delta():
    assert percentage_delta(candidate=5.0, baseline=0.0) == {
        "absolute": 5.0,
        "percent": None,
    }
    assert percentage_delta(candidate=15.0, baseline=10.0) == {
        "absolute": 5.0,
        "percent": 50.0,
    }
