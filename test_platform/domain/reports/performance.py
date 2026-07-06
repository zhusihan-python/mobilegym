from __future__ import annotations

from collections import defaultdict
from math import ceil
from typing import Any

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.selection import select_attempts


PERCENTILES = (50, 75, 90, 95, 99)


def nearest_rank_percentile(values: list[float | int], percentile: int) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = ceil((percentile / 100) * len(ordered))
    index = max(0, min(len(ordered) - 1, rank - 1))
    return ordered[index]


def percentage_delta(*, candidate: float, baseline: float) -> dict[str, float | None]:
    absolute = candidate - baseline
    percent = None if baseline == 0 else (absolute / baseline) * 100
    return {"absolute": absolute, "percent": percent}


def build_performance_report(report_input: ReportInput) -> dict[str, Any]:
    selected = select_attempts(report_input)
    summary_acc = _new_accumulator()
    lane_accumulators: dict[str, dict[str, Any]] = defaultdict(_new_accumulator)

    for planned in report_input.planned_lane_episodes:
        lane_key = str(planned.get("lane_key") or "unknown")
        attempt = selected.get(
            (
                str(planned.get("episode_id")),
                str(planned.get("lane_attempt_id")),
            )
        )
        _collect_sample(summary_acc, planned, attempt)
        _collect_sample(lane_accumulators[lane_key], planned, attempt)

    return {
        "schema_version": 1,
        "input": {
            "run_id": report_input.run_id,
            "run_attempt_id": report_input.run_attempt_id,
            "input_hash": report_input.input_hash,
        },
        "summary": _finalize_accumulator(summary_acc),
        "lanes": {
            key: _finalize_accumulator(value)
            for key, value in sorted(lane_accumulators.items())
        },
    }


def _new_accumulator() -> dict[str, Any]:
    return {
        "runtime_samples": [],
        "phase_samples": defaultdict(list),
        "excluded": {
            "incomplete": 0,
            "cancelled": 0,
            "errors": 0,
            "non_numeric_runtime": 0,
        },
    }


def _collect_sample(
    accumulator: dict[str, Any],
    planned: dict[str, Any],
    attempt: dict[str, Any] | None,
) -> None:
    if str(planned.get("status") or "").casefold() == "cancelled":
        accumulator["excluded"]["cancelled"] += 1
        return
    if attempt is None:
        accumulator["excluded"]["incomplete"] += 1
        return
    if _is_error(attempt):
        accumulator["excluded"]["errors"] += 1
        return

    execution = _execution(attempt)
    runtime = _parse_float(execution.get("runtime_s"))
    if runtime is None:
        accumulator["excluded"]["non_numeric_runtime"] += 1
    else:
        accumulator["runtime_samples"].append(runtime)

    stopwatch_flat = execution.get("stopwatch_flat")
    if isinstance(stopwatch_flat, dict):
        for phase, value in stopwatch_flat.items():
            sample = _parse_float(value)
            if sample is not None:
                accumulator["phase_samples"][str(phase)].append(sample)


def _finalize_accumulator(accumulator: dict[str, Any]) -> dict[str, Any]:
    return {
        "unit": "seconds",
        "runtime_s": _distribution(accumulator["runtime_samples"]),
        "phases": {
            phase: _distribution(values)
            for phase, values in sorted(accumulator["phase_samples"].items())
        },
        "excluded": dict(accumulator["excluded"]),
    }


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    distribution: dict[str, float | int | None] = {"sample_count": len(values)}
    for percentile in PERCENTILES:
        distribution[f"p{percentile}"] = nearest_rank_percentile(values, percentile)
    return distribution


def _execution(attempt: dict[str, Any]) -> dict[str, Any]:
    result = attempt.get("result_json")
    if not isinstance(result, dict):
        return {}
    execution = result.get("execution")
    return execution if isinstance(execution, dict) else {}


def _is_error(attempt: dict[str, Any]) -> bool:
    result = attempt.get("result_json")
    if isinstance(result, dict) and result.get("is_error") is not None:
        return bool(result.get("is_error"))
    return str(attempt.get("outcome") or "").upper() == "ERROR"


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
