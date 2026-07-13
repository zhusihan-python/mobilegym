from __future__ import annotations

from collections import Counter
from typing import Any

from test_platform.domain.canonical_json import canonical_sha256
from test_platform.domain.diagnostics.classifier import classify_episode_attempt
from test_platform.domain.diagnostics.input import DiagnosticInput


DIAGNOSTICS_SCHEMA_VERSION = 2


def build_diagnostics(diagnostic_input: DiagnosticInput) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    for attempt in diagnostic_input.episode_attempts:
        record = classify_episode_attempt(attempt)
        if record is None:
            continue
        items.append(
            _with_id(
                {
                    **record,
                    "run_id": diagnostic_input.run_id,
                    "run_attempt_id": diagnostic_input.run_attempt_id,
                }
            )
        )

    for event in diagnostic_input.event_diagnostics:
        items.append(_with_event_id(_event_diagnostic(event)))

    comparison = diagnostic_input.comparison
    if comparison is not None:
        for pair in comparison.get("pairs") or []:
            pair_record = _comparison_pair_diagnostic(
                pair,
                run_id=diagnostic_input.run_id,
                run_attempt_id=diagnostic_input.run_attempt_id,
                comparison_id=comparison["id"],
            )
            if pair_record is not None:
                items.append(_with_id(pair_record))

    for gate_result in diagnostic_input.gate_results:
        gate_record = _gate_diagnostic(
            gate_result,
            run_id=diagnostic_input.run_id,
            run_attempt_id=diagnostic_input.run_attempt_id,
        )
        if gate_record is not None:
            items.append(_with_id(gate_record))

    items = sorted(
        items,
        key=lambda item: (
            item["entity_type"],
            item["code"],
            item.get("episode_attempt_id") or "",
            item.get("pair_key") or "",
            item.get("gate_result_id") or "",
            item["id"],
        ),
    )
    return {
        "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "run_id": diagnostic_input.run_id,
        "run_attempt_id": diagnostic_input.run_attempt_id,
        "input_hash": diagnostic_input.input_hash,
        "provenance": diagnostic_input.provenance,
        "summary": _summary(items),
        "items": items,
    }


def _comparison_pair_diagnostic(
    pair: dict[str, Any],
    *,
    run_id: str,
    run_attempt_id: str,
    comparison_id: str,
) -> dict[str, Any] | None:
    classification = str(pair.get("classification") or "")
    if classification not in _PAIR_CLASSIFICATION_SPECS:
        return None
    spec = _PAIR_CLASSIFICATION_SPECS[classification]
    return {
        "code": spec["code"],
        "category": "comparison",
        "phase": "compare",
        "severity": spec["severity"],
        "retryable": spec["retryable"],
        "message": f"{spec['code']} for {pair.get('pair_key') or 'pair'}.",
        "entity_type": "comparison_pair",
        "run_id": run_id,
        "run_attempt_id": run_attempt_id,
        "comparison_id": comparison_id,
        "comparison_pair_id": pair.get("id"),
        "pair_key": pair.get("pair_key"),
        "baseline_episode_attempt_id": pair.get("baseline_episode_attempt_id"),
        "candidate_episode_attempt_id": pair.get("candidate_episode_attempt_id"),
        "artifact_refs": _pair_artifact_refs(pair),
        "recommended_action": spec["recommended_action"],
        "raw": {
            "classification": classification,
            "integrity": pair.get("integrity"),
            "delta": pair.get("delta"),
        },
    }


