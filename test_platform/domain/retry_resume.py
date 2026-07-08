from __future__ import annotations

from typing import Any


def select_retry_lane_episodes(
    planned_lane_episodes: list[dict[str, Any]],
    latest_attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attempts = _attempts_by_lane_episode(latest_attempts)
    selected: list[dict[str, Any]] = []
    for planned in planned_lane_episodes:
        key = _lane_episode_tuple(planned)
        attempt = attempts.get(key)
        if attempt is None:
            continue
        outcome = str(attempt.get("outcome") or "").upper()
        if outcome == "FAIL":
            selected.append({**_lane_episode_output(planned), "reason": "retry_failed"})
        elif outcome == "ERROR":
            selected.append({**_lane_episode_output(planned), "reason": "retry_error"})
    return selected


def select_resume_lane_episodes(
    planned_lane_episodes: list[dict[str, Any]],
    latest_attempts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    attempts = _attempts_by_lane_episode(latest_attempts)
    selected: list[dict[str, Any]] = []
    for planned in planned_lane_episodes:
        key = _lane_episode_tuple(planned)
        attempt = attempts.get(key)
        if attempt is None:
            selected.append({**_lane_episode_output(planned), "reason": "resume_missing"})
            continue
        if str(attempt.get("error_code") or "").upper() == "SERVICE_RESTARTED":
            selected.append({**_lane_episode_output(planned), "reason": "resume_service_restarted"})
    return selected


def _attempts_by_lane_episode(
    attempts: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (str(attempt["episode_key"]), str(attempt["lane_key"])): attempt
        for attempt in attempts
    }


def _lane_episode_tuple(value: dict[str, Any]) -> tuple[str, str]:
    return (str(value["episode_key"]), str(value["lane_key"]))


def _lane_episode_output(value: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {
        "episode_key": str(value["episode_key"]),
        "lane_key": str(value["lane_key"]),
    }
    if value.get("sequence_group_id") is not None:
        output["sequence_index"] = value.get("sequence_index")
        output["sequence_group_id"] = str(value["sequence_group_id"])
    return output
