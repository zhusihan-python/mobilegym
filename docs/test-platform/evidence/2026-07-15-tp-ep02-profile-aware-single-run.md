# TP-EP02 Profile-Aware Single Run Evidence - 2026-07-15

## Status

| Field | Value |
|---|---|
| Task | `TP-EP02` |
| Status | Complete |
| Requirements | `EP-FR-002`, `EP-FR-003`, `EP-FR-004`, `EP-FR-015` |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-15` |
| Live model required | No |
| Next task | `TP-EP03` not started |

## Delivered vertical slice

The Test Platform now launches one profile-aware Single Run through the public
`RunLaunch.preview/create` seam. A published Workflow v2 declares one
`candidate` Lane Slot; launch binds that slot to an exact current healthy
Target Revision and an exact published no-secret Execution Profile Revision.

Preview returns the resolved Lane Binding, exact public and revision hashes,
episode count, fingerprint inputs, Run Plan fingerprint, and a canonical
preview token without writing durable state. Create re-resolves the command
inside an immediate transaction, rejects token drift, persists the complete Run
graph and Run Plan v2, finalizes the plan artifact, and dispatches the Run once.

## Frozen identity and compatibility

Run Plan v2 stores the exact Workflow Version ID/hash, task source, one
self-contained public Execution Profile snapshot, exact Target/Profile revision
identities, an explicit effective runner configuration projection, episodes,
materialization, evaluation, comparison intent, gates, and artifact contract.
Run and Lane fingerprints exclude volatile Run IDs, timestamps, and artifact
roots while including both frozen revision identities.

Versioned readers accept Run Plan and Workflow schemas 1 and 2. Existing schema
1 Runs continue to expose honest Legacy Execution Identity. Schema 2 Run detail
exposes Profile-Aware Execution Identity with the profile name, revision number
and ID, public hash, target revision, and Lane fingerprint. The v2 Lane retains
the existing `runner_config` consumer contract so the current single-lane
executor can consume the frozen effective configuration.

## Structured failure and transaction evidence

- Workflow v2 validation rejects embedded Target/Profile identities and inline
  Agent/model settings.
- Launch rejects `latest`, draft selectors, missing or duplicate Lane Slots,
  cross-Project revisions, a stale/non-current Target Revision, an archived
  profile, secret-requiring revisions, and non-Single comparison intent.
- A stale preview token writes no Run, Run Attempt, Lane, episode, event,
  idempotency record, or finalized artifact and does not dispatch.
- The same idempotency key replays the original Run for the same request and
  conflicts for a different request. A key used by a failed stale-token request
  remains available for a corrected create, demonstrating rollback without a
  leaked idempotency record.
- Migration 17 adds nullable profile-revision identity columns to `lanes`, so
  legacy Lane rows require no rewrite or synthetic profile identity.

## Console evidence

`/test-platform/run-launch` lists published Workflow v2 versions, current
healthy Target Revisions, and published Execution Profile Revisions. The page
defaults the seed to `20260715`, previews exact hashes and fingerprints, creates
with an idempotency key, and navigates to Run Detail. After unmount/reload, Run
Detail shows the same exact Target/Profile identity and Lane fingerprint.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Public launch seam | `test_run_launch_preview_resolves_one_exact_profile_aware_lane_binding`: the `RunLaunch` module did not exist | Preview resolves one exact profile-aware Lane Binding |
| Workflow identity ownership | `test_workflow_v2_publishes_one_target_free_candidate_lane_slot`: Workflow v1 embedded bound targets and execution settings | Workflow v2 publishes one identity-free `candidate` Lane Slot |
| Durable launch | `test_run_launch_create_persists_run_plan_v2_before_dispatch`: Run Plan v1 could not represent an Execution Profile Revision | Create persists Run Plan v2 and the complete Run graph before one dispatch |
| Revision drift | `test_stale_preview_token_has_no_run_dispatch_or_idempotency_side_effect`: a preview had no canonical concurrency token | Create re-resolves in-transaction and rejects token drift without side effects |
| Fingerprint identity | `test_run_and_lane_fingerprints_are_stable_and_revision_sensitive`: renaming a profile changed the exact-revision plan fingerprint | Mutable profile display metadata is excluded from the canonical fingerprint |
| Artifact rollback | `test_run_launch_artifact_write_failure_has_no_durable_or_file_side_effect`: a failed pre-commit write left a temporary artifact | Pre-commit artifact failures remove the complete temporary root |
| Executor compatibility | `test_profile_aware_run_plan_executes_through_the_single_lane_adapter`: no test loaded Run Plan v2 through the real executor | The deterministic single-lane adapter executes the frozen v2 plan |
| Public provenance | `testPlatformRunLaunch.test.tsx`: Run Detail omitted exact Workflow/Profile launch identity | Profile-aware Run Detail and console reload display exact Workflow/Target/Profile identity |
| Migration compatibility | `test_profile_aware_lane_columns_are_nullable_for_legacy_runs`: Lane schema had no profile-revision columns | Migration 17 adds nullable exact identity columns and preserves legacy rows |

## Verification

| Phase | Result |
|---|---|
| Focused backend (Run Plan, RunLaunch module/API, migrations) | 34 passed, 1 existing Starlette/httpx2 deprecation warning |
| Legacy Runs/Workflows API regression | 17 passed, 1 existing warning |
| Focused console | 1 passed |
| TypeScript type check | clean |
| Full frontend | 67 passed across 22 files |
| Full backend | 470 passed; 1 existing paired browser smoke timed out in aggregate and passed alone, plus 1 existing warning |
| `git diff --check` | clean |

Manual dogfood and a live model were not required. The complete TP-EP02
behavior is observable through deterministic compilation, temporary SQLite and
artifact roots, HTTP, console, reload, and executor-compatible Run Plan seams.
TP-EP03 has not started.

The paired TP-H07 browser smoke passed when rerun alone. Three aggregate reruns
each completed the other 470 backend tests and timed out at different
asynchronous settle assertions in that same smoke. Page snapshots showed the
run data arriving, matching the pre-existing flake already recorded by TP-EP01;
no TP-EP02 code path or assertion failed.
