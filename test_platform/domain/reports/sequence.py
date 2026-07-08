from __future__ import annotations

from copy import deepcopy
from typing import Any

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.selection import select_attempts


def build_sequence_report(report_input: ReportInput) -> dict[str, Any]:
    selected = select_attempts(report_input)
    groups: dict[str, dict[str, Any]] = {}

    for planned in report_input.planned_lane_episodes:
        sequence_index = _sequence_index(planned.get("sequence_index"))
        sequence_group_id = planned.get("sequence_group_id")
        if sequence_index is None and sequence_group_id is None:
            continue

        group_id = str(sequence_group_id or "manual_sequence")
        group = groups.setdefault(
            group_id,
            {
                "sequence_group_id": group_id,
                "summary": _new_counts(),
                "items": [],
            },
        )
        attempt = selected.get(
            (
                str(planned.get("episode_id")),
                str(planned.get("lane_attempt_id")),
            )
        )
        _accumulate(group["summary"], attempt)
        group["items"].append(_sequence_item(planned, attempt, sequence_index))

    return {
        "schema_version": 1,
        "groups": [
            {
                "sequence_group_id": group["sequence_group_id"],
                "summary": _finalize_counts(group["summary"]),
                "items": sorted(group["items"], key=_item_sort_key),
            }
            for group in sorted(groups.values(), key=lambda item: str(item["sequence_group_id"]))
        ],
    }


def _sequence_item(
    planned: dict[str, Any],
    attempt: dict[str, Any] | None,
    sequence_index: int | None,
) -> dict[str, Any]:
    return {
        "sequence_index": sequence_index,
        "step": sequence_index + 1 if sequence_index is not None else None,
        "sequence_group_id": str(planned.get("sequence_group_id") or "manual_sequence"),
        "episode_key": str(planned.get("episode_key") or ""),
        "task_id": str(planned.get("task_id") or ""),
        "task_base_id": str(planned.get("task_base_id") or ""),
        "lane_key": str(planned.get("lane_key") or ""),
        "status": str(
            attempt.get("state") if attempt else planned.get("status") or "incomplete"
        ),
        "outcome": attempt.get("outcome") if attempt else planned.get("outcome"),
        "error_code": attempt.get("error_code") if attempt else planned.get("error_code"),
        "episode_attempt_id": attempt.get("id") if attempt else planned.get("episode_attempt_id"),
    }


def _new_counts() -> dict[str, int]:
    return {
        "planned_lane_episodes": 0,
        "attempted_lane_episodes": 0,
        "successes": 0,
        "failures": 0,
        "errors": 0,
        "incomplete": 0,
    }


def _accumulate(counts: dict[str, int], attempt: dict[str, Any] | None) -> None:
    counts["planned_lane_episodes"] += 1
    if attempt is None:
        counts["incomplete"] += 1
        return

    counts["attempted_lane_episodes"] += 1
    outcome = str(attempt.get("outcome") or "").upper()
    if _is_error(attempt):
        counts["errors"] += 1
    elif _is_success(attempt):
        counts["successes"] += 1
    elif outcome == "CANCELLED":
        counts["incomplete"] += 1
    else:
        counts["failures"] += 1


def _finalize_counts(counts: dict[str, int]) -> dict[str, Any]:
    finalized = deepcopy(counts)
    denominator = counts["planned_lane_episodes"]
    finalized["success_rate"] = 0.0 if denominator <= 0 else counts["successes"] / denominator
    finalized["progress_rate"] = (
        0.0 if denominator <= 0 else counts["attempted_lane_episodes"] / denominator
    )
    return finalized


def _is_success(attempt: dict[str, Any]) -> bool:
    result = attempt.get("result_json")
    if isinstance(result, dict) and result.get("is_success") is not None:
        return bool(result.get("is_success"))
    return str(attempt.get("outcome") or "").upper() in {"PASS", "SUCCESS"}


def _is_error(attempt: dict[str, Any]) -> bool:
    result = attempt.get("result_json")
    if isinstance(result, dict) and result.get("is_error") is not None:
        return bool(result.get("is_error"))
    return str(attempt.get("outcome") or "").upper() == "ERROR"


def _sequence_index(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _item_sort_key(item: dict[str, Any]) -> tuple[int, int, str, str]:
    sequence_index = item.get("sequence_index")
    if isinstance(sequence_index, int):
        return (
            0,
            sequence_index,
            str(item.get("episode_key") or ""),
            str(item.get("lane_key") or ""),
        )
    return (
        1,
        0,
        str(item.get("episode_key") or ""),
        str(item.get("lane_key") or ""),
    )
