# TP-H12 Infrastructure Monitor Reports Evidence - 2026-07-13

## Status

| Field | Value |
|---|---|
| Task | `TP-H12` |
| Status | Complete |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-13` |
| Live model required | No |

## What was built

An infrastructure report section that ingests versioned monitor artifacts
(`monitor.csv`) into report input, computes per-lane per-dimension distributions
for host, process, GPU, TCP, and model-server metrics, and reports availability
status with structured reasons. Infrastructure metrics are never mixed with
agent/simulator execution phases.

### Source discovery (shared helper)

`test_platform/domain/reports/monitor_source.py` provides
`discover_monitor_sources`, used by both `ReportInputRepository._get_for_run`
(report ingestion) and the artifact indexer, ensuring identical source sets.

Discovery rules:
- Serial/Parallel: `<lane_artifact_root>/monitor.csv`
- Multiprocess: `<lane_artifact_root>/*/monitor.csv` (one-level timestamp
  subdirectory, not recursive, not `shards/p*`)
- Path-safe: verified via `_contained_path` (rejects absolute/`..`/symlink escape)
- Deterministic: sorted by `(lane_key, relative_path)` before applying limits
- Bounded: max 32 sources; excess recorded in `discovered_count/accepted_count/excluded_source_count`

### CSV loader (streaming, bounded)

`test_platform/domain/reports/monitor_csv.py` streams the CSV via `csv.reader`
(never `read_text()`). Bounds: 10MB per source, 10,000 rows, 256 columns.
Per-cell malformed handling (NaN/Infinity/non-numeric excluded per-cell, not
per-row). Truncation records `truncated_at_bytes/rows`. Header signature
validation requires `timestamp` + at least one known metric. Duplicate columns
detected and excluded. Unknown columns recorded.

### Normalized metric registry

Explicit mapping from CSV column names to (dimension, metric_name, unit):
- Host: load1/load5/mem_used_gb/mem_pct
- Process: proc_<group>_rss_mb / proc_<group>_count
- GPU: gpu<n>_util/mem_used_mb/temp (gpu<n>_procs is metadata)
- TCP: tcp_established/time_wait/close_wait
- Model-server: vllm_* (allowlisted _VLLM_CSV_COLUMNS) / vllm_s<idx>_*

### Availability rules (mechanical)

- No source: `available=false`, `reason=no_monitor_artifact`
- Header missing timestamp or known metric: `malformed_header`
- Zero data rows: `empty`
- No valid numeric cells: `no_valid_samples`
- vLLM headers but n_servers always 0: `model_server_offline`
- Truncated source still `available` if it has valid data
- Top-level `available = any(source has >=1 valid sample)`
- Top-level `unavailable_collectors`: intersection (all sources lack it)
- `partially_unavailable_collectors`: union minus intersection

### Lane-scoped output

Each source produces its own dimensions — no cross-lane percentile merging.
Output preserves `relative_path` for artifact linking.

### Schema

Report `schema_version` remains 2 (infrastructure is a new key). ReportInput
`schema_version` bumped to 2 (monitor_sources added to hash inputs).

## Verification

| Phase | Result |
|---|---|
| Unit tests (metric registry, CSV loader, builder, discovery, bounds, paired-lane, offline, truncation, metadata columns, empty/duplicate headers, symlink escape, ISO timestamp, oversized, column limit, scan overflow) | 34 passed |
| Integration tests (no monitor → available=false + monitor CSV ingestion → available=true + artifact list/content + artifact_id equality + legacy schema v1 readable + scan overflow zero sources + source limit exact counts + no monitor bounds metadata) | 14 passed |
| Full Test Platform regression | 422 passed |
| Frontend vitest (per-source infrastructure rendering + artifact link + scan overflow warning) | 59 passed |
| TypeScript type check | clean |
| `git diff --check` | clean |

## Review gap fixes

Six P1/P2 issues found in post-commit review were closed:

1. **Artifact indexing**: `_index_run` now calls `discover_monitor_sources` and
   inserts monitor files as lane-level artifacts (`kind="monitor"`,
   `episode_attempt_id=NULL`). The artifact API lists and serves them.
2. **Settings unification**: removed separate `settings` parameters from
   `ReportRepository`, `ReportInputRepository`, `DiagnosticInputRepository`,
   and `RunCompletionPipeline`. All use `self.database.settings` — no None
   divergence.
3. **Bounded CSV**: oversized files (>10MB) return `truncated` status without
   parsing. `csv.field_size_limit` set to 1MB. `csv.Error` caught and returns
   structured status.
4. **Metadata vs malformed**: `gpu*_procs` and other known non-numeric columns
   are stored as string metadata, not counted as malformed cells.
5. **Header validation**: duplicate columns make header invalid. Header-only
   CSV returns `status="empty"`. `requests_total` unit is `requests` (not
   `tokens`). Sample window includes `duration_s`.
6. **Frontend**: per-source dimensions table with p95, truncated/partial status,
   and raw monitor artifact link.
