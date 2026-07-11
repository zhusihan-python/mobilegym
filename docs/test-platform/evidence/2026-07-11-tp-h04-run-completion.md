# TP-H04 Durable Run Completion Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Task | `TP-H04` |
| Status | Complete |
| Base revision | `46ffe3d4` plus the completed TP-H03 working tree |
| Branch | `main` |
| Recorded at | `2026-07-11` |
| Live model required | No |
| Browser dogfood required | No; deterministic browser smoke belongs to TP-H06 and TP-H07 |

## Completion contract

Successful executors now hand a run to `evaluating` instead of declaring it
terminal. `RunSupervisor` invokes one `RunCompletionPipeline` operation that:

1. creates or reuses the immutable report;
2. persists the quality-gate result in the same report transaction;
3. persists report and gate events;
4. transitions the run attempt and run through reporting to `completed`;
5. returns the durable report id, gate verdict, and five outcome counts;
6. lets `RunSupervisor` persist exactly one terminal run event.

A SQLite guard trigger in the integration suite rejects any transition to
`completed` unless both report and gate rows already exist. Repeating completion
for the same immutable input returns the existing report and gate without
creating duplicate rows.

## Failure and repair behavior

Report and gate persistence failures are converted into stable
`REPORT_PERSISTENCE_FAILED` and `QUALITY_GATE_PERSISTENCE_FAILED` lifecycle
errors. The run and active run attempt become `failed`; existing episode
attempts and artifact references remain intact. The corresponding failure event
is durable before the supervisor emits its single `run.failed` event.

Normal report GET is a pure read. For historical terminal runs only, a missing
report uses an idempotent repair path. Active runs return `REPORT_NOT_READY`
instead of creating completion facts as a read side effect.

An empty threshold map now yields `not_configured`. Configured thresholds retain
the existing `passed`, `failed`, and `error` verdict semantics.

## Operator-visible result

Run list and Run Detail expose execution lifecycle and quality verdict as
separate labelled values. Both surfaces also expose pass, fail, error,
cancelled, and incomplete counts without requiring the report panel. The live
event reducer consumes the terminal completion payload, so these facts appear
immediately without a report GET.

The deterministic component fixture covers the observable combination requested
by the task: `Execution: completed`, a failed verdict, and a non-zero error
count, alongside completed/passed and lifecycle-failed cases.

## TDD and verification

| Phase | Result |
|---|---|
| Red | Completion tests exposed terminal runs with no durable report/gate, empty thresholds reported as passed, and missing lifecycle/verdict/count UI facts |
| Completion module | 6 passed |
| Focused backend regression | 74 passed, 1 existing Starlette/httpx2 deprecation warning, in 56.17 s |
| Focused frontend regression | 20 passed across 5 files in 1.10 s |
| Frontend type check | `npm run platform:typecheck` passed |
| Full frontend regression | 55 passed across 19 files in 2.10 s |
| Full Test Platform regression | 290 passed, 1 existing Starlette/httpx2 deprecation warning, in 58.76 s |

The focused backend set includes success, idempotency, report failure, gate
failure, immediate API availability, direct executor handoff, cancellation,
single-lane modes, paired modes, and quality-gate semantics. The full regression
also confirms retry flows explicitly complete the prior attempt before selecting
failed/error work. No assertion was skipped or weakened.
