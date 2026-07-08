from __future__ import annotations

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.sequence import build_sequence_report


def _report_input() -> ReportInput:
    planned = [
        {
            "episode_id": "ep_gamma",
            "episode_key": "task.gamma|i0",
            "task_base_id": "task.gamma",
            "task_id": "task.gamma",
            "lane_key": "candidate",
            "lane_attempt_id": "lane1",
            "sequence_index": 2,
            "sequence_group_id": "manual_sequence",
            "status": "completed",
        },
        {
            "episode_id": "ep_alpha",
            "episode_key": "task.alpha|i0",
            "task_base_id": "task.alpha",
            "task_id": "task.alpha",
            "lane_key": "candidate",
            "lane_attempt_id": "lane1",
            "sequence_index": 0,
            "sequence_group_id": "manual_sequence",
            "status": "completed",
        },
        {
            "episode_id": "ep_beta",
            "episode_key": "task.beta|i0",
            "task_base_id": "task.beta",
            "task_id": "task.beta",
            "lane_key": "candidate",
            "lane_attempt_id": "lane1",
            "sequence_index": 1,
            "sequence_group_id": "manual_sequence",
            "status": "completed",
        },
    ]
    attempts = [
        _attempt("ea_gamma", "ep_gamma", "task.gamma|i0", "PASS"),
        _attempt("ea_alpha", "ep_alpha", "task.alpha|i0", "FAIL"),
        _attempt("ea_beta", "ep_beta", "task.beta|i0", "ERROR"),
    ]
    return ReportInput.from_payload(
        run_id="run1",
        run_attempt_id="attempt1",
        provenance={"run_id": "run1", "run_attempt_id": "attempt1"},
        planned_lane_episodes=planned,
        episode_attempts=attempts,
        comparison=None,
    )


def _attempt(
    attempt_id: str,
    episode_id: str,
    episode_key: str,
    outcome: str,
) -> dict:
    is_error = outcome == "ERROR"
    is_success = outcome == "PASS"
    return {
        "id": attempt_id,
        "episode_id": episode_id,
        "episode_key": episode_key,
        "pair_key": episode_key,
        "lane_attempt_id": "lane1",
        "lane_key": "candidate",
        "attempt_no": 1,
        "state": "completed",
        "outcome": outcome,
        "error_code": "EXECUTION_ERROR" if is_error else None,
        "result_json": {
            "is_success": is_success,
            "is_error": is_error,
        },
    }


def test_sequence_report_groups_manual_sequence_steps_in_order():
    report = build_sequence_report(_report_input())

    assert len(report["groups"]) == 1
    group = report["groups"][0]
    assert group["sequence_group_id"] == "manual_sequence"
    assert group["summary"] == {
        "planned_lane_episodes": 3,
        "attempted_lane_episodes": 3,
        "successes": 1,
        "failures": 1,
        "errors": 1,
        "incomplete": 0,
        "success_rate": 1 / 3,
        "progress_rate": 1.0,
    }
    assert [item["task_id"] for item in group["items"]] == [
        "task.alpha",
        "task.beta",
        "task.gamma",
    ]
    assert [item["step"] for item in group["items"]] == [1, 2, 3]
    assert [item["outcome"] for item in group["items"]] == ["FAIL", "ERROR", "PASS"]


def test_sequence_report_returns_empty_groups_for_non_sequence_runs():
    report_input = ReportInput.from_payload(
        run_id="run1",
        run_attempt_id="attempt1",
        provenance={"run_id": "run1", "run_attempt_id": "attempt1"},
        planned_lane_episodes=[
            {
                "episode_id": "ep1",
                "episode_key": "task.one|i0",
                "task_id": "task.one",
                "lane_key": "candidate",
                "lane_attempt_id": "lane1",
                "status": "completed",
            }
        ],
        episode_attempts=[],
        comparison=None,
    )

    assert build_sequence_report(report_input) == {"schema_version": 1, "groups": []}
