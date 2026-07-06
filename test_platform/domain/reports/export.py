from __future__ import annotations

from html import escape
from typing import Any

from test_platform.domain.canonical_json import canonical_json


def export_report_json(report: dict[str, Any]) -> str:
    return canonical_json(report)


def export_report_html(report: dict[str, Any]) -> str:
    provenance = report.get("provenance") if isinstance(report, dict) else {}
    gate = report.get("gate") if isinstance(report, dict) else {}
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
            f"<pre>{escape(canonical_json(report))}</pre>",
            "</body>",
            "</html>",
        ]
    )
