from __future__ import annotations

from typing import Any


def classify_episode_attempt(attempt: dict[str, Any]) -> dict[str, Any] | None:
    """Classify one terminal episode attempt into a diagnostic record.

    PASS/CANCELLED attempts do not produce diagnostics. FAIL is kept separate
    from ERROR: it is an assertion diagnostic, not an execution failure.
    """

    outcome = str(attempt.get("outcome") or "").upper()
    if outcome in {"", "PASS", "SUCCESS", "CANCELLED", "SKIPPED"}:
        return None

    result = attempt.get("result_json")
    result = result if isinstance(result, dict) else {}
    execution = result.get("execution")
    execution = execution if isinstance(execution, dict) else {}
    error_code = str(attempt.get("error_code") or "")
    stop_reason = str(execution.get("stop_reason") or "")
    error_message = str(execution.get("error") or "")
    code = _diagnostic_code(outcome, error_code, stop_reason, error_message)
    spec = _CODE_SPECS.get(code, _CODE_SPECS["EXECUTION_ERROR"])

    return {
        "code": code,
        "category": spec["category"],
        "phase": spec["phase"],
        "retryable": bool(spec["retryable"]),
        "severity": spec["severity"],
        "message": _message(code, attempt, error_message),
        "entity_type": "episode_attempt",
        "episode_attempt_id": attempt.get("id"),
        "episode_id": attempt.get("episode_id"),
        "episode_key": attempt.get("episode_key"),
        "lane_attempt_id": attempt.get("lane_attempt_id"),
        "lane_key": attempt.get("lane_key"),
        "raw": {
            "outcome": attempt.get("outcome"),
            "error_code": attempt.get("error_code"),
            "stop_reason": stop_reason or None,
        },
        "artifact_refs": _artifact_refs(attempt),
        "recommended_action": spec["recommended_action"],
    }


def _diagnostic_code(
    outcome: str,
    error_code: str,
    stop_reason: str,
    error_message: str,
) -> str:
    if error_code in _CODE_SPECS and error_code != "EXECUTION_ERROR":
        return error_code
    if "judge_error" in error_message:
        return "JUDGE_ERROR"
    if stop_reason in _STOP_REASON_CODES:
        return _STOP_REASON_CODES[stop_reason]
    if outcome == "FAIL":
        return "ASSERTION_FAILURE"
    return "EXECUTION_ERROR"


def _message(code: str, attempt: dict[str, Any], raw_error: str) -> str:
    if raw_error:
        return raw_error
    episode_key = attempt.get("episode_key") or attempt.get("episode_id") or "episode"
    return f"{code} in {episode_key}."


def _artifact_refs(attempt: dict[str, Any]) -> list[str]:
    artifact_root = attempt.get("artifact_root")
    if not artifact_root:
        return []
    return [str(artifact_root)]


_STOP_REASON_CODES = {
    "MAX_STEPS": "MAX_STEPS",
    "FORMAT_ERROR": "ACTION_FORMAT_ERROR",
    "REPETITIVE_LOOP": "REPETITIVE_LOOP",
    "PAIRING_VIOLATION": "PAIRING_VIOLATION",
}


_CODE_SPECS: dict[str, dict[str, Any]] = {
    "ASSERTION_FAILURE": {
        "category": "assertion",
        "phase": "judge",
        "retryable": False,
        "severity": "warning",
        "recommended_action": "Inspect the task state and judge evidence.",
    },
    "MAX_STEPS": {
        "category": "agent",
        "phase": "agent.control",
        "retryable": True,
        "severity": "error",
        "recommended_action": "Inspect the trajectory and consider a retry or step budget change.",
    },
    "ACTION_FORMAT_ERROR": {
        "category": "agent",
        "phase": "agent.action",
        "retryable": True,
        "severity": "error",
        "recommended_action": "Inspect the agent action payload.",
    },
    "REPETITIVE_LOOP": {
        "category": "agent",
        "phase": "agent.control",
        "retryable": True,
        "severity": "error",
        "recommended_action": "Inspect recent trajectory steps for looping.",
    },
    "JUDGE_ERROR": {
        "category": "judge",
        "phase": "judge",
        "retryable": True,
        "severity": "error",
        "recommended_action": "Inspect judge logs and rerun evaluation.",
    },
    "PAIRING_VIOLATION": {
        "category": "comparison",
        "phase": "compare.integrity",
        "retryable": False,
        "severity": "error",
        "recommended_action": "Inspect pair integrity diffs and frozen target metadata.",
    },
    "WORKER_CRASH": {
        "category": "infra",
        "phase": "execute.worker",
        "retryable": True,
        "severity": "error",
        "recommended_action": "Inspect worker logs and retry the lane.",
    },
    "EXECUTION_ERROR": {
        "category": "execution",
        "phase": "execute",
        "retryable": True,
        "severity": "error",
        "recommended_action": "Inspect execution logs and artifacts.",
    },
}
