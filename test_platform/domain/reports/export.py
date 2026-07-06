from __future__ import annotations

from html import escape
from typing import Any

from test_platform.domain.canonical_json import canonical_json


def export_report_json(
    report: dict[str, Any],
    *,
    secret_values: list[str] | None = None,
) -> str:
    return canonical_json(_redact_secrets(report, secret_values or []))


def export_report_html(
    report: dict[str, Any],
    *,
    secret_values: list[str] | None = None,
) -> str:
    redacted = _redact_secrets(report, secret_values or [])
    provenance = redacted.get("provenance") if isinstance(redacted, dict) else {}
    gate = redacted.get("gate") if isinstance(redacted, dict) else {}
    verdict = gate.get("verdict") if isinstance(gate, dict) else None
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en">',
            "<head>",
            '<meta charset="utf-8">',
            "<title>Run Report</title>",
            "<style>",
            "body{font-family:system-ui,sans-serif;margin:32px;color:#111827;}",
            "pre{white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px;}",
            "</style>",
            "</head>",
            "<body>",
            "<h1>Run Report</h1>",
            f"<p>Gate verdict: {escape(str(verdict or 'not_evaluated'))}</p>",
            "<h2>Provenance</h2>",
            f"<pre>{escape(canonical_json(provenance if isinstance(provenance, dict) else {}))}</pre>",
            "<h2>Report JSON</h2>",
            f"<pre>{escape(canonical_json(redacted))}</pre>",
            "</body>",
            "</html>",
        ]
    )


def _redact_secrets(value: Any, secret_values: list[str]) -> Any:
    secrets = [secret for secret in secret_values if secret]
    if not secrets:
        return value
    if isinstance(value, dict):
        return {key: _redact_secrets(child, secrets) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact_secrets(child, secrets) for child in value]
    if isinstance(value, tuple):
        return [_redact_secrets(child, secrets) for child in value]
    if isinstance(value, str):
        redacted = value
        for secret in secrets:
            redacted = redacted.replace(secret, "[REDACTED_SECRET]")
        return redacted
    return value
