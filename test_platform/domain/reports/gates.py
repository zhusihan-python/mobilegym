from __future__ import annotations

from typing import Any, Callable


MetricResolver = Callable[[dict[str, Any]], Any]


def evaluate_gates(report: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    observed: dict[str, Any] = {}
    reasons: list[dict[str, Any]] = []
    has_missing = False

    for metric, threshold in thresholds.items():
        resolver = _RESOLVERS.get(metric)
        value = None if resolver is None else resolver(report)
        value = _stable_number(value)
        observed[metric] = value
        if value is None:
            has_missing = True
            reasons.append(
                {
                    "metric": metric,
                    "reason": "metric_missing",
                    "threshold": threshold,
                    "observed": None,
                }
            )
            continue
        if _violates(metric, value, threshold):
            reasons.append(
                {
                    "metric": metric,
                    "reason": "threshold_exceeded"
                    if metric.startswith("max_")
                    else "threshold_not_met",
                    "threshold": threshold,
                    "observed": value,
                }
            )

    verdict = "error" if has_missing else "failed" if reasons else "passed"
    return {
        "schema_version": 1,
        "verdict": verdict,
        "thresholds": dict(thresholds),
        "observed": observed,
        "reasons": reasons,
    }


def _success_rate_drop(report: dict[str, Any]) -> float | None:
    lanes = _get(report, "functional", "lanes")
    if not isinstance(lanes, dict):
        return None
    baseline = _get(lanes, "baseline", "success_rate")
    candidate = _get(lanes, "candidate", "success_rate")
    if baseline is None or candidate is None:
        return None
    return float(baseline) - float(candidate)


def _get(value: Any, *path: str) -> Any:
    current = value
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _stable_number(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 12)
    try:
        return round(float(value), 12)
    except (TypeError, ValueError):
        return None


def _violates(metric: str, observed: Any, threshold: Any) -> bool:
    try:
        observed_value = float(observed)
        threshold_value = float(threshold)
    except (TypeError, ValueError):
        return True
    if metric.startswith("min_"):
        return observed_value < threshold_value
    return observed_value > threshold_value


_RESOLVERS: dict[str, MetricResolver] = {
    "max_regressions": lambda report: _get(
        report, "comparison", "classification_counts", "regressions"
    ),
    "max_candidate_errors": lambda report: _get(
        report, "comparison", "classification_counts", "candidate_errors"
    ),
    "min_success_rate": lambda report: _get(
        report, "functional", "summary", "success_rate"
    ),
    "max_success_rate_drop": _success_rate_drop,
    "max_runtime_p95_increase": lambda report: _get(
        report, "comparison", "runtime_s", "percent_delta"
    ),
    "max_unpaired": lambda report: _get(
        report, "comparison", "coverage", "unpaired_pairs"
    ),
}
