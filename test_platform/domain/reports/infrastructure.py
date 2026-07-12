"""Infrastructure report section: host/process/GPU/TCP/model-server distributions.

Consumes parsed monitor CSV data (per-lane sources), computes per-dimension
distributions using nearest-rank percentiles, and reports availability status
with structured reasons.

Infrastructure metrics are NEVER mixed with agent/simulator execution phases
(the ``performance`` report section handles those).
"""

from __future__ import annotations

import re
from typing import Any

from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.monitor_csv import MonitorCsvResult
from test_platform.domain.reports.performance import nearest_rank_percentile

_PERCENTILES = (50, 75, 90, 95, 99)

# Dimension names.
_HOST = "host"
_PROCESS = "process"
_GPU = "gpu"
_TCP = "tcp"
_MODEL_SERVER = "model_server"

_ALL_DIMENSIONS = (_HOST, _PROCESS, _GPU, _TCP, _MODEL_SERVER)

# Allowlisted vLLM columns (from bench_env/monitor._VLLM_CSV_COLUMNS).
_VLLM_ALLOWLIST = frozenset({
    "running", "waiting", "kv_cache_pct",
    "prompt_tps", "gen_tps", "req_per_s",
    "avg_e2e_s", "avg_ttft_s", "avg_queue_s",
    "prefix_hit_pct",
    "prompt_tokens_total", "gen_tokens_total",
    "requests_total", "n_servers",
})


def is_known_metric_column(col: str) -> bool:
    """Check if a CSV column name is a recognized monitor metric."""
    if col in ("timestamp", "bench_episodes"):
        return False
    # gpu*_procs is metadata, not a metric — but still "known".
    if re.match(r"^gpu\d+_procs$", col):
        return True
    # Host metrics.
    if col in ("load1", "load5", "mem_used_gb", "mem_pct"):
        return True
    # TCP metrics.
    if col in ("tcp_established", "tcp_time_wait", "tcp_close_wait"):
        return True
    # Process metrics: proc_<group>_count / proc_<group>_rss_mb.
    if re.match(r"^proc_[a-z_]+_(count|rss_mb)$", col):
        return True
    # GPU metrics: gpu<n>_util / gpu<n>_mem_used_mb / gpu<n>_temp.
    if re.match(r"^gpu\d+_(util|mem_used_mb|temp)$", col):
        return True
    # vLLM aggregate metrics.
    if col.startswith("vllm_"):
        suffix = col[len("vllm_"):]
        if suffix in _VLLM_ALLOWLIST:
            return True
        if re.match(r"^s\d+_", suffix):
            sub = suffix.split("_", 1)[1]
            return sub in _VLLM_ALLOWLIST
    return False


def _classify_column(col: str) -> tuple[str, str, str] | None:
    """Map a CSV column to (dimension, metric_name, unit).

    Returns None for non-metric columns (timestamp, gpu_procs, bench_episodes).
    """
    if col == "timestamp" or col == "bench_episodes":
        return None
    if re.match(r"^gpu\d+_procs$", col):
        return None  # metadata
    if col == "load1":
        return (_HOST, "load_1m", "load")
    if col == "load5":
        return (_HOST, "load_5m", "load")
    if col == "mem_used_gb":
        return (_HOST, "memory_used_gib", "GiB")
    if col == "mem_pct":
        return (_HOST, "memory_used_pct", "percent")
    if col == "tcp_established":
        return (_TCP, "established", "connections")
    if col == "tcp_time_wait":
        return (_TCP, "time_wait", "connections")
    if col == "tcp_close_wait":
        return (_TCP, "close_wait", "connections")
    m = re.match(r"^proc_([a-z_]+)_rss_mb$", col)
    if m:
        return (_PROCESS, f"{m.group(1)}.rss_mib", "MiB")
    m = re.match(r"^proc_([a-z_]+)_count$", col)
    if m:
        return (_PROCESS, f"{m.group(1)}.count", "count")
    m = re.match(r"^gpu(\d+)_util$", col)
    if m:
        return (_GPU, f"{m.group(1)}.utilization", "percent")
    m = re.match(r"^gpu(\d+)_mem_used_mb$", col)
    if m:
        return (_GPU, f"{m.group(1)}.memory_used_mib", "MiB")
    m = re.match(r"^gpu(\d+)_temp$", col)
    if m:
        return (_GPU, f"{m.group(1)}.temperature", "celsius")
    if col.startswith("vllm_"):
        suffix = col[len("vllm_"):]
        if suffix in _VLLM_ALLOWLIST:
            return (_MODEL_SERVER, f"aggregate.{suffix}", _vllm_unit(suffix))
        m = re.match(r"^s(\d+)_(.+)$", suffix)
        if m and m.group(2) in _VLLM_ALLOWLIST:
            return (_MODEL_SERVER, f"{m.group(1)}.{m.group(2)}", _vllm_unit(m.group(2)))
    return None


