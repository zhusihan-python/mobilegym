# TP-H06 Deterministic Browser Smoke Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Task | `TP-H06` |
| Status | Complete |
| Base revision | `ddecb72` plus the completed TP-H05 working tree |
| Branch | `main` |
| Recorded at | `2026-07-11` |
| Live model required | No |
| Network service required | No |
| Browser | Playwright Chromium automated smoke plus Codex in-app Browser dogfood |

## Test-only composition

`test_platform.testing.deterministic` contains the deterministic target probe,
task factory, Agent, environment, and executor resolver. The production
application does not import this module. Its resolver requires
`enabled=True` at composition and accepts only `deterministic` or
`deterministic-slow` lanes; the safety test proves disabled composition fails
before an executor can be selected.

The standalone test server is also under `test_platform.testing`. It explicitly
passes the resolver and target registry to the normal `create_app` factory. Vite
keeps its production-compatible default proxy target while accepting a
test-process `TEST_PLATFORM_API_URL`, allowing isolated random-port stacks.

## Production seams crossed

The adapter delegates execution to the normal `SerialRunExecutor`; it does not
insert terminal rows. The smoke crosses:

1. public project, target, target-health, workflow, publish, run, cancel, Resume,
   report, diagnostics, artifact, replay, and event interfaces;
2. normal task materialization and Manual Sequence plan compilation;
3. `BaseRunner`, runner events, `LaneOutcomeCommitter`, attempts, and terminal
   outcome normalization;
4. `RunRecorder` trajectory and raw/annotated screenshot generation;
5. artifact indexing, replay DTO mapping, diagnostics, completion, report, gate,
   and baseline eligibility;
6. the real Vite console and SSE client against an independently running API.

## Deterministic scenarios

The three-step Manual Sequence preserves the authored task order and produces
`PASS / FAIL / PASS`. Every execution environment starts with marker `clean`;
the task returns a judge error if isolation is violated. Step two exposes the
stable judge reason `deterministic failure at sequence step 2`. The browser
opens its screenshot replay, reads that judge JSON, observes all three ordered
report rows, and sees strict baseline promotion disabled.

The browser reloads while execution is active and again after completion.
Durable outcome counts remain exactly two pass, one fail, and zero incomplete,
showing that SSE replay/reconnect does not double count terminal work.

A `deterministic-slow` run is cancelled with the console action. The browser
observes `cancelled`, zero active workers, and the public run DTO retains a
terminal `CANCELLED` episode. Standard runner `finally` cleanup owns environment
closure.

For restart recovery, the smoke kills the API process while another slow run is
`running`, restarts it against the same temporary SQLite database and runs
directory, and verifies durable `failed / SERVICE_RESTARTED` recovery. The
public Resume interface selects the affected episode with reason
`resume_service_restarted`; the resumed attempt is then cancelled cleanly.

## Recovery defect found during dogfood

The first browser recovery run exposed a lifecycle/event inconsistency: startup
reconciliation durably changed the run to `failed`, but the event stream ended
at the pre-crash `run.started`. On reconnect, the UI replayed that stale event
and displayed `running` over the terminal REST snapshot.

Startup reconciliation now returns recovered run IDs to `RunSupervisor`, which
persists the sole recovery `run.failed` event with `SERVICE_RESTARTED`. The
browser and REST read models therefore converge on the durable terminal state.

## Verification

| Phase | Result |
|---|---|
| Safety red | `test_platform.testing` did not exist; deterministic composition could not be explicitly selected |
| Public-seam tracer | Project through replay passed with ordered PASS/FAIL/PASS, screenshots, and judge evidence |
| Browser red | Recovery REST state was failed while SSE replay left the console showing running |
| Focused TP-H06 suite | 3 passed, 1 existing Starlette/httpx2 deprecation warning, in 10.10 s |
| Frontend type check | `npm run platform:typecheck` passed |
| Full frontend regression | 55 passed across 19 files in 2.00 s |
| Full Test Platform regression | 299 passed, 1 existing Starlette/httpx2 deprecation warning, in 68.99 s |
| In-app Browser dogfood | Completed run displayed 2 pass / 1 fail, screenshot replay, judge reason, ordered three-row report, diagnostics, artifacts, and disabled strict promotion |

No assertion was skipped or weakened. All smoke storage and services are
temporary and no external model endpoint, credential, simulator deployment, or
pre-existing run directory is used.

## Timing sensitivity

The `deterministic-slow` agent emits a `CLICK` action each step with a short
`asyncio.sleep`, looping until the run is cancelled or the API process is
killed. The cancellation and restart-recovery scenarios therefore do not depend
on a single narrow timing window; the slow run is still active at the moment
the test acts on it. The only timing risk is the overall test timeout under
slower CI machines: the suite uses explicit `httpx`/Playwright timeouts and
process-kill waits rather than fixed sleeps, so watch the aggregate duration,
not per-step races.
