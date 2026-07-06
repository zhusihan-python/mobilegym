from __future__ import annotations

from copy import deepcopy
from typing import Any

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.selection import select_attempts


def build_functional_report(report_input: ReportInput) -> dict[str, Any]:
    selected = select_attempts(report_input)
    summary = _new_counts()
    lanes: dict[str, dict[str, Any]] = {}
    taxonomy = {
        "suite": {},
        "app": {},
        "difficulty": {},
    }

    for planned in report_input.planned_lane_episodes:
        lane_key = str(planned.get("lane_key") or "unknown")
        lane_counts = lanes.setdefault(lane_key, _new_counts())
        attempt = selected.get(
            (
                str(planned.get("episode_id")),
                str(planned.get("lane_attempt_id")),
            )
        )
        _accumulate(summary, attempt)
        _accumulate(lane_counts, attempt)

        suite, apps, difficulty = _taxonomy_values(planned, attempt)
        _accumulate_group(taxonomy["suite"], suite, attempt)
        for app in apps:
            _accumulate_group(taxonomy["app"], app, attempt)
        _accumulate_group(taxonomy["difficulty"], difficulty, attempt)

    return {
        "schema_version": 1,
        "input": {
            "run_id": report_input.run_id,
            "run_attempt_id": report_input.run_attempt_id,
            "input_hash": report_input.input_hash,
            "selected_episode_attempt_ids": sorted(
                str(attempt["id"]) for attempt in selected.values()
            ),
        },
        "summary": _finalize_counts(summary),
        "lanes": {
            key: _finalize_counts(value) for key, value in sorted(lanes.items())
        },
        "taxonomy": {
            dimension: {
                key: _finalize_counts(value)
                for key, value in sorted(groups.items())
            }
            for dimension, groups in taxonomy.items()
        },
    }


def _new_counts() -> dict[str, int]:
    return {
        "planned_lane_episodes": 0,
        "attempted_lane_episodes": 0,
        "successes": 0,
        "failures": 0,
        "errors": 0,
        "incomplete": 0,
        "false_completions": 0,
    }


def _accumulate(counts: dict[str, int], attempt: dict[str, Any] | None) -> None:
    counts["planned_lane_episodes"] += 1
    if attempt is None:
        counts["incomplete"] += 1
        return

    counts["attempted_lane_episodes"] += 1
    if _is_success(attempt):
        counts["successes"] += 1
    elif _is_error(attempt):
        counts["errors"] += 1
    else:
        counts["failures"] += 1
    if _result_value(attempt, "false_complete") is True:
        counts["false_completions"] += 1


def _accumulate_group(
    groups: dict[str, dict[str, int]],
    key: str,
    attempt: dict[str, Any] | None,
) -> None:
    counts = groups.setdefault(key, _new_counts())
    _accumulate(counts, attempt)


def _finalize_counts(counts: dict[str, int]) -> dict[str, Any]:
    finalized = deepcopy(counts)
    denominator = counts["planned_lane_episodes"]
    finalized["success_rate"] = _rate(counts["successes"], denominator)
    finalized["progress_rate"] = _rate(counts["attempted_lane_episodes"], denominator)
    finalized["error_rate"] = _rate(counts["errors"], denominator)
    finalized["false_completion_rate"] = _rate(
        counts["false_completions"],
        denominator,
    )
    return finalized


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _taxonomy_values(
    planned: dict[str, Any],
    attempt: dict[str, Any] | None,
) -> tuple[str, list[str], str]:
    result = attempt.get("result_json") if attempt else None
    if not isinstance(result, dict):
        result = {}

    suite = result.get("suite") or planned.get("suite") or "unknown"
    difficulty = result.get("difficulty") or planned.get("difficulty") or "unknown"
    raw_apps = result.get("apps") or planned.get("apps") or ["unknown"]
    apps = raw_apps if isinstance(raw_apps, list) else [raw_apps]
    return str(suite), [str(app) for app in apps], str(difficulty)


def _is_success(attempt: dict[str, Any]) -> bool:
    value = _result_value(attempt, "is_success")
    if value is not None:
        return bool(value)
    return str(attempt.get("outcome") or "").upper() in {"PASS", "SUCCESS"}


def _is_error(attempt: dict[str, Any]) -> bool:
    value = _result_value(attempt, "is_error")
    if value is not None:
        return bool(value)
    return str(attempt.get("outcome") or "").upper() == "ERROR"


def _result_value(attempt: dict[str, Any], key: str) -> Any:
    result = attempt.get("result_json")
    if not isinstance(result, dict):
        return None
    return result.get(key)
