# Test Platform Execution Profiles TP-EP05 Handoff - 2026-07-16

## Suggested Skills

- `implement`: use only after the user explicitly authorizes the next delivery
  slice. The next planned slice is TP-EP06.
- `tdd`: use with `implement`; keep tests at the public launch, prepared-state,
  comparison, report, replay, and Console seams named by the delivery plan.
- `code-review`: run the Standards and Spec axes against the fixed base before
  committing the next slice.
- `diagnosing-bugs`: use only if a reproducible failure appears in existing
  compatibility or browser coverage; preserve evidence of the failing public
  interface before changing production code.
- `handoff`: use again when another slice needs a compact, cross-machine
  checkpoint.

If these skills are unavailable on the next PC, follow the same workflows
manually. Do not use MobileGym bench-task authoring skills unless the requested
work actually changes `bench_env/task/` or its judges/tests.

## Purpose Of The Next Session

Resume the approved Execution Profiles delivery sequence after TP-EP05. The
next planned slice is TP-EP06, "Run an Execution Comparison," but it has not
started and still requires a separate explicit authorization from the user.

Do not infer authorization from this document. A review-only request permits
inspection and reporting only. If the user authorizes TP-EP06, use
`implement` + `tdd`, then `code-review`, then commit. Dogfood or a live model is
not required for the deterministic acceptance path unless the user explicitly
requests it.

## Authoritative References

Read these instead of reconstructing their contracts from this handoff:

- Repository instructions: `AGENTS.md`.
- Domain language: `docs/test-platform/CONTEXT.md`.
- Product requirements: `docs/test-platform/EXECUTION_PROFILES_PRD.md`.
- Accepted architecture: `docs/test-platform/EXECUTION_PROFILES_ARCHITECTURE.md`.
- Delivery sequence and TP-EP06 contract:
  `docs/test-platform/EXECUTION_PROFILES_DELIVERY_PLAN.md`, section `TP-EP06`.
- Current product status: `docs/test-platform/PRODUCT_BACKLOG.md`.
- TP-EP05 mechanical evidence:
  `docs/test-platform/evidence/2026-07-16-tp-ep05-profile-aware-target-comparison.md`.
- TP-EP05 implementation commit: `931d8b4`.

The code is authoritative for descriptive behavior. The unchecked TP-EP06
acceptance criteria in the delivery plan are prescriptive.

## Git And Synchronization State

At the checkpoint captured by this handoff:

- Branch: `main`.
- HEAD: `931d8b4 TP-EP05: run profile-aware target comparisons`.
- TP-EP05 base: `b0f7d08 TP-EP04: complete profile lifecycle and revision discovery`.
- Local `main` is two commits ahead of `origin/main`.
- `.agents/` remains untracked and must never be committed.
- No TP-EP06 files or changes exist.

This handoff file is intentionally stored under `docs/handoff/`, not in an OS
temporary directory. It is new after commit `931d8b4`; commit and push it, plus
the two local commits ahead of `origin/main`, before expecting another PC to
see the checkpoint. Recheck `git status --short --branch` immediately after
syncing on the other PC.

## Completed Delivery State

TP-EP00 through TP-EP05 are complete. Do not redo them without a new failure or
an explicit request.

TP-EP05 moved Target Comparison onto Workflow v2 Lane Slots and exact
profile-aware Lane Bindings. The full behavior, constraints, public consumer
coverage, and verification record are in the TP-EP05 evidence document and
commit; this handoff does not duplicate them.

The final review cycle closed all findings:

- Spec review: no findings.
- Standards review: no findings after normalizing the `Prepared Episode`
  terminology.
- Public Console acceptance covers Run detail reload, Comparison and gate,
  replay Lane switching, exact Target/Profile identity retention, and immutable
  incident selection.
- Acceptance tests observe backend behavior through public Run detail and
  Comparison interfaces rather than repository or SQL side channels.

