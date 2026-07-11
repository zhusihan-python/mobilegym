# TP-H03 Paired Lane Outcome Commit Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Task | `TP-H03` |
| Status | Complete |
| Base revision | `74d11ff` |
| Branch | `main` |
| Recorded at | `2026-07-11T20:12:59+08:00` |
| Live model required | No |

## Interface adoption

Paired serial and paired parallel execution now adapt each lane's observed
outcomes into the existing `LaneOutcomeCommitter.commit(batch, events)`
interface. The committer retains one operation; its immutable batch can leave
the run open after atomically inserting all expected episode attempts and
finalizing one lane. This paired mode preserves the required sequence:

1. canonical baseline lane facts and one baseline finalization;
2. canonical candidate lane facts and one candidate finalization;
3. one comparison and its complete pair set;
4. one run finalization.

The former paired-only ingestion, missing-result synthesis, artifact-root
selection, pairing-violation event emission, and per-lane finalize calls were
removed. Pair integrity and path-diff implementations remain in their existing
serial/parallel comparison adapters.

## Outcome parity

A deterministic candidate-crash fixture runs through both paired adapters. The
baseline completes before the candidate environment fails without returning a
result. Both modes expose the same public report input:

- baseline attempt: `PASS`;
- candidate attempt: `ERROR / WORKER_CRASH` with an error result body;
- comparison classification: `candidate_error` with `PASS / ERROR` delta;
- functional summary: one success and one error;
- healthy baseline lane: `completed`; crashing candidate lane and run attempt:
  `failed` before the original exception is re-raised;
- real baseline and synthetic candidate terminal events agree with persistence.

Path-level integrity is evaluated only when both lanes produced actual state.
A missing/crashed lane therefore remains an outcome error instead of being
mislabelled as an initial-state pairing violation.

An operator-cancellation fixture begins with a cancelled token. Both paired
adapters create the complete two-lane grid with `CANCELLED / CANCELLED`, persist
two `episode.cancelled` events, finalize both lanes as `cancelled`, persist the
`baseline_error` comparison, finalize the run only afterward, and re-raise
`RunCancelled`. Completed results observed before a later cancellation continue
to retain their original outcomes and completed lane state through the shared
committer.

## Ordering and compatibility evidence

Temporary SQLite guard and audit triggers prove observable ordering without
mocking private helpers:

- comparison insertion aborts unless both lane attempts are terminal and the
  full two-lane episode grid is durable;
- run finalization aborts unless the comparison already exists;
- a successful paired run records exactly two effective lane finalizations,
  one comparison, and one effective run finalization.

Existing successful paired fixtures continue to verify shared prepared task
identity, projection hashes, instruction integrity, path-level state diffs,
prepared DTOs, `stable_pass`, `regression`, and `pairing_violation`
classifications. Existing artifact formats and replay paths are unchanged.

## Verification

| Phase | Result |
|---|---|
| Red | Candidate crash escaped paired serial execution before terminal persistence; `ReportInputRepository` had no readable run attempt |
| New paired contract | 4 passed in 1.31 s |
| TP-H03 focused suite | 50 passed in 24.14 s; one existing Starlette/httpx2 deprecation warning |
| Extended committer/ingestor regression | 72 passed in 25.02 s; same warning |
| Full Test Platform regression | 281 passed in 65.53 s; same warning |

No assertion was skipped or weakened. Generated pytest temporary directories
were removed before commit. The pre-existing user-owned `.agents/` directory
was not modified.
