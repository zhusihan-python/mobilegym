# Test Platform Hardening TP-H13 Handoff - 2026-07-13

## Suggested Skills

- `tdd`: use if the user approves implementation of TP-H13; add migration,
  repository/API, and frontend contract tests before or alongside each slice.
- `diagnose`: use only if an existing baseline or migration test fails in a way
  that needs reproduction and root-cause isolation.
- `ego-browser`: use only if the user asks for a live console dogfood of named
  baseline promotion, discovery, detail, archive, and name reuse.
- `handoff`: use again when TP-H13 or a later hardening slice needs a compact
  checkpoint for another machine or session.

If these skills are unavailable in the next environment, follow the same
workflows manually. Code review by itself does not require a skill.

## Purpose Of The Next Session

Continue Test Platform hardening after TP-H12. The next planned slice is
TP-H13, "Add named, discoverable strict baselines."

The likely first request is to review a TP-H13 implementation plan. Do not start
implementing merely because this handoff identifies TP-H13 as next; when asked
for review, inspect and report only. Implement only after the user explicitly
asks to proceed.

## Authoritative References

Read these instead of reconstructing their contracts from this handoff:

- Overall dependency map and TP-H13 acceptance criteria:
  `docs/test-platform/HARDENING_PLAN.md`, section `TP-H13`.
- Strict selected-lane eligibility already delivered by TP-H05:
  `docs/test-platform/evidence/2026-07-11-tp-h05-strict-baseline-eligibility.md`.
- Reliability/report schema context delivered by TP-H11:
  `docs/test-platform/evidence/2026-07-13-tp-h11-reliability.md`.
- Infrastructure work just completed in TP-H12:
  `docs/test-platform/evidence/2026-07-13-tp-h12-infrastructure.md`.
- Repository-wide instructions: `AGENTS.md`.

The code is authoritative for descriptive behavior. Treat the unchecked TP-H13
acceptance criteria in `HARDENING_PLAN.md` as prescriptive requirements.

## Git And Verification State

Before this handoff file was created:

- Branch: `main`.
- HEAD: `04e7e20 TP-H12: add infrastructure monitor reports`.
- Worktree: clean.
- `git diff --check`: clean.
- TP-H12 received final review approval.

This handoff file is intentionally stored in the repository so it can be read
on another PC after syncing.

### Concurrent merge detected while writing this handoff

After the handoff file was created, a separate Git operation began in the shared
workspace. The repository is now in a merge with:

- local `HEAD`: `04e7e20` (the reviewed, final TP-H12 commit),
- `MERGE_HEAD`: `5aab955`,
- local and `origin/main`: one commit of divergence on each side,
- TP-H12 files showing `AA`/`UU` conflicts,
- this handoff file still untracked and unaffected.

The handoff task did not start this merge and did not resolve, stage, abort, or
overwrite any conflicted file. Before syncing this document to another PC, the
user must decide how to handle that concurrent merge. Preserve `04e7e20` as the
reviewed TP-H12 result; do not accidentally replace it with the older TP-H12
revision while resolving the divergence.

Latest reported verification baseline:

- `pytest test_platform/tests -q`: 422 passed.
- Full Test Platform frontend suite: 59 passed.
- Focused `tests/testPlatformReports.test.tsx`: 2 passed after the final warning
  assertions.
- `npm run platform:typecheck`: clean.

The previous review environment could verify Git state and source diffs but did
not have the project's pytest environment available. The test results above are
the user's executed results and are recorded in the TP-H12 evidence/commit.

## Completed Hardening State

TP-H00 through TP-H12 are complete. Do not reopen or redo them without a new
failure or an explicit user request.

The latest relevant commits are:

- `04e7e20` - TP-H12 infrastructure monitor reports.
- `b2dc066` - TP-H11 reliability and Pass@k reports.
- `e7611ae` - TP-H10 fail-fast compatibility preflight.
- `5fb0419` - TP-H09 screenshot/model compatibility testing.
- `eea8c1c` - TP-H08 post-P1 regression evidence.
- `00da2cb` - TP-H07 deterministic paired browser smoke.
- `46220a2` - TP-H06 deterministic operational browser smoke.
- `811c92e` - TP-H05 strict selected-lane baseline eligibility.

