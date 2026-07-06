from __future__ import annotations

from collections import defaultdict
from typing import Any

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.performance import (
    nearest_rank_percentile,
    percentage_delta,
)


CLASSIFICATION_KEYS = {
    "regression": "regressions",
    "fixed": "fixed",
    "stable_pass": "stable_pass",
    "stable_fail": "stable_fail",
    "baseline_error": "baseline_errors",
    "candidate_error": "candidate_errors",
    "pairing_violation": "pairing_violations",
    "unpaired": "unpaired",
}


def build_comparison_report(report_input: ReportInput) -> dict[str, Any]:
    comparison = report_input.comparison or {"pairs": []}
    pairs = comparison.get("pairs") if isinstance(comparison, dict) else []
    pairs = pairs if isinstance(pairs, list) else []
    attempts_by_id = {
        str(attempt["id"]): attempt for attempt in report_input.episode_attempts
    }
    counts = _classification_counts(pairs)
    pair_deltas, runtime_pairs, phase_pairs = _pair_deltas(pairs, attempts_by_id)

    return {
        "schema_version": 1,
        "input": {
            "run_id": report_input.run_id,
            "run_attempt_id": report_input.run_attempt_id,
            "input_hash": report_input.input_hash,
            "comparison_id": comparison.get("id") if isinstance(comparison, dict) else None,
        },
        "classification_counts": counts,
        "coverage": _coverage(pairs),
        "runtime_s": _aggregate_delta(runtime_pairs),
        "phases": {
            phase: _aggregate_delta(values)
            for phase, values in sorted(phase_pairs.items())
        },
        "pairs": [
            {
                "pair_key": str(pair.get("pair_key") or ""),
                "classification": str(pair.get("classification") or ""),
                "baseline_episode_attempt_id": pair.get("baseline_episode_attempt_id"),
                "candidate_episode_attempt_id": pair.get("candidate_episode_attempt_id"),
                "delta": pair_deltas.get(str(pair.get("pair_key") or ""), {}),
            }
            for pair in pairs
        ],
        "pair_deltas": pair_deltas,
    }


def _classification_counts(pairs: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "total_pairs": len(pairs),
        "regressions": 0,
        "fixed": 0,
        "stable_pass": 0,
        "stable_fail": 0,
        "baseline_errors": 0,
        "candidate_errors": 0,
        "pairing_violations": 0,
        "unpaired": 0,
    }
    for pair in pairs:
        key = CLASSIFICATION_KEYS.get(str(pair.get("classification") or ""))
        if key is not None:
            counts[key] += 1
    return counts


def _coverage(pairs: list[dict[str, Any]]) -> dict[str, float | int]:
    total = len(pairs)
    unpaired = sum(1 for pair in pairs if _is_unpaired(pair))
    paired = total - unpaired
    return {
        "total_pairs": total,
        "paired_pairs": paired,
        "unpaired_pairs": unpaired,
        "coverage_rate": 0.0 if total == 0 else paired / total,
    }


def _pair_deltas(
    pairs: list[dict[str, Any]],
    attempts_by_id: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    list[tuple[float, float]],
    dict[str, list[tuple[float, float]]],
]:
    pair_deltas: dict[str, dict[str, Any]] = {}
    runtime_pairs: list[tuple[float, float]] = []
    phase_pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)

    for pair in pairs:
        if _is_unpaired(pair):
            continue
        pair_key = str(pair.get("pair_key") or "")
        baseline = attempts_by_id.get(str(pair.get("baseline_episode_attempt_id")))
        candidate = attempts_by_id.get(str(pair.get("candidate_episode_attempt_id")))
        if baseline is None or candidate is None:
            continue

        pair_report: dict[str, Any] = {}
        baseline_runtime = _runtime_s(baseline)
        candidate_runtime = _runtime_s(candidate)
        if baseline_runtime is not None and candidate_runtime is not None:
            runtime_pairs.append((baseline_runtime, candidate_runtime))
            pair_report["runtime_s"] = percentage_delta(
                candidate=candidate_runtime,
                baseline=baseline_runtime,
            )

        baseline_phases = _stopwatch_flat(baseline)
        candidate_phases = _stopwatch_flat(candidate)
        phase_report: dict[str, Any] = {}
        for phase in sorted(set(baseline_phases) & set(candidate_phases)):
            baseline_phase = baseline_phases[phase]
            candidate_phase = candidate_phases[phase]
            phase_pairs[phase].append((baseline_phase, candidate_phase))
            phase_report[phase] = percentage_delta(
                candidate=candidate_phase,
                baseline=baseline_phase,
            )
        if phase_report:
            pair_report["phases"] = phase_report
        if pair_report:
            pair_deltas[pair_key] = pair_report

    return pair_deltas, runtime_pairs, phase_pairs


def _aggregate_delta(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    baseline_values = [baseline for baseline, _candidate in pairs]
    candidate_values = [candidate for _baseline, candidate in pairs]
    baseline_p95 = nearest_rank_percentile(baseline_values, 95)
    candidate_p95 = nearest_rank_percentile(candidate_values, 95)
    delta = (
        {"absolute": None, "percent": None}
        if baseline_p95 is None or candidate_p95 is None
        else percentage_delta(candidate=candidate_p95, baseline=baseline_p95)
    )
    return {
        "unit": "seconds",
        "sample_count": len(pairs),
        "baseline_p95": baseline_p95,
        "candidate_p95": candidate_p95,
        "absolute_delta": delta["absolute"],
        "percent_delta": delta["percent"],
    }


def _is_unpaired(pair: dict[str, Any]) -> bool:
    return (
        str(pair.get("classification") or "") == "unpaired"
        or pair.get("baseline_episode_attempt_id") is None
        or pair.get("candidate_episode_attempt_id") is None
    )


def _runtime_s(attempt: dict[str, Any]) -> float | None:
    return _parse_float(_execution(attempt).get("runtime_s"))


def _stopwatch_flat(attempt: dict[str, Any]) -> dict[str, float]:
    raw = _execution(attempt).get("stopwatch_flat")
    if not isinstance(raw, dict):
        return {}
    parsed: dict[str, float] = {}
    for key, value in raw.items():
        sample = _parse_float(value)
        if sample is not None:
            parsed[str(key)] = sample
    return parsed


def _execution(attempt: dict[str, Any]) -> dict[str, Any]:
    result = attempt.get("result_json")
    if not isinstance(result, dict):
        return {}
    execution = result.get("execution")
    return execution if isinstance(execution, dict) else {}


def _parse_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