def _vllm_unit(metric: str) -> str:
    """Return the unit for a vLLM metric."""
    if metric in ("prompt_tps", "gen_tps"):
        return "tokens/s"
    if metric == "req_per_s":
        return "requests/s"
    if metric in ("avg_e2e_s", "avg_ttft_s", "avg_queue_s"):
        return "seconds"
    if metric in ("kv_cache_pct", "prefix_hit_pct"):
        return "percent"
    if metric in ("running", "waiting"):
        return "count"
    if metric in ("prompt_tokens_total", "gen_tokens_total"):
        return "tokens"
    if metric == "requests_total":
        return "requests"
    if metric == "n_servers":
        return "count"
    return "count"


def _compute_duration(start_ts: str, end_ts: str) -> float | None:
    """Compute duration in seconds from two timestamp strings.

    Handles both numeric timestamps (test fixtures) and ISO-8601
    (the real ``bench_env.monitor`` producer format).
    """
    # Try numeric first.
    try:
        return round(float(end_ts) - float(start_ts), 2)
    except (ValueError, TypeError):
        pass
    # Try ISO-8601.
    try:
        from datetime import datetime

        fmts = [
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
        ]
        start_dt = None
        end_dt = None
        for fmt in fmts:
            if start_dt is None:
                try:
                    start_dt = datetime.strptime(start_ts, fmt)
                except ValueError:
                    pass
            if end_dt is None:
                try:
                    end_dt = datetime.strptime(end_ts, fmt)
                except ValueError:
                    pass
        if start_dt and end_dt:
            return round((end_dt - start_dt).total_seconds(), 2)
    except Exception:
        pass
    return None


def build_infrastructure_report(report_input: ReportInput) -> dict[str, Any]:
    """Build the infrastructure report section.

    Output is lane-scoped (per-source), with no cross-lane percentile merging.
    """
    monitor_sources = report_input.monitor_sources

    if not monitor_sources:
        return {
            "schema_version": 1,
            "input": {
                "run_id": report_input.run_id,
                "run_attempt_id": report_input.run_attempt_id,
                "input_hash": report_input.input_hash,
            },
            "available": False,
            "reason": "no_monitor_artifact",
            "sources": [],
            "scan_truncated_lanes": [],
            "discovered_source_count": 0,
            "accepted_source_count": 0,
            "excluded_source_count": 0,
            "unavailable_collectors": list(_ALL_DIMENSIONS),
            "partially_unavailable_collectors": [],
        }

    source_reports: list[dict[str, Any]] = []
    for src in monitor_sources:
        # Skip metadata-only entries (no csv_result — used for overflow/bounds).
        if src.get("csv_result") is None and "_scan_truncated_lanes" in src:
            continue
        source_reports.append(_build_source_report(src, report_input.run_id))

    # Aggregate availability.
    any_valid = any(s.get("available", False) for s in source_reports)

    # Unavailable collectors: intersection (all sources lack it).
    per_source_unavailable = [
        set(s.get("unavailable_collectors", [])) for s in source_reports
    ]
    if per_source_unavailable:
        all_unavailable = set.intersection(*per_source_unavailable) if per_source_unavailable else set()
        any_unavailable = set.union(*per_source_unavailable)
        partially = sorted(any_unavailable - all_unavailable)
        all_unavail = sorted(all_unavailable)
    else:
        # No source reports (e.g., metadata-only entry from scan overflow) →
        # all collectors are unknown/unavailable.
        all_unavail = list(_ALL_DIMENSIONS)
        partially = []

    # Collect scan_truncated_lanes from sources that carry it.
    scan_truncated = []
    discovered_count = 0
    accepted_count = 0
    excluded_source_count = 0
    for src in monitor_sources:
        if src.get("_scan_truncated_lanes"):
            scan_truncated.extend(src["_scan_truncated_lanes"])
        discovered_count = max(discovered_count, src.get("_discovered_count", 0))
        accepted_count = max(accepted_count, src.get("_accepted_count", 0))
        excluded_source_count = max(excluded_source_count, src.get("_excluded_source_count", 0))
    scan_truncated = sorted(set(scan_truncated))

    # Determine the reason when not available.
    reason = None
    if not any_valid:
        if not source_reports:
            if scan_truncated:
                reason = "scan_overflow"
            elif excluded_source_count > 0:
                reason = "source_limit_exceeded"
            else:
                reason = "no_monitor_artifact"
        else:
            reason = "no_valid_samples"

    return {
        "schema_version": 1,
        "input": {
            "run_id": report_input.run_id,
            "run_attempt_id": report_input.run_attempt_id,
            "input_hash": report_input.input_hash,
        },
        "available": any_valid,
        "reason": reason,
        "sources": source_reports,
        "scan_truncated_lanes": scan_truncated,
        "discovered_source_count": discovered_count,
        "accepted_source_count": accepted_count,
        "excluded_source_count": excluded_source_count,
        "unavailable_collectors": all_unavail,
        "partially_unavailable_collectors": partially,
    }


