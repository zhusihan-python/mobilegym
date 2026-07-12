"""Reliability report section: Pass@k and flakiness by materialization identity.

Groups repeated trials by ``(lane_key, materialization_key)`` — the combination
of lane identity (so paired baseline/candidate lanes are never mixed) and the
stable materialization identity (constant across ``trial_id``). Reuses the
benchmark framework's canonical unbiased Pass@k estimator, and annotates
flakiness only when sufficient evidence exists.

The planned trial set is taken from ``planned_lane_episodes`` (the authoritative
universe), not from episode_attempts (which omits missing trials and may contain
multiple attempt_no rows per planned trial). Each planned row already carries
the latest selected attempt's outcome.
"""

from __future__ import annotations

from typing import Any

from bench_env.metrics import _compute_single_pass_k

from test_platform.domain.reports.input import ReportInput

_PASS_K_VALUES = [1, 2, 5]

# Outcomes that count as functional success (matching the existing
# functional/sequence report contract).
_SUCCESS_OUTCOMES = frozenset({"PASS", "SUCCESS"})


def build_reliability_report(report_input: ReportInput) -> dict[str, Any]:
    """Build the reliability section from canonical report input.

    Trials are grouped by ``(lane_key, materialization_key)``. The planned
    trial set comes from ``planned_lane_episodes``; valid trials (success or
    functional failure) contribute to the Pass@k denominator.
    """
    planned = report_input.planned_lane_episodes

    # Group planned rows by (lane_key, materialization_key).
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in planned:
        lane_key = str(item.get("lane_key") or "")
        mat_key = str(item.get("materialization_key") or item.get("episode_key") or "")
        groups.setdefault((lane_key, mat_key), []).append(item)

    tasks: list[dict[str, Any]] = []
    for (lane_key, mat_key) in sorted(groups):
        rows = groups[(lane_key, mat_key)]
        counts = _count_trials(rows)
        pass_at_k: dict[str, float | None] = {}
        for k in _PASS_K_VALUES:
            if counts["valid"] == 0:
                pass_at_k[str(k)] = None
            else:
                pass_at_k[str(k)] = round(
                    _compute_single_pass_k(counts["valid"], counts["success"], k), 4
                )
        flakiness = _classify_flakiness(counts)
        tasks.append({
            "lane_key": lane_key,
            "materialization_key": mat_key,
            "task_id": _extract_task_id(rows),
            "counts": counts,
            "pass_at_k": pass_at_k,
            "flakiness": flakiness,
        })

    summary = _build_summary(tasks)

    return {
        "schema_version": 1,
        "input": {
            "run_id": report_input.run_id,
            "run_attempt_id": report_input.run_attempt_id,
            "input_hash": report_input.input_hash,
        },
        "summary": summary,
        "tasks": tasks,
        "pass_k_values": _PASS_K_VALUES,
    }


def _count_trials(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count planned, attempted, valid, success, failure, error, cancelled, missing.

    Each row is a planned_lane_episodes entry carrying the latest selected
    attempt's outcome (or None if no attempt exists).
    """
    planned = len(rows)
    success = 0
    failure = 0
    error = 0
    cancelled = 0
    missing = 0

    for row in rows:
        outcome = str(row.get("outcome") or "").upper()
        status = str(row.get("status") or "").lower()
        if outcome in _SUCCESS_OUTCOMES:
            success += 1
        elif outcome == "FAIL":
            failure += 1
        elif outcome == "ERROR":
            error += 1
        elif outcome == "CANCELLED" or status == "cancelled":
            cancelled += 1
        elif status == "incomplete" or not outcome:
            missing += 1
        else:
            # Unknown outcome — treat as missing (no valid result).
            missing += 1

    valid = success + failure
    attempted = planned - missing
    return {
        "planned": planned,
        "attempted": attempted,
        "valid": valid,
        "success": success,
        "failure": failure,
        "error": error,
        "cancelled": cancelled,
        "missing": missing,
    }


def _classify_flakiness(counts: dict[str, int]) -> str | None:
    """Classify flakiness based on valid trial evidence.

    - ``flaky``: >=2 valid trials with both success and failure.
    - ``stable``: >=2 valid trials, all same outcome.
    - ``insufficient_trials``: <2 valid trials.
    - ``None``: no valid trials at all.
    """
    if counts["valid"] < 2:
        if counts["valid"] == 0:
            return None
        return "insufficient_trials"
    if counts["success"] > 0 and counts["failure"] > 0:
        return "flaky"
    return "stable"


def _extract_task_id(rows: list[dict[str, Any]]) -> str:
    """Extract a task_id from the first row's episode_key."""
    for row in rows:
        episode_key = str(row.get("episode_key") or "")
        if episode_key:
            return episode_key.split("|")[0]
    return "unknown"


def _build_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(tasks)
    pass_at_1_values = [
        t["pass_at_k"]["1"]
        for t in tasks
        if t["pass_at_k"].get("1") is not None
    ]
    flaky = sum(1 for t in tasks if t["flakiness"] == "flaky")
    insufficient = sum(1 for t in tasks if t["flakiness"] == "insufficient_trials")
    return {
        "total_materializations": total,
        "pass_at_1": (
            round(sum(pass_at_1_values) / len(pass_at_1_values), 4)
            if pass_at_1_values
            else None
        ),
        "flaky_tasks": flaky,
        "insufficient_trials_tasks": insufficient,
    }
