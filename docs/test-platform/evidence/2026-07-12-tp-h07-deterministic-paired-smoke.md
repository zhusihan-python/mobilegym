# TP-H07 Deterministic Paired-Comparison Browser Smoke Evidence - 2026-07-12

## Status

| Field | Value |
|---|---|
| Task | `TP-H07` |
| Status | Complete |
| Base revision | `46220a2` (TP-H06) plus this working tree |
| Branch | `main` |
| Recorded at | `2026-07-12` |
| Live model required | No |
| Network service required | No |
| Browser | Playwright Chromium automated smoke |

## Fixture

The paired scenario uses a plain two-task batch (no `linear_sequence`, since
Manual Sequence forbids a compare node). The matrix carries a baseline and a
candidate lane; the candidate lane carries a test-only `failing_task_id` that
flows into `PlannedLane.runner_config`, so the deterministic agent can produce
one regression and one stable pair. A gate node enforces `max_regressions: 0`
so the comparison gate fails deterministically.

The deterministic adapter is unchanged in composition: the same test-only
resolver from TP-H06 is reused, and `_execute_paired` carries its factories into
the `PairedSerialRunExecutor` unchanged.

## Unified failure mechanism

The failure signal moved from the judge (which previously read `sequence_index`)
to the agent + environment marker channel:

1. `DeterministicAgent` receives a `failing_task_id` (candidate lane only) and
   remembers the current task id from `reset(instruction)`. It emits
   `Action.complete("deterministic_failure")` on the nominated task (paired) or
   on `"sequence step 2"` (single-lane TP-H06 compatibility).
2. `DeterministicEnvironment.step` writes `marker = "deterministic_failure"` on
   that completion message (otherwise `"mutated"`).
3. `DeterministicTask.evaluate` reads the final marker from
   `judge_input.apps["fake"]["marker"]` and returns
   `JudgeResult.fail("deterministic failure at sequence step 2")` so the TP-H06
   failure reason is preserved verbatim.

`PairedSerialRunExecutor` reuses one environment across a lane's episodes, so
`DeterministicTask.setup` calls `env.reset_episode()` (marker, step count, agent
message) before the executor's integrity `get_state` check. `SerialRunExecutor`
builds a fresh environment per episode, where the reset is a no-op, so TP-H06's
isolation guarantee is unchanged.

## RED tracer (before the adapter change)

The REST tracer was written first against the existing adapter. Before the
unified marker change, the second episode of each lane hit
`initial_marker != "clean"` (paired env reuse) and returned
`deterministic isolation violation`, producing a `pairing_violation`
classification instead of the expected `regression`:

```
AssertionError: ['pairing_violation', 'stable_pass'] == ['regression', 'stable_pass']
```

## GREEN tracer (after the adapter change)

After the unified marker + per-episode reset, the REST tracer asserts:

- 2 prepared episode identities;
- baseline + candidate attempts pair by `episode_key` with identical keys per pair;
- classification multiset is exactly `{regression, stable_pass}`;
- every pair integrity is OK;
- coverage: `total_pairs=2, paired_pairs=2, unpaired_pairs=0, coverage_rate=1.0`;
- classification counts: `regressions=1, stable_pass=1`;
- gate verdict `failed` with reason `max_regressions` (threshold 0, observed 1).

## Chromium browser smoke

The browser smoke launches a paired run through the public Vite console, waits
for `completed`, and asserts:

- `tp-comparison` renders 2 pair rows;
- pair classification multiset (scoped by `tp-comparison-pair-{pair_key}`) is
  `{regression, stable_pass}`;
- every pair integrity contains `ok` (case-insensitive);
- `tp-gate-verdict` is `failed`;
- `tp-report-regressions` is `1`;
- `tp-report-runtime-delta` is present and not `—`;
- the replay picker defaults to the candidate lane; switching to the baseline
  option for the regression episode's `episode_key` loads its screenshot, and
  switching back to candidate shows no identity drift.

## Timing sensitivity

The paired run executes two lanes serially, each running two episodes with a
short `asyncio.sleep` per step. The smoke uses explicit Playwright/httpx
timeouts (30 s for run completion, 15 s for comparison/picker load); the only
CI risk is the aggregate suite timeout, not a per-step race.

## Verification

| Phase | Result |
|---|---|
| RED tracer | `['pairing_violation', 'stable_pass'] != ['regression', 'stable_pass']` |
| GREEN tracer | 1 passed in ~2.6 s |
| Paired browser smoke | 1 passed in ~7 s against real Chromium |
| TP-H06 regression guard | original 3 tests still `PASS/FAIL/PASS` with the unchanged failure reason |
| Focused TP-H06+H07 suite | 5 passed in ~17 s (stable across 3 consecutive runs) |
| Paired integration tests | `test_paired_serial_run.py` + `test_paired_parallel_run.py` — 18 passed |
| Frontend type check | `npm run platform:typecheck` passed |
| Frontend vitest | 55 passed across 19 files |
| Full Test Platform regression | 301 passed in ~76 s |

No assertion was skipped or weakened. All smoke storage and services are
temporary and no external model endpoint, credential, simulator deployment, or
pre-existing run directory is used.

## Review gap fixes

A post-commit source review identified three acceptance gaps that were closed
before final sign-off:

1. **Pair coverage in the browser**: the comparison panel now renders
   `total_pairs / paired_pairs / unpaired_pairs / coverage_rate` from the report
   (`tp-comparison-coverage`), and the browser smoke asserts all four values.
2. **Replay switch false-positive**: the lane switch now asserts picker value,
   Agent console Lane + Episode text, and that the screenshot `src` changes to a
   distinct artifact and reverts on switch-back. The baseline option is matched
   by exact parsed `episode_key`, not substring.
3. **Shared prepared identity**: the REST tracer now asserts per pair that
   `prepared.projection_hash`, `integrity.prepared_projection_hash`,
   `integrity.baseline_actual_projection_hash`, and
   `integrity.candidate_actual_projection_hash` are all equal, and that each
   pair_key maps uniquely to an identity with a non-null `instance_seed`.
