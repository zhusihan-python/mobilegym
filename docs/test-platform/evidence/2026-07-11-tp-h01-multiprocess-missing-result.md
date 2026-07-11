# TP-H01 Multi-process Missing-result Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Task | `TP-H01` |
| Status | Complete |
| Base revision | `895657f9c5025ff6fa3a2f8b2f5ed835526c9a5a` |
| Branch | `main` |
| Recorded at | `2026-07-11T17:28:39+08:00` |
| Live model required | No |

## Defect and repair

`MultiprocessRunExecutor._synthetic_crash_result()` returned the canonical
cancelled body when `cancelled=True`, but its non-cancelled return was
incorrectly nested after that branch's `return` and was unreachable. A missing
non-cancelled shard result therefore supplied `None` to ingestion while applying
the `WORKER_CRASH` override, producing the contradictory persisted combination
`FAIL / WORKER_CRASH`.

The repair moves the existing non-cancelled canonical error result to the
reachable default return path. The user-cancellation branch is unchanged.

## Observable regression matrix

The existing real multi-process adapter shard-crash integration scenario now
uses two configured shards to produce an exact `1 PASS + 1 ERROR` matrix. It
asserts the missing episode through temporary SQLite persistence and the public
report/follow-up read models:

- persisted outcome is `ERROR`;
- persisted error code is `WORKER_CRASH`;
- result body has `is_success=false`, `is_error=true`, a stable worker-crash
  error message, and `execution.stop_reason=ERROR`;
- terminal event is `episode.error` with matching outcome and error code;
- functional and Manual Sequence summaries count errors, with zero failures and
  zero incompletes;
- retry selection returns `reason=retry_error` and preserves sequence metadata.

The cancellation integration fixture deterministically completes one result,
then pauses until cancellation leaves missing work. It requires at least one
synthetic `CANCELLED / CANCELLED` result, forbids `WORKER_CRASH` and an error stop
reason, and verifies the corresponding `episode.cancelled` event.

## TDD evidence

| Phase | Result |
|---|---|
| Red | Shard-crash scenario failed on persisted `outcome`: expected `ERROR`, observed `FAIL` |
| Green | Shard-crash scenario passed after the one-branch reachability repair |
| Focused regression | 16 passed in 2.28 s |
| Full Test Platform regression | 267 passed in 64.88 s; one existing Starlette/httpx2 deprecation warning |

The focused command covered `test_multiprocess_lane.py`, functional reports,
sequence reports, and retry selection. No assertion was weakened or skipped.

Standards review found one duplicated local event sink; it was replaced by one
shared file-level test helper. Spec review identified weak cancellation and
shard-cardinality assertions; both fixtures were made deterministic and the
final focused and full regressions were rerun after those changes.

## Worktree hygiene

All generated pytest temporary directories were removed. The pre-existing
user-owned `.agents/` directory was not modified. No secrets, credentials,
private endpoints, or absolute local-machine paths are recorded here.
