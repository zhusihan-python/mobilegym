# TP-H14 Diagnostic Identity and Filtering Evidence - 2026-07-13

## Status

| Field | Value |
|---|---|
| Task | `TP-H14` |
| Status | Complete |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-13` |
| Live model required | No |

## What was built

Browser, page, network, and runner failures now enter the durable platform event
stream as normalized `diagnostic.*` events. Browser producers attach the known
trial, episode, step, App, and worker context. Controllers bind the platform
diagnostic sink automatically and emit structured action-format and runner
exception events. Serial execution now provisions the same browser-log artifact
root as the other runner paths.

`PlatformRunnerEventSink` preserves phase and execution identity in the event
payload consumed by diagnostic construction. Diagnostics schema version 2 adds
stable source-event identity and persists target, App, task, lane, episode,
attempt, worker, step, scope, artifact, comparison-pair, report, and paired
episode-attempt facts. Diagnostics derived from source events use the durable
`source_event_id` in their IDs, so later identity enrichment does not create a
second logical diagnostic. When an event identifies an episode and lane attempt
but multiple episode attempts match, the narrower attempt identity remains
explicitly unavailable instead of being guessed from the latest row.

Migration `0015_diagnostic_identity.sql` adds the normalized columns and indexes,
including a partial unique source-event index. Integration coverage verifies both
fresh migration and upgrade behavior, durable event normalization, source-event
deduplication, complete diagnostic identity, and persistence bindings.

## Public contract

The diagnostics endpoint accepts combinable category, severity, target, App,
task, retryability, lane, episode, and attempt filters. Results use a bounded
maximum page size of 200 and an opaque stable `(created_at, id)` cursor.

Public diagnostic rows expose category, severity, retryability, recommended
action, and the available execution identity. Optional comparison and gate facts
are omitted when unavailable, preserving the existing episode, comparison, and
gate DTO classifications. Artifact references contain only stable artifact IDs
and API content links; internal relative filesystem paths are not exposed.

## Console behavior

Run Detail renders diagnostic identity, retryability, recommended action, and
artifact links. Operators can combine category, task, and retryability filters.
The Evidence Dock selects diagnostics by exact `episode_attempt_id`; diagnostics
without exact attempt identity are shown separately and labelled run-wide while
retaining any known episode facts, rather than being attached to whichever
episode happens to be selected. Run Detail consumes
every stable cursor page, so filtering and evidence scope are not truncated at
the first 100 or 200 diagnostics.

The retained IDs (`source_event_id`, `episode_attempt_id`, comparison/report/gate
identity, worker, and step) provide TP-H15 with stable navigation keys without
matching display order or message text.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Durable producers | Browser and controller failures stopped at local logs or exceptions | Structured browser/page/network/runner events carry known execution context |
| Event persistence | Runner events did not retain diagnostic identity | Platform sink normalizes `diagnostic.*` events into the durable writer |
| Diagnostic identity | Public diagnostics lacked producer and execution identity | Schema v2 and migration persist stable source, scope, attempt, worker, step, App, and artifact facts |
| Ambiguous attempt identity | Multiple matching attempts reassigned earlier events to the latest row | Ambiguous attempt identity stays null while stable episode/lane/source identity remains available |
| Filtering and pagination | Queries supported only category and severity | All TP-H14 filters compose with bounded stable cursor pagination |
| Public artifacts | Diagnostic references could expose internal paths | DTOs expose artifact ID, kind, media type, and content href only |
| Evidence scope | The dock displayed all run diagnostics for every episode and consumed only the first page | Exact episode-attempt and explicitly run-wide sections consume all cursor pages and are tested |
| Classification compatibility | Adding identity introduced nullable comparison/gate keys | Exact DTO assertions preserve existing optional-field shapes |

## Verification

| Phase | Result |
|---|---|
| Focused backend diagnostics and migrations | 18 passed, 1 existing Starlette/httpx2 deprecation warning |
| Focused browser/controller/serial bench tests | 16 passed |
| Focused frontend diagnostics | 1 passed |
| TypeScript type check | clean |
| Full frontend | 60 passed across 20 files |
| Full backend invocation | 426 passed; 2 existing browser smoke tests stopped before product startup because Windows `CreateProcess` could not execute the available `npx.cmd` shim (`WinError 2`) |
| Backend without the browser-smoke module | 423 passed, 1 existing warning |
| `git diff --check` | clean |

The two browser failures are the previously recorded environment launcher
limitation, not a TP-H14 product failure. Both fail in
`subprocess.Popen(["npx", "vite", ...])` before Vite, the API, or the console
starts. All focused TP-H14 suites and all non-browser-smoke Test Platform
regressions are green.
