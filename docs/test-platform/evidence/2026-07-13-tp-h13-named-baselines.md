# TP-H13 Named, Discoverable Strict Baselines Evidence - 2026-07-13

## Status

| Field | Value |
|---|---|
| Task | `TP-H13` |
| Status | Complete |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-13` |
| Live model required | No |

## What was built

Strict baseline promotion is now a named project capability rather than an
anonymous promotion record. Names are NFKC-normalized, whitespace-collapsed,
case-folded for uniqueness, and limited to 80 display characters. Empty and
overlong names have stable errors. A SQLite partial unique index enforces one
active normalized name per project, while archived rows release the name.

Migration `0014_named_baselines.sql` adds catalog fields and deterministically
labels every anonymous row as `Legacy baseline <baseline-id>`. The full ID is
used so labels cannot collide within or across projects. The migration test
upgrades a real 0001-0013 temporary database containing an anonymous baseline,
then mechanically proves active conflict and archive/name-reuse semantics.

## Public contract

- `POST /api/platform/v1/runs/{run_id}/baseline` accepts
  `{display_name, lane_key}` and continues to delegate every eligibility
  decision to `BaselineEligibility`.
- `GET /api/platform/v1/projects/{project_id}/baselines` lists active baselines
  with source run, selected lane, target revision, workflow version, report
  version, and creation time. `include_archived=true` is available to callers
  that need archived records.
- `GET /api/platform/v1/baselines/{baseline_id}` returns the public summary,
  immutable source-report identity, and replay links pinned to the source run
  attempt and selected lane.
- `POST /api/platform/v1/baselines/{baseline_id}/archive` archives without
  deleting the report or provenance.
- `GET /api/platform/v1/reports/{report_id}` reads the exact immutable report
  referenced by a baseline; baseline detail never points at a movable
  latest-report lookup.

Public list/detail DTOs expose product provenance rather than persistence-only
fields such as `target_revision_ids_json` or `name_key`.

## Console behavior

Run Detail requires a baseline name before promotion and renders structured
server rejection messages. A new Baselines workspace lists the selected
project's active baselines. Detail pages show provenance facts, source run,
immutable source report, selected-lane replay links, and archive state. After
archive, the source evidence remains linked and the operator can return to the
source run and reuse the released name.

Imported runs still show `STRICT_PROVENANCE_INCOMPLETE` through the existing
eligibility interface and cannot be promoted. Trend charts and automatic
current-vs-baseline execution were not added.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Legacy migration | Upgraded databases had no `display_name` column | Deterministic labels and partial active-name uniqueness passed |
| Public catalog workflow | Named promotion body was rejected with 422 | Create/list/detail/duplicate/archive/reuse passed through HTTP |
| Name bounds | No bounded public contract existed | Missing, blank, and 81-character names are rejected |
| Console workflow | No name input or Baselines route existed | Naming, duplicate error, discovery, detail, archive, and reuse passed |
| Immutable report link | Detail pointed at the run's movable latest report | Report-ID read interface and pinned detail link passed |

## Verification

| Phase | Result |
|---|---|
| Focused backend (`test_reports_api.py` + `test_migrations.py`) | 25 passed, 1 existing Starlette/httpx2 deprecation warning |
| Focused frontend (`testPlatformReports.test.tsx`) | 3 passed |
| Full frontend | 60 passed across 20 files |
| TypeScript type check | clean |
| Full backend invocation | 424 passed; 2 existing browser smoke tests stopped before product startup because Windows `CreateProcess` could not execute the available `npx.cmd` shim (`WinError 2`) |
| Backend without the browser-smoke module | 421 passed, 1 existing warning |
| `git diff --check` | clean |

The two browser failures are classified as an environment launcher limitation,
not a TP-H13 product failure: both fail in `subprocess.Popen(["npx", "vite",
...])` before Vite, the API, or the console starts. The prescribed TP-H13
focused suites and all non-browser Test Platform regressions are green.