def _event_diagnostic(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    code = str(payload.get("code") or "RUNNER_DIAGNOSTIC")
    phase = str(payload.get("phase") or "") or None
    spec = _EVENT_CODE_SPECS.get(code, _EVENT_CODE_SPECS["RUNNER_DIAGNOSTIC"])
    app_ids = payload.get("app_ids")
    app_ids = [str(value) for value in app_ids] if isinstance(app_ids, list) else []
    episode_id = event.get("episode_id")
    episode_attempt_id = event.get("episode_attempt_id")
    return {
        "source_event_id": event.get("source_event_id"),
        "code": code,
        "category": spec["category"],
        "phase": phase,
        "severity": str(payload.get("severity") or spec["severity"]),
        "retryable": bool(payload.get("retryable", spec["retryable"])),
        "message": str(payload.get("message") or code),
        "recommended_action": str(
            payload.get("recommended_action") or spec["recommended_action"]
        ),
        "entity_type": "diagnostic_event",
        "scope": "episode" if episode_attempt_id else "run",
        "run_id": event.get("run_id"),
        "run_attempt_id": event.get("run_attempt_id"),
        "run_attempt_no": event.get("run_attempt_no"),
        "lane_id": event.get("lane_id"),
        "lane_attempt_id": event.get("lane_attempt_id"),
        "lane_key": event.get("lane_key"),
        "target_id": event.get("target_id"),
        "episode_id": episode_id,
        "episode_attempt_id": episode_attempt_id,
        "episode_key": event.get("episode_key") or payload.get("episode_key"),
        "episode_attempt_no": event.get("episode_attempt_no"),
        "worker_id": event.get("worker_id"),
        "step": payload.get("step"),
        "task_id": payload.get("task_id"),
        "app_ids": app_ids,
        "artifact_refs": [],
        "raw": {},
    }


def _gate_diagnostic(
    gate_result: dict[str, Any],
    *,
    run_id: str,
    run_attempt_id: str,
) -> dict[str, Any] | None:
    verdict = str(gate_result.get("verdict") or "")
    if verdict not in {"failed", "error"}:
        return None
    code = "QUALITY_GATE_ERROR" if verdict == "error" else "QUALITY_GATE_FAILED"
    return {
        "code": code,
        "category": "gate",
        "phase": "gate",
        "severity": "error",
        "retryable": False,
        "message": f"Quality gate {verdict}.",
        "entity_type": "gate",
        "run_id": run_id,
        "run_attempt_id": run_attempt_id,
        "gate_result_id": gate_result.get("id"),
        "report_id": gate_result.get("report_id"),
        "artifact_refs": [],
        "recommended_action": "Inspect gate reasons and adjust the run or thresholds.",
        "raw": {
            "verdict": verdict,
            "thresholds": gate_result.get("thresholds"),
            "observed": gate_result.get("observed"),
            "reasons": gate_result.get("reasons"),
        },
    }


def _pair_artifact_refs(pair: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("baseline_attempt", "candidate_attempt"):
        attempt = pair.get(key)
        if isinstance(attempt, dict) and attempt.get("artifact_root"):
            refs.append(str(attempt["artifact_root"]))
    return refs


def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    by_category = Counter(item["category"] for item in items)
    by_severity = Counter(item["severity"] for item in items)
    return {
        "total": len(items),
        "by_category": dict(sorted(by_category.items())),
        "by_severity": dict(sorted(by_severity.items())),
    }


def _with_id(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "diag_" + canonical_sha256(record).removeprefix("sha256:"),
        **record,
    }


def _with_event_id(record: dict[str, Any]) -> dict[str, Any]:
    source_event_id = str(record.get("source_event_id") or "")
    return {
        "id": "diag_event_" + canonical_sha256(source_event_id).removeprefix("sha256:"),
        **record,
    }


_PAIR_CLASSIFICATION_SPECS: dict[str, dict[str, Any]] = {
    "candidate_error": {
        "code": "CANDIDATE_ERROR",
        "severity": "error",
        "retryable": True,
        "recommended_action": "Inspect candidate artifacts and retry if the failure is transient.",
    },
    "regression": {
        "code": "REGRESSION",
        "severity": "error",
        "retryable": False,
        "recommended_action": "Inspect pair deltas and candidate behavior.",
    },
    "fixed": {
        "code": "FIXED",
        "severity": "info",
        "retryable": False,
        "recommended_action": "No action required.",
    },
    "unpaired": {
        "code": "UNPAIRED",
        "severity": "warning",
        "retryable": False,
        "recommended_action": "Inspect pair materialization and task selection.",
    },
}


_EVENT_CODE_SPECS: dict[str, dict[str, Any]] = {
    "BROWSER_PAGE_ERROR": {
        "category": "page",
        "severity": "error",
        "retryable": False,
        "recommended_action": "Inspect the browser log and page error.",
    },
    "BROWSER_REQUEST_FAILED": {
        "category": "network",
        "severity": "error",
        "retryable": True,
        "recommended_action": "Inspect the browser log and retry the request.",
    },
    "BROWSER_HTTP_ERROR": {
        "category": "network",
        "severity": "error",
        "retryable": True,
        "recommended_action": "Inspect the HTTP response and browser log.",
    },
    "BROWSER_CONSOLE_ERROR": {
        "category": "browser",
        "severity": "error",
        "retryable": False,
        "recommended_action": "Inspect the browser console log.",
    },
    "RUNNER_DIAGNOSTIC": {
        "category": "runner",
        "severity": "error",
        "retryable": True,
        "recommended_action": "Inspect runner logs and retry if the failure is transient.",
    },
}
