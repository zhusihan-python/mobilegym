# TP-H16 Hardening Release Acceptance - 2026-07-13

## Release status

| Field | Value |
|---|---|
| Task | `TP-H16` |
| Status | Complete |
| Starting revision | `b10e24b` (`TP-H15: connect recovery previews and incident URLs`) |
| Source revision | TP-H16 documentation commit containing this record |
| Branch | `main` |
| Recorded at | `2026-07-13` |
| Live model required | No |

TP-H00 through TP-H15 are complete and linked below. The release gate was run
from a worktree containing only the TP-H16 documentation changes plus the local,
untracked `.agents/` skill directory. Generated test caches, run artifacts,
credentials, and machine-specific paths are excluded from the release commit.

## Final verification

| Command or gate | Result |
|---|---|
| `python -m pytest -c test_platform/pytest.ini test_platform/tests -q` | **PASS** — 430 passed, 1 existing Starlette/httpx2 deprecation warning |
| `python -m pytest -c bench_env/tests/pytest.ini bench_env/tests/common -m "not live" -q --basetemp .tmp/tp-h16-bench-xdist-20260713-1750` | **PASS** — 229 passed with the configured xdist workers and a writable sandbox-local temporary root |
| `npm run platform:test` | **PASS** — 64 passed across 20 files |
| `npm test` | **PASS** — 177 passed across 50 files |
| `npm run platform:typecheck` | **PASS** — no diagnostics |
| `npx tsc --noEmit` | **PASS** — no diagnostics |
| `npm run lint` | **PASS** — 0 errors, 73 existing React Hook warnings, no store-getter anti-patterns |
| Deterministic smoke module | **PASS** — 5 passed, including Manual Sequence and paired Chromium scenarios |
| Compatible/incompatible fake preflight focus | **PASS** — 2 passed, 10 deselected; compatible creation persisted redacted provenance and incompatible creation returned 409 with zero side effects |

The deterministic browser stack uses a fresh pytest temporary root, dynamic
loopback ports, a fresh SQLite database and runs directory, the local Vite entry
point, and test-only deterministic adapters. It does not use a commercial or
local model server.

## Acceptance demonstrations

| Release contract | Mechanical demonstration |
|---|---|
| Manual Sequence and paired browser operation | `test_platform/tests/e2e/test_mvp_smoke.py`; 5-test smoke module green |
| Compatible and incompatible model preflight | `test_platform/tests/integration/test_compatibility_preflight_api.py`; focused 2-test gate green |
| Report v1 compatibility | `test_platform/tests/integration/test_reports_api.py::test_legacy_schema_v1_report_remains_readable_and_exportable` passed in the 430-test backend suite |
| Report v2 reliability and infrastructure | [`2026-07-13-tp-h11-reliability.md`](2026-07-13-tp-h11-reliability.md) and [`2026-07-13-tp-h12-infrastructure.md`](2026-07-13-tp-h12-infrastructure.md) |
| Named baseline migration, discovery, archive, and reuse | [`2026-07-13-tp-h13-named-baselines.md`](2026-07-13-tp-h13-named-baselines.md) |
| Diagnostic ingestion, identity, filters, and scoped evidence | [`2026-07-13-tp-h14-diagnostic-identity.md`](2026-07-13-tp-h14-diagnostic-identity.md) |
| Recovery previews, exact links, copy, clean-page open, and reload | [`2026-07-13-tp-h15-recovery-incident-urls.md`](2026-07-13-tp-h15-recovery-incident-urls.md) |

## Task evidence index

