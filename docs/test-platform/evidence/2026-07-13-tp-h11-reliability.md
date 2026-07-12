# TP-H11 Reliability and Pass@k Reports Evidence - 2026-07-13

## Status

| Field | Value |
|---|---|
| Task | `TP-H11` |
| Status | Complete |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-13` |
| Live model required | No |

## What was built

A reliability report section that groups repeated trials by stable materialization
identity (`materialization_key`) and computes Pass@k using the benchmark
framework's canonical unbiased estimator.

### Reliability builder

`test_platform/domain/reports/reliability.py` is a pure function that takes
`ReportInput` and returns `{"schema_version": 1, "input": {...}, "summary": {...},
"tasks": [...], "pass_k_values": [1, 2, 5]}`.

Trials are grouped by `materialization_key` (stable across `trial_id`), not by
`task_id` or display order. Valid trials (PASS or FAIL) contribute to the Pass@k
denominator; ERROR, CANCELLED, and MISSING trials are reported separately.

The Pass@k computation reuses `bench_env.metrics._compute_single_pass_k` — the
canonical unbiased estimator `1 - C(n-c, k) / C(n, k)`. The reliability builder
extracts n (valid) and c (success) per materialization group, then calls the
estimator. The CLI `compute_pass_at_k` is not modified.

### Flakiness classification

- **flaky**: >=2 valid trials with both success and failure
- **stable**: >=2 valid trials, all same outcome
- **insufficient_trials**: <2 valid trials
- **null**: no valid trials at all

### Per-task counts

Each materialization reports: planned, attempted, valid, success, failure,
error, cancelled, missing.

### Schema version bump

Report `schema_version` bumped from 1 to 2. The cache query (`_find_existing`)
now filters `schema_version = 2`. Old reports (version 1) remain readable via
`get` (takes latest regardless of version). New reports are rebuilt with
version 2 (old cache misses → rebuild).

### materialization_key in report input

The `_episode_attempts` query and `_map_report_episode_attempt` mapper now
include `e.materialization_key`, which flows through `ReportInput` into the
reliability builder.

### Frontend

ReportPanel renders a `tp-report-reliability` section with aggregate Pass@1,
flaky task count, insufficient trial count, and a per-task table (task_id,
Pass@1, valid/success/failure counts, flakiness badge).

### Export

JSON and HTML exports automatically include the reliability section because
they serialize the entire report dict. No changes to `export.py`.

## Verification

| Phase | Result |
|---|---|
| Unit tests (planned-based counts, flakiness, Pass@k values, paired-lane separation, SUCCESS outcome, missing-trial detection, canonical estimator match) | 15 passed |
| Integration tests (schema_version=2, reliability in report, materialization_key in episode_attempts, legacy schema v1 readable + exportable) | 10 passed |
| Full Test Platform regression | 384 passed |
| Frontend vitest (reliability rendering) | 59 passed |
| TypeScript type check | clean |
| `git diff --check` | clean |

## Review gap fixes

Three P1 issues found in post-commit review were closed:

1. **Planned/missing from wrong source**: the builder now reads
   `planned_lane_episodes` (the authoritative trial universe, which already
   selects the latest attempt per episode/lane) instead of raw episode_attempts.
   Missing trials (planned but no attempt) are correctly counted; repeated
   attempt_no rows do not inflate the denominator.
2. **Paired lanes mixed**: grouping changed from `materialization_key` alone to
   `(lane_key, materialization_key)`. Baseline and candidate lanes sharing the
   same materialization_key are now separate reliability samples.
3. **SUCCESS counted as missing**: `_SUCCESS_OUTCOMES = {"PASS", "SUCCESS"}` —
   both are counted as success, matching the existing functional/sequence report
   contract.

A schema v1 legacy report readability/export test verifies old reports remain
accessible after the version bump.

## Observable demo

Run a deterministic repeated-trial workflow containing a stable pass, stable
fail, and flaky task; inspect Pass@1 and per-task flakiness in the Reliability
section of the run detail report.