Use `git log` and the evidence links in `HARDENING_PLAN.md` for the earlier
TP-H00 through TP-H04 history rather than duplicating it here.

TP-H12's final review specifically confirmed that bounded monitor discovery,
zero-source scan overflow, the 32-source limit, unavailable collector semantics,
artifact links, public DTOs, and frontend overflow/exclusion warnings have
mechanical coverage.

## TP-H13 Starting Points

The existing baseline capability is promotion-oriented and must be extended,
not replaced. Start source inspection at:

- Persistence schema: `test_platform/persistence/migrations/0009_reports_baselines.sql`.
- Baseline persistence: `BaselineRepository` in
  `test_platform/persistence/repositories.py`.
- Eligibility policy: `test_platform/services/baselines.py`.
- Public routes: `test_platform/api/routes/reports.py`.
- Frontend client and DTOs: `web/test-platform/api/client.ts` and
  `web/test-platform/api/types.ts`.
- Current promotion UI: `web/test-platform/features/runs/RunDetailPage.tsx`.
- Backend integration seam: `test_platform/tests/integration/test_reports_api.py`.
- Migration seam: `test_platform/tests/integration/test_migrations.py`.
- Frontend seam: `tests/testPlatformReports.test.tsx`.

When reviewing a TP-H13 plan, verify that it resolves these contract edges
mechanically:

- A bounded, normalized display-name contract and a structured duplicate-name
  error.
- Active-name uniqueness scoped to project, with archive releasing the name;
  migration/index semantics must work on SQLite.
- Deterministic readable labels for existing anonymous rows without losing
  provenance.
- Public list/detail/archive interfaces, including immutable source report and
  replay identity/linkage.
- List/detail DTOs expose the exact provenance requested by TP-H13 without raw
  persistence-only fields.
- Promotion continues to delegate eligibility exclusively to
  `BaselineEligibility`; do not create a second eligibility implementation.
- Imported runs with incomplete provenance remain visibly ineligible.
- Migration tests cover both fresh databases and upgrades containing legacy
  anonymous baselines.
- Frontend tests cover naming, active duplicate rejection, discovery/detail,
  archive, and reuse of an archived name.
- Trend charts and automatic current-vs-baseline runs remain out of scope.

Pay particular attention to whether a proposed unique constraint handles
archived rows correctly and whether deterministic legacy labels can collide.
Require exact assertions, not tests that only check that fields exist.

## Expected Review And Delivery Style

The user has been working slice-by-slice:

1. They provide a plan and ask for review.
2. After plan issues close, they implement and provide a commit plus validation.
3. Review the actual commit/source, not only the delivery report.
4. Report findings in priority order (`P1`, `P2`, `P3`) with exact file/line
   references and a mechanical regression test where appropriate.
5. A clean test suite alone is not sufficient if the test misses the claimed
   branch or only asserts key presence.
6. Do not edit code during a review-only request.

The final TP-H12 review required several rounds because discovery bounds and
tests initially did not exercise the claimed edge paths. Carry that lesson into
TP-H13: inspect migration upgrade paths, partial uniqueness, archive/name reuse,
legacy data, and public DTO behavior directly.

## TP-H13 Verification Baseline

The focused commands prescribed by `HARDENING_PLAN.md` are:

```bash
python -m pytest -c test_platform/pytest.ini \
  test_platform/tests/integration/test_reports_api.py \
  test_platform/tests/integration/test_migrations.py -q
npx vitest run --config vitest.platform.config.ts tests/testPlatformReports.test.tsx
```

At completion, also run the full Test Platform backend/frontend regression,
`npm run platform:typecheck`, `git diff --check`, and `git status --short`.
Use the repository's configured Python environment on the new PC.

## Redaction Note

No API keys, passwords, credentials, personal identifiers, or machine-specific
service secrets are included. Paths inside this document are repository-relative
so the handoff remains usable after cloning or syncing to another PC.
