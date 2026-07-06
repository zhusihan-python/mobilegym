from __future__ import annotations

from test_platform.domain.retry_resume import (
    select_resume_lane_episodes,
    select_retry_lane_episodes,
)


def test_retry_selects_failed_and_error_lane_episodes_without_touching_passes():
    planned = [
        {"episode_key": "task::0", "lane_key": "candidate"},
        {"episode_key": "task::1", "lane_key": "candidate"},
        {"episode_key": "task::2", "lane_key": "candidate"},
    ]
    attempts = [
        {"episode_key": "task::0", "lane_key": "candidate", "outcome": "PASS"},
        {"episode_key": "task::1", "lane_key": "candidate", "outcome": "FAIL"},
        {"episode_key": "task::2", "lane_key": "candidate", "outcome": "ERROR"},
    ]

    selected = select_retry_lane_episodes(planned, attempts)

    assert selected == [
        {"episode_key": "task::1", "lane_key": "candidate", "reason": "retry_failed"},
        {"episode_key": "task::2", "lane_key": "candidate", "reason": "retry_error"},
    ]


def test_resume_selects_missing_and_service_restarted_but_skips_completed():
    planned = [
        {"episode_key": "task::0", "lane_key": "candidate"},
        {"episode_key": "task::1", "lane_key": "candidate"},
        {"episode_key": "task::2", "lane_key": "candidate"},
    ]
    attempts = [
        {"episode_key": "task::0", "lane_key": "candidate", "state": "completed", "outcome": "PASS", "error_code": None},
        {
            "episode_key": "task::2",
            "lane_key": "candidate",
            "state": "completed",
            "outcome": "ERROR",
            "error_code": "SERVICE_RESTARTED",
        },
    ]

    selected = select_resume_lane_episodes(planned, attempts)

    assert selected == [
        {"episode_key": "task::1", "lane_key": "candidate", "reason": "resume_missing"},
        {"episode_key": "task::2", "lane_key": "candidate", "reason": "resume_service_restarted"},
    ]
