# TP-H02 Lane Outcome Committer Evidence - 2026-07-11

## Status

| Field | Value |
|---|---|
| Task | `TP-H02` |
| Status | Complete |
| Base revision | `49d3194` |
| Branch | `main` |
| Recorded at | `2026-07-11T17:53:26+08:00` |
| Live model required | No |

## Deep module seam

`LaneOutcomeCommitter` is the single-lane outcome commit module. Its interface
is one `commit(batch, events)` operation. The immutable batch contains the run
and lane-attempt identity, lane artifact root, ordered expected episodes,
normalized observations, cancellation cause, and repeat count.

The implementation hides:

- complete validation of expected, unknown, duplicate, and missing keys before
  the first write;
- one SQLite transaction for every episode attempt plus lane, run-attempt, and
  run finalization, with synthetic events emitted only after commit;
- plan-order episode ingestion regardless of observation delivery order;
- canonical `ERROR / WORKER_CRASH` and `CANCELLED / CANCELLED` missing-result
  bodies and matching terminal events;
- stable episode artifact-root resolution;
- preservation of observed results completed before later run cancellation;
- exactly one lane/run finalization after every expected episode receives a
  terminal interpretation.

No database port was introduced: SQLite is a local-substitutable dependency and
`ResultIngestor` is the existing local persistence implementation. Keeping the
committer beside it avoids a circular adapter and preserves a small interface.

## Executor adapters

Each single-lane executor now normalizes its raw result shape before crossing
the seam and calls the same operation once:

| Executor | Raw shape | Adapter identity |
|---|---|---|
| Serial | positional result list | assigns immutable expected episode keys by plan position |
| Parallel | result objects | reads `episode_key` from each object |
| Multi-process | result dictionaries | reads `episode_key` from each dictionary |

The former serial, parallel, and multi-process reconciliation and synthetic
result implementations were deleted. Two tests that directly called the old
parallel private method were replaced by interface-level contract coverage.

## Contract and parity evidence

The new table-driven contract uses temporary SQLite and an in-memory event
adapter. It covers:

- object and dictionary result bodies;
- ordered and unordered delivery with plan-order persistence;
- non-cancelled missing results;
- cancelled missing results while preserving an already completed observation;
- unknown, duplicate, and missing observation keys with zero partial ingestion;
- matching persisted outcome/error code and synthetic terminal event;
- a forced failure on the second episode insert proving the whole batch rolls
  back, lane/run state stays `running`, and no buffered event escapes;
- an audit trigger proving one successful batch performs exactly one effective
  lane finalization.

Two adapter parity scenarios run through serial, parallel, and multi-process
execution. The missing-result fixture compares identical ordered attempts,
artifact roots, synthetic terminal events, and functional report counts. The
non-empty fixture uses `PASS / FAIL / ERROR` observations and deliberately
exercises serial positional results, unordered parallel objects, and unordered
multi-process dictionaries. It compares persisted attempts, real terminal
events, and functional reports. Each persisted artifact root is then resolved
through `ReplayRepository.get_episode_replay()` with a contained trajectory
fixture, proving Run Explorer/replay discovery rather than only comparing path
strings.

During migration, an old serial cancellation assertion expected a completed
`FAIL` episode to be rewritten as `CANCELLED`. The prescriptive PRD requires
completed pre-cancellation results to retain their real outcomes, so the test
now expects the completed `FAIL` plus a synthetic `CANCELLED` for remaining
missing work.

## Verification

| Phase | Result |
|---|---|
| Red | Contract collection failed because `LaneOutcomeCommitter` did not exist |
| Interface contract + adapter parity | 12 passed in 1.65 s |
| Focused executor/ingestor regression | 40 passed in 34.00 s |
| Full Test Platform regression | 277 passed in 65.25 s; one existing Starlette/httpx2 deprecation warning |

## Review closure

The final two-axis review used `49d3194` as the fixed point.

- Spec: PASS with no findings. The reviewer confirmed the single interface,
  non-empty raw adapter parity, atomic rollback, post-commit synthetic events,
  replay discovery, exactly-once lane transition, and the TP-H03 paired-lane
  boundary.
- Standards: no hard violations. One non-blocking P3 judgement noted that the
  two parity fixtures repeat the three-mode dispatch and reuse private fakes
  from adjacent integration modules. Consolidating the broader executor test
  support seam is deferred because it does not affect the public committer
  contract or TP-H02 correctness.

No assertion was skipped or weakened. All generated pytest temporary
directories were removed. The pre-existing user-owned `.agents/` directory was
not modified.