def _build_source_report(src: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Build a per-source infrastructure report from parsed CSV data."""
    from test_platform.domain.canonical_json import canonical_sha256

    lane_key = str(src.get("lane_key") or "")
    relative_path = str(src.get("relative_path") or "")
    artifact_id = (
        "artifact_"
        + canonical_sha256({"run_id": run_id, "relative_path": relative_path}).removeprefix("sha256:")
    )

    csv_result_data = src.get("csv_result")
    if csv_result_data is None:
        return {
            "lane_key": lane_key,
            "relative_path": relative_path,
            "artifact_id": artifact_id,
            "format_version": "bench_env.monitor.csv.v1",
            "available": False,
            "status": "unreadable",
            "reason": "csv_parse_failed",
            "dimensions": {},
            "unavailable_collectors": list(_ALL_DIMENSIONS),
            "excluded": {},
            "sample_window": None,
        }

    # Reconstruct from serialized dict.
    headers = csv_result_data.get("headers", [])
    rows = csv_result_data.get("rows", [])
    row_count = csv_result_data.get("row_count", 0)
    truncated_bytes = csv_result_data.get("truncated_at_bytes")
    truncated_rows = csv_result_data.get("truncated_at_rows")
    truncated_columns = csv_result_data.get("truncated_at_columns", False)
    malformed_cells = csv_result_data.get("malformed_cells", 0)
    status = csv_result_data.get("status", "ok")
    unknown_columns = csv_result_data.get("unknown_columns", [])

    # Determine dimensions present in headers.
    col_to_dim: dict[str, tuple[str, str, str]] = {}
    dims_in_header: set[str] = set()
    for col in headers:
        classified = _classify_column(col)
        if classified:
            dim, metric, unit = classified
            col_to_dim[col] = (dim, metric, unit)
            dims_in_header.add(dim)

    unavailable = sorted(set(_ALL_DIMENSIONS) - dims_in_header)

    # Check for model_server_offline (n_servers all 0).
    model_server_reason = None
    if _MODEL_SERVER in dims_in_header and "vllm_n_servers" in col_to_dim:
        dim, metric, unit = col_to_dim["vllm_n_servers"]
        n_servers_values = [
            r.get("vllm_n_servers") for r in rows
            if isinstance(r.get("vllm_n_servers"), (int, float))
        ]
        if n_servers_values and all(v == 0 for v in n_servers_values):
            model_server_reason = "model_server_offline"

    # Build per-dimension metrics.
    dimensions: dict[str, Any] = {}
    for dim in _ALL_DIMENSIONS:
        dim_cols = {col: v for col, v in col_to_dim.items() if v[0] == dim}
        if not dim_cols:
            dimensions[dim] = {
                "available": False,
                "reason": model_server_reason if dim == _MODEL_SERVER and model_server_reason else "collector_absent",
            }
            continue

        metrics: dict[str, Any] = {}
        has_valid_sample = False
        for col, (dim_name, metric_name, unit) in dim_cols.items():
            values = [
                r.get(col) for r in rows
                if isinstance(r.get(col), (int, float))
            ]
            if not values:
                metrics[metric_name] = {
                    "unit": unit, "sample_count": 0,
                    **{f"p{p}": None for p in _PERCENTILES},
                }
                continue
            has_valid_sample = True
            metrics[metric_name] = {
                "unit": unit,
                "sample_count": len(values),
                **{f"p{p}": round(nearest_rank_percentile(values, p), 4) for p in _PERCENTILES},
            }

        # For model_server_offline, report available=False even if there are
        # numeric samples (n_servers=0 means the model server was not running).
        if dim == _MODEL_SERVER and model_server_reason:
            dimensions[dim] = {
                "available": False,
                "reason": model_server_reason,
                "metrics": metrics,
            }
        else:
            dimensions[dim] = {
                "available": has_valid_sample,
                "reason": None if has_valid_sample else "no_valid_samples",
                "metrics": metrics,
            }

    source_available = any(d.get("available") for d in dimensions.values())

    # Sample window from timestamps.
    sample_window = None
    timestamps = [r.get("timestamp") for r in rows if r.get("timestamp")]
    if timestamps:
        # Try numeric first, then ISO-8601 (the real producer format).
        duration_s = _compute_duration(timestamps[0], timestamps[-1])
        sample_window = {
            "start": timestamps[0],
            "end": timestamps[-1],
            "duration_s": duration_s,
            "sample_count": len(rows),
        }

    return {
        "lane_key": lane_key,
        "relative_path": relative_path,
        "artifact_id": artifact_id,
        "format_version": "bench_env.monitor.csv.v1",
        "available": source_available,
        "status": status,
        "truncated_at_bytes": truncated_bytes,
        "truncated_at_rows": truncated_rows,
        "truncated_at_columns": truncated_columns,
        "sample_window": sample_window,
        "dimensions": dimensions,
        "unavailable_collectors": unavailable,
        "excluded": {
            "malformed_cells": malformed_cells,
            "unknown_columns": unknown_columns,
        },
    }
