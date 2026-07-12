"""Unit tests for infrastructure report builder, CSV loader, and source discovery."""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path

import pytest

from test_platform.domain.reports.infrastructure import (
    build_infrastructure_report,
    is_known_metric_column,
)
from test_platform.domain.reports.input import ReportInput
from test_platform.domain.reports.monitor_csv import load_monitor_csv
from test_platform.domain.reports.monitor_source import discover_monitor_sources


# ---- Frozen header from real bench_env.monitor output ----

_MONITOR_HEADER = [
    "timestamp", "load1", "load5", "mem_used_gb", "mem_pct",
    "proc_vllm_count", "proc_vllm_rss_mb",
    "proc_chromium_count", "proc_chromium_rss_mb",
    "tcp_established", "tcp_time_wait", "tcp_close_wait",
    "gpu0_util", "gpu0_mem_used_mb", "gpu0_temp", "gpu0_procs",
    "vllm_running", "vllm_waiting", "vllm_kv_cache_pct",
    "vllm_prompt_tps", "vllm_gen_tps", "vllm_n_servers",
]


def _write_csv(path: Path, rows: list[list[str]], header: list[str] | None = None) -> None:
    hdr = header or _MONITOR_HEADER
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(hdr)
        for row in rows:
            writer.writerow(row)


def _make_input(monitor_sources=None) -> ReportInput:
    return ReportInput(
        run_id="run-1",
        run_attempt_id="attempt-1",
        provenance={},
        planned_lane_episodes=[],
        episode_attempts=[],
        comparison=None,
        input_hash="sha256:test",
        monitor_sources=monitor_sources,
    )


def _make_source_from_csv(path: Path, lane_key: str = "candidate") -> dict:
    result = load_monitor_csv(path)
    return {
        "lane_key": lane_key,
        "relative_path": str(path.name),
        "csv_result": {
            "headers": result.headers,
            "rows": result.rows,
            "row_count": result.row_count,
            "truncated_at_bytes": result.truncated_at_bytes,
            "truncated_at_rows": result.truncated_at_rows,
            "malformed_cells": result.malformed_cells,
            "status": result.status,
            "unknown_columns": result.unknown_columns,
        },
    }


class TestMetricRegistry:
    def test_known_host_columns(self):
        assert is_known_metric_column("load1")
        assert is_known_metric_column("mem_used_gb")

    def test_known_gpu_columns(self):
        assert is_known_metric_column("gpu0_util")
        assert is_known_metric_column("gpu0_procs")  # metadata but known

    def test_known_vllm_columns(self):
        assert is_known_metric_column("vllm_running")
        assert is_known_metric_column("vllm_s0_running")

    def test_unknown_columns(self):
        assert not is_known_metric_column("vllm_bad_metric")
        assert not is_known_metric_column("random_column")
        assert not is_known_metric_column("timestamp")
        assert not is_known_metric_column("bench_episodes")


