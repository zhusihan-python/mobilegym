"""Streaming CSV loader for monitor artifacts.

Path-safe, size-bounded, and tolerant of malformed cells. Reads monitor.csv
via streaming ``csv.reader`` (never ``read_text()``), enforces byte/row/column
limits, and excludes malformed numeric cells per-cell (not per-row).
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Bounds.
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB per source
_MAX_ROWS = 10_000
_MAX_COLUMNS = 256
_MAX_FIELD_SIZE = 1024 * 1024  # 1 MB per CSV field

# Required header signature: must have ``timestamp`` plus at least one known
# metric column.
_KNOWN_METRIC_PREFIXES = (
    "load1", "load5", "mem_", "tcp_",
    "proc_", "gpu", "vllm_",
)


@dataclass(frozen=True)
class MonitorCsvResult:
    """Parsed monitor CSV data."""

    headers: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated_at_bytes: int | None
    truncated_at_rows: int | None
    truncated_at_columns: bool
    malformed_cells: int
    header_valid: bool
    duplicate_columns: list[str]
    status: str  # ok | truncated | empty | malformed_header | malformed_csv
    unknown_columns: list[str] = field(default_factory=list)


def load_monitor_csv(path: Path) -> MonitorCsvResult:
    """Load a monitor CSV file with bounds and per-cell malformed handling."""
    file_size = path.stat().st_size
    if file_size > _MAX_BYTES:
        return MonitorCsvResult(
            headers=[], rows=[], row_count=0,
            truncated_at_bytes=_MAX_BYTES, truncated_at_rows=None, truncated_at_columns=False,
            malformed_cells=0, header_valid=False,
            duplicate_columns=[], status="truncated",
        )

    csv.field_size_limit(_MAX_FIELD_SIZE)

    try:
        with open(path, "r", newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            try:
                raw_headers = next(reader)
            except StopIteration:
                return MonitorCsvResult(
                    headers=[], rows=[], row_count=0,
                    truncated_at_bytes=None, truncated_at_rows=None, truncated_at_columns=False,
                    malformed_cells=0, header_valid=False,
                    duplicate_columns=[], status="empty",
                )
            except csv.Error:
                return MonitorCsvResult(
                    headers=[], rows=[], row_count=0,
                    truncated_at_bytes=None, truncated_at_rows=None, truncated_at_columns=False,
                    malformed_cells=0, header_valid=False,
                    duplicate_columns=[], status="malformed_header",
                )

            # Detect duplicate columns and build deduplicated header.
            seen_cols: dict[str, int] = {}
            duplicates: list[str] = []
            headers: list[str] = []
            truncated_at_columns = False
            for col in raw_headers:
                if len(headers) >= _MAX_COLUMNS:
                    truncated_at_columns = True
                    break
                if col in seen_cols:
                    duplicates.append(col)
                else:
                    seen_cols[col] = len(headers)
                    headers.append(col)

            # Header signature validation: require timestamp + at least one
            # column recognized by the explicit metric registry (not just a
            # prefix match).
            from test_platform.domain.reports.infrastructure import (
                is_known_metric_column,
            )
            header_set = set(headers)
            has_timestamp = "timestamp" in header_set
            has_known_metric = any(
                is_known_metric_column(col) for col in header_set
            )
            header_valid = has_timestamp and has_known_metric
            # Duplicate columns or column truncation make header invalid.
            if duplicates or truncated_at_columns:
                header_valid = False
            if not header_valid:
                return MonitorCsvResult(
                    headers=headers, rows=[], row_count=0,
                    truncated_at_bytes=None, truncated_at_rows=None,
                    truncated_at_columns=truncated_at_columns,
                    malformed_cells=0, header_valid=False,
                    duplicate_columns=duplicates, status="malformed_header",
                )

            unknown = _find_unknown_columns(headers)

            # Classify columns: numeric metric vs metadata.
            from test_platform.domain.reports.infrastructure import (
                _classify_column, is_known_metric_column,
            )
            metadata_cols = {
                col for col in headers
                if col != "timestamp"
                and is_known_metric_column(col)
                and _classify_column(col) is None
            }

            # Stream data rows.
            truncated_at_bytes: int | None = None
            truncated_at_rows: int | None = None
            malformed_cells = 0
            csv_error_during_read = False
            rows: list[dict[str, Any]] = []
            bytes_read = len(",".join(raw_headers).encode("utf-8"))

            try:
                for raw_row in reader:
                    if len(rows) >= _MAX_ROWS:
                        truncated_at_rows = _MAX_ROWS
                        break
                    bytes_read += len(",".join(raw_row).encode("utf-8"))
                    if bytes_read > _MAX_BYTES:
                        truncated_at_bytes = _MAX_BYTES
                        break

                    row: dict[str, Any] = {}
                    for i, col in enumerate(headers):
                        if i >= len(raw_row):
                            continue
                        raw_val = raw_row[i].strip()
                        if col == "timestamp":
                            row[col] = raw_val
                            continue
                        if col in metadata_cols:
                            row[col] = raw_val
                            continue
                        parsed, was_malformed = _parse_numeric(raw_val)
                        if was_malformed and raw_val:
                            malformed_cells += 1
                        if parsed is not None:
                            row[col] = parsed
                        elif raw_val:
                            row[col] = raw_val
                        else:
                            row[col] = None
                    rows.append(row)
            except csv.Error:
                # Distinguish malformed CSV from normal truncation.
                csv_error_during_read = True

    except (OSError, PermissionError):
        return MonitorCsvResult(
            headers=[], rows=[], row_count=0,
            truncated_at_bytes=None, truncated_at_rows=None, truncated_at_columns=False,
            malformed_cells=0, header_valid=False,
            duplicate_columns=[], status="malformed_header",
        )

    # Determine final status.
    if csv_error_during_read:
        status = "malformed_csv"
    elif not rows and not truncated_at_bytes and not truncated_at_rows:
        status = "empty"
    elif truncated_at_bytes or truncated_at_rows:
        status = "truncated"
    else:
        status = "ok"

    return MonitorCsvResult(
        headers=headers, rows=rows, row_count=len(rows),
        truncated_at_bytes=truncated_at_bytes,
        truncated_at_rows=truncated_at_rows,
        truncated_at_columns=truncated_at_columns,
        malformed_cells=malformed_cells,
        header_valid=True,
        duplicate_columns=duplicates,
        status=status,
        unknown_columns=unknown,
    )


def _parse_numeric(value: str) -> tuple[float | None, bool]:
    """Parse a numeric value, rejecting NaN/Infinity."""
    if not value:
        return None, False
    try:
        f = float(value)
    except (ValueError, TypeError):
        return None, True
    if math.isnan(f) or math.isinf(f):
        return None, True
    return f, False


def _find_unknown_columns(headers: list[str]) -> list[str]:
    """Identify columns not in the known metric registry."""
    from test_platform.domain.reports.infrastructure import is_known_metric_column

    unknown = []
    for col in headers:
        if col == "timestamp":
            continue
        if col == "bench_episodes":
            continue
        if not is_known_metric_column(col):
            unknown.append(col)
    return unknown