One pre-existing browser flake was root-caused during the final full run:
concurrent FastAPI reads shared a SQLite connection and could intermittently
raise `sqlite3.InterfaceError`. Commit `931d8b4` disables the CPython statement
cache for that shared connection. A 250-round concurrent preview diagnostic
stayed green, the paired browser test passed, and the final backend suite was
fully green.

## Verification Baseline

Latest completed results:

- Focused paired backend and Run Launch API: 31 passed, 2 existing warnings.
- Focused Console: 7 passed across 3 files.
- Full Test Platform backend: 501 passed, 2 existing warnings.
- Full Platform Console: 69 passed across 22 files.
- `npm run platform:typecheck`: clean.
- `git diff --check`: clean before commit.
- `.agents/` staged-file exclusion check: clean.
- Manual dogfood/live-model run: not required.

The backend suite was run with the repository's `uv` dependency overlay:

```powershell
uv run --with-requirements test_platform/requirements-dev.txt `
  --with-requirements bench_env/requirements.txt `
  pytest -c test_platform/pytest.ini test_platform/tests -q
npm run platform:test
npm run platform:typecheck
```

The two warnings are existing Starlette/httpx deprecation and Windows asyncio
transport cleanup warnings; neither changed the test outcomes.

## TP-EP06 Starting Points

Use the TP-EP06 section of the delivery plan as the source of requirements.
Start code inspection at:

- Launch command/domain shape: `test_platform/domain/run_launch.py`.
- Run Launch validation and transaction boundary:
  `test_platform/services/run_launch.py`.
- Frozen comparison policy and Lane snapshots:
  `test_platform/domain/run_plans.py`.
- Workflow v2 Lane Slot policy: `test_platform/domain/workflows.py`.
- Profile revision snapshots and deterministic adapters:
  `test_platform/domain/execution_profiles.py` and the existing execution
  profile integration tests.
- Public Run Launch API: `test_platform/api/routes/run_launch.py`.
- Console launch model: `web/test-platform/features/run-launch/model.ts`.
- Console form and preview:
  `web/test-platform/features/run-launch/components/RunLaunchForm.tsx` and
  `RunLaunchPreview.tsx`.
- Existing public consumer coverage: `tests/testPlatformRunLaunch.test.tsx`,
  `tests/testPlatformPairedResult.test.tsx`, and
  `tests/testPlatformRunObservatory.test.tsx`.

The TP-EP06 focused files prescribed by the delivery plan are not yet present
where they are named as new tests. Create them only after authorization:

- `test_platform/tests/integration/test_execution_comparison_launch.py`.
- `tests/testPlatformExecutionComparison.test.tsx`.

Keep the first implementation causal and narrow: Execution Comparison holds
one exact Target Revision constant while varying exactly two Execution Profile
Revisions. Reject no-variation and mixed-axis requests before durable writes.
Do not add provider-defined comparison callbacks, consult mutable profile heads
while rendering history, or re-prepare the shared episode per Lane.

## Required Working Conventions

- The user communicates in Chinese; Test Platform documentation and Console UI
  remain in English.
- Preserve `.agents/` as local untracked material.
- Use explicit file staging; never stage the whole worktree blindly.
- Keep acceptance tests on agreed public seams. Do not verify through internal
  repository calls or direct SQL when a public API/UI exists.
- Use `RUN_COMPARISON_CONSTRAINT_VIOLATED` for the Run Launch constraint error;
  the legacy create-run path retains its legacy error contract.
- Keep Run Launch state/model logic in its feature-local `model.ts`; do not
  regrow `RunLaunchPage.tsx` or `WorkflowsPage.tsx` with extracted concerns.
- After implementation, run focused tests, full backend, full Console,
  TypeScript, `git diff --check`, current-status searches, and the `.agents/`
  staged exclusion check before committing.

## Redaction Note

No API keys, passwords, credential values, personal identifiers, or
machine-specific service secrets are included. All operational paths in this
document are repository-relative so the handoff remains usable after cloning
or syncing to another PC.