class TestCsvLoader:
    def test_valid_csv(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
        ])
        result = load_monitor_csv(csv_path)
        assert result.status == "ok"
        assert result.row_count == 1
        assert result.header_valid
        assert result.malformed_cells == 0

    def test_empty_csv(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        csv_path.write_text("")
        result = load_monitor_csv(csv_path)
        assert result.status == "empty"
        assert result.row_count == 0

    def test_malformed_header_missing_timestamp(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [["0.5"]], header=["load1"])
        result = load_monitor_csv(csv_path)
        assert result.status == "malformed_header"

    def test_malformed_header_no_known_metric(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [["1"]], header=["timestamp"])
        result = load_monitor_csv(csv_path)
        assert result.status == "malformed_header"

    def test_duplicate_columns_excluded(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [["1", "0.5", "0.6"]], header=["timestamp", "load1", "load1"])
        result = load_monitor_csv(csv_path)
        assert "load1" in result.duplicate_columns

    def test_per_cell_malformed_not_dropping_row(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "0.5", "abc", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
        ])
        result = load_monitor_csv(csv_path)
        assert result.malformed_cells == 1
        # Row is still present with other valid cells.
        assert result.row_count == 1
        assert result.rows[0]["load1"] == 0.5
        # load5 cell was malformed → should not be numeric.
        assert not isinstance(result.rows[0].get("load5"), float)

    def test_nan_rejected(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "NaN", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
        ])
        result = load_monitor_csv(csv_path)
        assert result.malformed_cells == 1
        assert not isinstance(result.rows[0].get("load1"), float)

    def test_infinity_rejected(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "inf", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
        ])
        result = load_monitor_csv(csv_path)
        assert result.malformed_cells == 1

    def test_unknown_columns_recorded(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1", "42"],
        ], header=_MONITOR_HEADER + ["random_metric"])
        result = load_monitor_csv(csv_path)
        assert "random_metric" in result.unknown_columns

    def test_truncated_rows(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        rows = []
        for i in range(10_001):
            rows.append([
                str(i), "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
                "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1",
            ])
        _write_csv(csv_path, rows)
        result = load_monitor_csv(csv_path)
        assert result.truncated_at_rows is not None
        assert result.status == "truncated"


class TestInfrastructureBuilder:
    def test_no_monitor_sources(self):
        report = build_infrastructure_report(_make_input())
        assert report["available"] is False
        assert report["reason"] == "no_monitor_artifact"

    def test_with_monitor_data(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
            ["2", "0.8", "0.4", "2.5", "60", "1", "110", "0", "0", "12", "6", "0",
             "40", "1100", "65", "1", "2", "0", "60", "120", "220", "1"],
        ])
        src = _make_source_from_csv(csv_path)
        report = build_infrastructure_report(_make_input([src]))
        assert report["available"] is True
        source = report["sources"][0]
        assert source["lane_key"] == "candidate"
        dims = source["dimensions"]
        # Host should have metrics.
        assert dims["host"]["available"]
        assert "load_1m" in dims["host"]["metrics"]
        assert dims["host"]["metrics"]["load_1m"]["unit"] == "load"
        assert dims["host"]["metrics"]["load_1m"]["sample_count"] == 2

    def test_paired_lanes_separate(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
        ])
        src1 = _make_source_from_csv(csv_path, lane_key="baseline")
        src2 = _make_source_from_csv(csv_path, lane_key="candidate")
        report = build_infrastructure_report(_make_input([src1, src2]))
        assert len(report["sources"]) == 2
        assert {s["lane_key"] for s in report["sources"]} == {"baseline", "candidate"}

    def test_unavailable_gpu(self, tmp_path: Path):
        """A CSV without GPU columns must report gpu as collector_absent."""
        csv_path = tmp_path / "monitor.csv"
        header = ["timestamp", "load1", "load5", "mem_used_gb", "mem_pct"]
        _write_csv(csv_path, [["1", "0.5", "0.3", "2.0", "50"]], header=header)
        src = _make_source_from_csv(csv_path)
        report = build_infrastructure_report(_make_input([src]))
        source = report["sources"][0]
        assert "gpu" in source["unavailable_collectors"]
        assert source["dimensions"]["gpu"]["reason"] == "collector_absent"

    def test_empty_window(self, tmp_path: Path):
        """A CSV with valid header but zero data rows."""
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [])  # header only
        result = load_monitor_csv(csv_path)
        assert result.status == "empty"
        assert result.row_count == 0
        src = _make_source_from_csv(csv_path)
        report = build_infrastructure_report(_make_input([src]))
        source = report["sources"][0]
        assert source["available"] is False

    def test_model_server_offline(self, tmp_path: Path):
        """vLLM headers present but n_servers always 0."""
        csv_path = tmp_path / "monitor.csv"
        header = ["timestamp", "load1", "vllm_running", "vllm_n_servers"]
        _write_csv(csv_path, [["1", "0.5", "0", "0"]], header=header)
        src = _make_source_from_csv(csv_path)
        report = build_infrastructure_report(_make_input([src]))
        source = report["sources"][0]
        assert source["dimensions"]["model_server"]["reason"] == "model_server_offline"

    def test_truncated_source_still_available(self, tmp_path: Path):
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["1", "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
        ])
        result = load_monitor_csv(csv_path)
        # Manually mark as truncated.
        src = _make_source_from_csv(csv_path)
        src["csv_result"]["status"] = "truncated"
        src["csv_result"]["truncated_at_rows"] = 10000
        report = build_infrastructure_report(_make_input([src]))
        source = report["sources"][0]
        assert source["status"] == "truncated"
        assert source["available"] is True  # still has valid data

    def test_top_level_unavailable_intersection(self, tmp_path: Path):
        """If baseline lacks GPU but candidate has it, top-level unavailable
        should NOT include gpu (intersection only)."""
        csv_with_gpu = tmp_path / "with_gpu.csv"
        _write_csv(csv_with_gpu, [
            ["1", "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
        ])

        csv_no_gpu = tmp_path / "no_gpu.csv"
        header_no_gpu = ["timestamp", "load1", "load5", "mem_used_gb", "mem_pct"]
        _write_csv(csv_no_gpu, [["1", "0.5", "0.3", "2.0", "50"]], header=header_no_gpu)

        src1 = _make_source_from_csv(csv_with_gpu, lane_key="baseline")
        src2 = _make_source_from_csv(csv_no_gpu, lane_key="candidate")
        report = build_infrastructure_report(_make_input([src1, src2]))
        # GPU is NOT in top-level unavailable (only candidate lacks it).
        assert "gpu" not in report["unavailable_collectors"]
        # GPU IS in partially_unavailable.
        assert "gpu" in report["partially_unavailable_collectors"]


class TestSourceDiscovery:
    def test_serial_lane_root(self, tmp_path: Path):
        lane_root = tmp_path / "lanes/candidate/attempts/0001"
        lane_root.mkdir(parents=True)
        _write_csv(lane_root / "monitor.csv", [["1", "0.5"]], header=["timestamp", "load1"])

        lane_attempts = [{"id": "la1", "lane_key": "candidate", "run_attempt_id": "ra1",
                          "artifact_root": "lanes/candidate/attempts/0001"}]
        result = discover_monitor_sources(tmp_path, lane_attempts)
        assert result.accepted_count == 1
        assert result.sources[0].lane_key == "candidate"

    def test_multiprocess_subdirectory(self, tmp_path: Path):
        lane_root = tmp_path / "lanes/candidate/attempts/0001"
        ts_dir = lane_root / "20260712_120000"
        ts_dir.mkdir(parents=True)
        _write_csv(ts_dir / "monitor.csv", [["1", "0.5"]], header=["timestamp", "load1"])

        lane_attempts = [{"id": "la1", "lane_key": "candidate", "run_attempt_id": "ra1",
                          "artifact_root": "lanes/candidate/attempts/0001"}]
        result = discover_monitor_sources(tmp_path, lane_attempts)
        assert result.accepted_count == 1
        assert "20260712_120000" in result.sources[0].relative_path

    def test_no_monitor_file(self, tmp_path: Path):
        lane_root = tmp_path / "lanes/candidate/attempts/0001"
        lane_root.mkdir(parents=True)
        lane_attempts = [{"id": "la1", "lane_key": "candidate", "run_attempt_id": "ra1",
                          "artifact_root": "lanes/candidate/attempts/0001"}]
        result = discover_monitor_sources(tmp_path, lane_attempts)
        assert result.accepted_count == 0

    def test_deterministic_sort_order(self, tmp_path: Path):
        """Sources must be sorted by (lane_key, relative_path)."""
        for lane_key in ["candidate", "baseline"]:
            lane_root = tmp_path / f"lanes/{lane_key}/attempts/0001"
            lane_root.mkdir(parents=True)
            _write_csv(lane_root / "monitor.csv", [["1", "0.5"]], header=["timestamp", "load1"])

        lane_attempts = [
            {"id": "la1", "lane_key": "candidate", "run_attempt_id": "ra1",
             "artifact_root": "lanes/candidate/attempts/0001"},
            {"id": "la2", "lane_key": "baseline", "run_attempt_id": "ra1",
             "artifact_root": "lanes/baseline/attempts/0001"},
        ]
        result = discover_monitor_sources(tmp_path, lane_attempts)
        assert result.sources[0].lane_key == "baseline"
        assert result.sources[1].lane_key == "candidate"

    def test_source_limit(self, tmp_path: Path):
        """Exceeding 32 sources should record excluded_source_count."""
        lane_attempts = []
        for i in range(40):
            lane_root = tmp_path / f"lane{i}/attempts/0001"
            lane_root.mkdir(parents=True)
            _write_csv(lane_root / "monitor.csv", [["1", "0.5"]], header=["timestamp", "load1"])
            lane_attempts.append({
                "id": f"la{i}", "lane_key": f"lane{i:02d}",
                "run_attempt_id": "ra1", "artifact_root": f"lane{i}/attempts/0001",
            })
        result = discover_monitor_sources(tmp_path, lane_attempts)
        assert result.discovered_count == 40
        assert result.accepted_count == 32
        assert result.excluded_source_count == 8


class TestBoundsAndEdgeCases:
    """Tests for discovery bounds, CSV limits, and edge cases."""

    def test_discovery_overflow_dirs_fail_closed(self, tmp_path: Path):
        """When a lane root has >65 entries, subdirectory discovery is skipped
        (fail-closed) to avoid non-deterministic selection."""
        lane_root = tmp_path / "lane/att/0001"
        lane_root.mkdir(parents=True)
        _write_csv(lane_root / "monitor.csv", [["1", "0.5"]], header=["timestamp", "load1"])
        # Create 66 subdirectories with monitor.csv.
        for i in range(66):
            d = lane_root / f"ts{i:04d}"
            d.mkdir()
            _write_csv(d / "monitor.csv", [["1", "0.5"]], header=["timestamp", "load1"])

        lane_attempts = [{"id": "la1", "lane_key": "candidate", "run_attempt_id": "ra1",
                          "artifact_root": "lane/att/0001"}]
        result = discover_monitor_sources(tmp_path, lane_attempts)
        # Serial path monitor.csv still found.
        serial_sources = [s for s in result.sources if "/" not in s.relative_path.split("monitor.csv")[0].rstrip("/").split("/")[-1]]
        assert result.accepted_count == 1  # only the direct lane root monitor.csv

    def test_discovery_many_files_triggers_overflow(self, tmp_path: Path):
        """100 regular files in lane root (>65 entry limit) triggers overflow,
        but the direct monitor.csv is still found (serial path is not gated)."""
        lane_root = tmp_path / "lane/att/0001"
        lane_root.mkdir(parents=True)
        _write_csv(lane_root / "monitor.csv", [["1", "0.5"]], header=["timestamp", "load1"])
        for i in range(100):
            (lane_root / f"file{i}.txt").write_text("x")

        lane_attempts = [{"id": "la1", "lane_key": "candidate", "run_attempt_id": "ra1",
                          "artifact_root": "lane/att/0001"}]
        result = discover_monitor_sources(tmp_path, lane_attempts)
        # Serial monitor.csv is still found (not gated by subdir scan).
        assert result.accepted_count == 1
        # Overflow was detected and recorded.
        assert "candidate" in result.scan_truncated_lanes

    def test_column_limit_marks_invalid(self, tmp_path: Path):
        """CSV with >256 columns should have header marked invalid."""
        csv_path = tmp_path / "monitor.csv"
        cols = ["timestamp"] + [f"load1_{i}" for i in range(256)]
        row = ["1"] + ["0.5"] * 256
        _write_csv(csv_path, [row], header=cols)
        result = load_monitor_csv(csv_path)
        assert not result.header_valid
        assert result.truncated_at_columns

    def test_csv_error_classified_as_malformed_csv(self, tmp_path: Path):
        """A CSV with a field larger than the size limit should return malformed_csv."""
        csv_path = tmp_path / "monitor.csv"
        # Create a row with a massive quoted field (> 1MB field limit).
        huge_field = "x" * (2 * 1024 * 1024)  # 2MB field
        csv_path.write_text(
            f'timestamp,load1\n1,"{huge_field}"\n'
        )
        result = load_monitor_csv(csv_path)
        # File is < 10MB so it's not oversized-truncated. The field_size_limit
        # triggers csv.Error → malformed_csv status.
        assert result.status == "malformed_csv"

    def test_iso_timestamp_duration(self, tmp_path: Path):
        """Real ISO-8601 timestamps from bench_env.monitor produce non-null duration_s."""
        csv_path = tmp_path / "monitor.csv"
        _write_csv(csv_path, [
            ["2026-07-13T10:00:00.000000", "0.5", "0.3", "2.0", "50", "1", "100", "0", "0", "10", "5", "0",
             "30", "1000", "60", "1", "1", "0", "50", "100", "200", "1"],
            ["2026-07-13T10:01:00.000000", "0.8", "0.4", "2.5", "60", "1", "110", "0", "0", "12", "6", "0",
             "40", "1100", "65", "1", "2", "0", "60", "120", "220", "1"],
        ])
        result = load_monitor_csv(csv_path)
        assert result.status == "ok"
        src = {
            "lane_key": "candidate",
            "relative_path": "monitor.csv",
            "csv_result": {
                "headers": result.headers,
                "rows": result.rows,
                "row_count": result.row_count,
                "truncated_at_bytes": result.truncated_at_bytes,
                "truncated_at_rows": result.truncated_at_rows,
                "truncated_at_columns": result.truncated_at_columns,
                "malformed_cells": result.malformed_cells,
                "status": result.status,
                "unknown_columns": result.unknown_columns,
            },
        }
        from test_platform.domain.reports.infrastructure import build_infrastructure_report
        from test_platform.domain.reports.input import ReportInput
        ri = ReportInput(
            run_id="r1", run_attempt_id="a1", provenance={},
            planned_lane_episodes=[], episode_attempts=[], comparison=None,
            input_hash="h1", monitor_sources=[src],
        )
        report = build_infrastructure_report(ri)
        source = report["sources"][0]
        assert source["sample_window"] is not None
        assert source["sample_window"]["duration_s"] is not None
        assert source["sample_window"]["duration_s"] == 60.0  # 1 minute apart

    def test_oversized_file_not_parsed(self, tmp_path: Path):
        """Files > 10MB should return truncated without parsing."""
        csv_path = tmp_path / "monitor.csv"
        # Write > 10MB of data.
        header = "timestamp,load1\n"
        row = "1,0.5\n"
        with open(csv_path, "w") as f:
            f.write(header)
            while csv_path.stat().st_size < 11 * 1024 * 1024:
                f.write(row)
        result = load_monitor_csv(csv_path)
        assert result.status == "truncated"
        assert result.truncated_at_bytes is not None
        assert result.row_count == 0  # not parsed

    def test_symlink_escape_rejected(self, tmp_path: Path):
        """A symlinked monitor.csv pointing outside run_root must be rejected."""
        import tempfile

        lane_root = tmp_path / "lane/att/0001"
        lane_root.mkdir(parents=True)
        # Create external file OUTSIDE run_root.
        external_dir = Path(tempfile.mkdtemp())
        external = external_dir / "monitor.csv"
        _write_csv(external, [["1", "0.5"]], header=["timestamp", "load1"])
        # Create symlink pointing outside run_root.
        link = lane_root / "monitor.csv"
        link.symlink_to(external)

        lane_attempts = [{"id": "la1", "lane_key": "candidate", "run_attempt_id": "ra1",
                          "artifact_root": "lane/att/0001"}]
        result = discover_monitor_sources(tmp_path, lane_attempts)
        # Symlink escapes run_root → must be rejected.
        assert result.accepted_count == 0
