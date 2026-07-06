from __future__ import annotations

from test_platform.domain.reports.gates import evaluate_gates


def _report() -> dict:
    return {
        "functional": {
            "summary": {"success_rate": 0.8},
            "lanes": {
                "baseline": {"success_rate": 0.9},
                "candidate": {"success_rate": 0.7},
            },
        },
        "comparison": {
            "classification_counts": {
                "regressions": 2,
                "candidate_errors": 1,
            },
            "coverage": {"unpaired_pairs": 1},
            "runtime_s": {"percent_delta": 15.0},
        },
    }


def test_quality_gates_store_thresholds_observed_verdict_and_reasons():
    thresholds = {
        "max_regressions": 1,
        "max_candidate_errors": 1,
        "min_success_rate": 0.75,
        "max_success_rate_drop": 0.1,
        "max_runtime_p95_increase": 10.0,
        "max_unpaired": 0,
    }

    result = evaluate_gates(_report(), thresholds)

    assert result["verdict"] == "failed"
    assert result["thresholds"] == thresholds
    assert result["observed"] == {
        "max_regressions": 2,
        "max_candidate_errors": 1,
        "min_success_rate": 0.8,
        "max_success_rate_drop": 0.2,
        "max_runtime_p95_increase": 15.0,
        "max_unpaired": 1,
    }
    assert [reason["metric"] for reason in result["reasons"]] == [
        "max_regressions",
        "max_success_rate_drop",
        "max_runtime_p95_increase",
        "max_unpaired",
    ]


def test_missing_gate_metrics_produce_gate_error():
    result = evaluate_gates({"functional": {"summary": {}}}, {"min_success_rate": 0.9})

    assert result["verdict"] == "error"
    assert result["observed"] == {"min_success_rate": None}
    assert result["reasons"] == [
        {
            "metric": "min_success_rate",
            "reason": "metric_missing",
            "threshold": 0.9,
            "observed": None,
        }
    ]