| Task | Evidence |
|---|---|
| TP-H00 | [`2026-07-11-tp-h00-regression-baseline.md`](2026-07-11-tp-h00-regression-baseline.md), [`2026-07-11-tp-h00-blocker-repair.md`](2026-07-11-tp-h00-blocker-repair.md) |
| TP-H01 | [`2026-07-11-tp-h01-multiprocess-missing-result.md`](2026-07-11-tp-h01-multiprocess-missing-result.md) |
| TP-H02 | [`2026-07-11-tp-h02-lane-outcome-committer.md`](2026-07-11-tp-h02-lane-outcome-committer.md) |
| TP-H03 | [`2026-07-11-tp-h03-paired-lane-outcome-commit.md`](2026-07-11-tp-h03-paired-lane-outcome-commit.md) |
| TP-H04 | [`2026-07-11-tp-h04-run-completion.md`](2026-07-11-tp-h04-run-completion.md) |
| TP-H05 | [`2026-07-11-tp-h05-strict-baseline-eligibility.md`](2026-07-11-tp-h05-strict-baseline-eligibility.md) |
| TP-H06 | [`2026-07-11-tp-h06-deterministic-browser-smoke.md`](2026-07-11-tp-h06-deterministic-browser-smoke.md) |
| TP-H07 | [`2026-07-12-tp-h07-deterministic-paired-smoke.md`](2026-07-12-tp-h07-deterministic-paired-smoke.md) |
| TP-H08 | [`2026-07-12-tp-h08-post-p1-regression.md`](2026-07-12-tp-h08-post-p1-regression.md) |
| TP-H09 | [`2026-07-12-tp-h09-model-compatibility.md`](2026-07-12-tp-h09-model-compatibility.md) |
| TP-H10 | [`2026-07-12-tp-h10-preflight.md`](2026-07-12-tp-h10-preflight.md) |
| TP-H11 | [`2026-07-13-tp-h11-reliability.md`](2026-07-13-tp-h11-reliability.md) |
| TP-H12 | [`2026-07-13-tp-h12-infrastructure.md`](2026-07-13-tp-h12-infrastructure.md) |
| TP-H13 | [`2026-07-13-tp-h13-named-baselines.md`](2026-07-13-tp-h13-named-baselines.md) |
| TP-H14 | [`2026-07-13-tp-h14-diagnostic-identity.md`](2026-07-13-tp-h14-diagnostic-identity.md) |
| TP-H15 | [`2026-07-13-tp-h15-recovery-incident-urls.md`](2026-07-13-tp-h15-recovery-incident-urls.md) |

## Reproduced transient failures

| Initial observation | Classification | Reproduction and closure |
|---|---|---|
| The official `bench_env` command without `--basetemp` could not create its default sandbox temporary root | Execution-environment permission, not product behavior | `python -m pytest -c bench_env/tests/pytest.ini bench_env/tests/common -m "not live" -q --basetemp .tmp/tp-h16-bench-xdist-20260713-1750` passed 229 tests with the same configured xdist workers |
| Six main Vitest files timed out while `npm test`, both TypeScript checks, platform Vitest, lint, and Python tests competed for imports concurrently | Load-sensitive test-infrastructure timing, not an assertion failure | The unmodified official `npm test` command passed 177 tests when rerun independently |

No assertion was weakened, skipped, or given a longer timeout to close either
observation.

## Accepted warnings and deferred work

| Item | Owner | Release disposition |
|---|---|---|
| Starlette warns that `httpx` TestClient support is deprecated in favor of `httpx2` | Test Platform dependency maintenance | Accepted dependency warning; migrate in a separate dependency update |
| 73 `react-hooks/exhaustive-deps` lint warnings across existing Apps and OS code | Respective App and OS maintainers | Accepted warning backlog; release lint has zero errors and TP-H16 adds no runtime App/OS code |
| Retry and Resume service orchestration retain a similar control-flow shape | Test Platform maintainers | Optional deep-module refactor; current shared selectors, preflight, stale-token transaction, and tests are authoritative |
| TP-FUTURE-01 versioned Execution Profiles | Product and architecture review | Remains a deferred backlog opportunity with no schema, migration, API, UI, retry, or rollout commitment |

## Release conclusion

All TP-H16 acceptance criteria are met. The simulator-first Test Platform
hardening program is complete, deterministic acceptance is independent of live
models, and future product directions remain explicitly outside the release.
