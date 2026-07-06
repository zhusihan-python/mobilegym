from __future__ import annotations

from test_platform.domain.diagnostics.classifier import classify_episode_attempt


def _attempt(
    *,
    outcome: str | None,
    error_code: str | None = None,
    execution: dict | None = None,
    is_error: bool | None = None,
) -> dict:
    result_json = {
        "is_error": is_error if is_error is not None else outcome == "ERROR",
        "execution": execution or {},
    }
    return {
        "id": "ea1",
        "episode_id": "ep1",
        "episode_key": "task|t0",
        "lane_key": "candidate",
        "lane_attempt_id": "la1",
        "outcome": outcome,
        "error_code": error_code,
        "result_json": result_json,
        "artifact_root": "lanes/candidate/trajectory/task_t0",
    }


def test_fail_classifies_as_assertion_not_execution_error():
    diagnostic = classify_episode_attempt(_attempt(outcome="FAIL", error_code="ASSERTION_FAILURE"))

    assert diagnostic["code"] == "ASSERTION_FAILURE"
    assert diagnostic["category"] == "assertion"
    assert diagnostic["phase"] == "judge"
    assert diagnostic["retryable"] is False
    assert diagnostic["severity"] == "warning"


def test_stop_reason_and_error_code_drive_stable_error_codes():
    max_steps = classify_episode_attempt(
        _attempt(outcome="FAIL", execution={"stop_reason": "MAX_STEPS"})
    )
    judge_error = classify_episode_attempt(
        _attempt(
            outcome="ERROR",
            error_code="EXECUTION_ERROR",
            execution={"error": "judge_error: RuntimeError: boom", "stop_reason": "ERROR"},
        )
    )
    pairing = classify_episode_attempt(
        _attempt(outcome="ERROR", error_code="PAIRING_VIOLATION", execution={"stop_reason": "PAIRING_VIOLATION"})
    )
    crash = classify_episode_attempt(
        _attempt(outcome="ERROR", error_code="WORKER_CRASH", execution={"stop_reason": "ERROR"})
    )

    assert max_steps["code"] == "MAX_STEPS"
    assert max_steps["category"] == "agent"
    assert judge_error["code"] == "JUDGE_ERROR"
    assert judge_error["category"] == "judge"
    assert pairing["code"] == "PAIRING_VIOLATION"
    assert pairing["category"] == "comparison"
    assert crash["code"] == "WORKER_CRASH"
    assert crash["category"] == "infra"
    assert crash["retryable"] is True
