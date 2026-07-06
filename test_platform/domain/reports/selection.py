from __future__ import annotations

from typing import Any

from test_platform.domain.reports.input import ReportInput


def select_attempts(report_input: ReportInput) -> dict[tuple[str, str], dict[str, Any]]:
    """Select latest terminal non-cancelled attempt per planned episode/lane."""

    selected: dict[tuple[str, str], dict[str, Any]] = {}
    for attempt in report_input.episode_attempts:
        if str(attempt.get("state") or "").casefold() == "cancelled":
            continue
        episode_id = attempt.get("episode_id")
        lane_attempt_id = attempt.get("lane_attempt_id")
        if episode_id is None or lane_attempt_id is None:
            continue
        key = (str(episode_id), str(lane_attempt_id))
        existing = selected.get(key)
        if existing is None or int(attempt.get("attempt_no") or 0) > int(
            existing.get("attempt_no") or 0
        ):
            selected[key] = attempt
    return dict(sorted(selected.items(), key=lambda item: _attempt_sort_key(item[1])))


def _attempt_sort_key(attempt: dict[str, Any]) -> tuple[str, str, int, str]:
    return (
        str(attempt.get("episode_key") or ""),
        str(attempt.get("lane_key") or ""),
        int(attempt.get("attempt_no") or 0),
        str(attempt.get("id") or ""),
    )
