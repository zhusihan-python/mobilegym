from __future__ import annotations

from test_platform.domain.reports.functional import build_functional_report
from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.selection import select_attempts


def _report_input() -> ReportInput:
    planned = [
        {
            "episode_id": "ep1",
            "episode_key": "task::1",
            "pair_key": "pair1",
            "task_base_id": "fake.Task",
            "task_id": "fake.Task",
            "lane_id": "lane_base",
            "lane_key": "baseline",
            "role": "baseline",
            "lane_attempt_id": "la_base",
            "suite": "fake",
            "apps": ["fakeapp"],
            "difficulty": "L1",
        },
        {
            "episode_id": "ep1",
            "episode_key": "task::1",
            "pair_key": "pair1",
            "task_base_id": "fake.Task",
            "task_id": "fake.Task",
            "lane_id": "lane_cand",
            "lane_key": "candidate",
            "role": "candidate",
            "lane_attempt_id": "la_cand",
            "suite": "fake",
            "apps": ["fakeapp"],
            "difficulty": "L1",
        },
        {
            "episode_id": "ep2",
            "episode_key": "task::2",
            "pair_key": "pair2",
            "task_base_id": "fake.Task",
            "task_id": "fake.Task",
            "lane_id": "lane_base",
            "lane_key": "baseline",
            "role": "baseline",
            "lane_attempt_id": "la_base",
            "suite": "fake",
            "apps": ["fakeapp"],
            "difficulty": "L1",
        },
        {
            "episode_id": "ep2",
            "episode_key": "task::2",
            "pair_key": "pair2",
            "task_base_id": "fake.Task",
            "task_id": "fake.Task",
            "lane_id": "lane_cand",
            "lane_key": "candidate",
            "role": "candidate",
            "lane_attempt_id": "la_cand",
            "suite": "fake",
            "apps": ["fakeapp"],
            "difficulty": "L1",
        },
    ]
    attempts = [
        {
            "id": "old_ep1_base",
            "episode_id": "ep1",
            "episode_key": "task::1",
            "pair_key": "pair1",
            "lane_attempt_id": "la_base",
            "lane_key": "baseline",
            "attempt_no": 1,
            "state": "completed",
            "outcome": "FAIL",
            "error_code": "ASSERTION_FAILURE",
            "result_json": {
                "suite": "fake",
                "apps": ["fakeapp"],
                "difficulty": "L1",
                "is_success": False,
                "is_error": False,
                "false_complete": False,
            },
        },
        {
            "id": "new_ep1_base",
            "episode_id": "ep1",
            "episode_key": "task::1",
            "pair_key": "pair1",
            "lane_attempt_id": "la_base",
            "lane_key": "baseline",
            "attempt_no": 2,
            "state": "completed",
            "outcome": "PASS",
            "error_code": None,
            "result_json": {
                "suite": "fake",
                "apps": ["fakeapp"],
                "difficulty": "L1",
                "is_success": True,
                "is_error": False,
                "false_complete": False,
            },
        },
        {
            "id": "ep1_cand",
            "episode_id": "ep1",
            "episode_key": "task::1",
            "pair_key": "pair1",
            "lane_attempt_id": "la_cand",
            "lane_key": "candidate",
            "attempt_no": 1,
            "state": "completed",
            "outcome": "ERROR",
            "error_code": "EXECUTION_ERROR",
            "result_json": {
                "suite": "fake",
                "apps": ["fakeapp"],
                "difficulty": "L1",
                "is_success": False,
                "is_error": True,
                "false_complete": False,
            },
        },
        {
            "id": "ep2_base",
            "episode_id": "ep2",
            "episode_key": "task::2",
            "pair_key": "pair2",
            "lane_attempt_id": "la_base",
            "lane_key": "baseline",
            "attempt_no": 1,
            "state": "completed",
            "outcome": "PASS",
            "error_code": None,
            "result_json": {
                "suite": "fake",
                "apps": ["fakeapp"],
                "difficulty": "L1",
                "is_success": True,
                "is_error": False,
                "false_complete": True,
            },
        },
    ]
    return ReportInput.from_payload(
        run_id="run1",
        run_attempt_id="attempt1",
        provenance={"run_id": "run1", "run_attempt_id": "attempt1"},
        planned_lane_episodes=planned,
        episode_attempts=attempts,
        comparison=None,
    )


def test_select_attempts_picks_latest_terminal_non_cancelled_per_episode_lane():
    selected = select_attempts(_report_input())

    assert sorted(attempt["id"] for attempt in selected.values()) == [
        "ep1_cand",
        "ep2_base",
        "new_ep1_base",
    ]


def test_functional_report_uses_planned_denominator_and_taxonomy_breakdowns():
    report_input = _report_input()

    report = build_functional_report(report_input)
    same_report = build_functional_report(report_input)

    assert report == same_report
    assert report["input"]["selected_episode_attempt_ids"] == [
        "ep1_cand",
        "ep2_base",
        "new_ep1_base",
    ]
    assert report["summary"] == {
        "planned_lane_episodes": 4,
        "attempted_lane_episodes": 3,
        "successes": 2,
        "failures": 0,
        "errors": 1,
        "incomplete": 1,
        "false_completions": 1,
        "success_rate": 0.5,
        "progress_rate": 0.75,
        "error_rate": 0.25,
        "false_completion_rate": 0.25,
    }
    assert report["lanes"]["candidate"]["planned_lane_episodes"] == 2
    assert report["lanes"]["candidate"]["errors"] == 1
    assert report["lanes"]["candidate"]["incomplete"] == 1
    assert report["taxonomy"]["suite"]["fake"]["planned_lane_episodes"] == 4
    assert report["taxonomy"]["app"]["fakeapp"]["successes"] == 2
    assert report["taxonomy"]["difficulty"]["L1"]["false_completions"] == 1
