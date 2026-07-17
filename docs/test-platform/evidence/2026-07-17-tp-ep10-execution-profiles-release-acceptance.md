# TP-EP10 Execution Profiles Release Acceptance - 2026-07-17

## Release status

| Field | Value |
|---|---|
| Task | `TP-EP10` |
| Status | Complete |
| Requirements | `EP-FR-001` through `EP-FR-015` |
| Base revision | `a40dd9b6bf22882040fd05f3ab9edd9d4d115222` (`TP-EP09: complete console migration`) |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-17` |
| Live model required | No |
| Release disposition | Execution Profiles release complete; Legacy create-run compatibility window remains active |

TP-EP00 through TP-EP09 were reproduced from a clean worktree before the
release gate was closed. TP-EP10 adds one deterministic profile-aware browser
scenario and explicitly composes the fake compatibility probe only in the
testing server. Normal startup retains the production compatibility probe.

## Delivered release seam

`test_browser_closes_profile_aware_release_contract` starts a fresh SQLite
database, runs directory, deterministic API process, Vite process, and Chromium
context. Through the real Console it creates and publishes passing, failing,
and credential-bound profiles, then exercises:

- Single launch with exact frozen Target/Profile revisions, Compatibility
  Preflight, report provenance, Strict Baseline promotion, reload, and the
  Baseline-to-source-Run historical link;
- Target Comparison with one exact profile revision across two exact Target
  Revisions;
- Execution Comparison with one exact Target Revision and two exact profile
  revisions, public revision diff, deterministic subject-specific outcomes,
  Retry, reload, replay, and a clean-page incident link;
- a missing-secret disabled state followed by deterministic authentication
  failure, with the Run count unchanged;
- browser-storage and complete temporary-runtime scans for a raw secret
  sentinel.

The testing server now injects `FakeCompatibilityProbe` alongside its existing
explicit deterministic executor and Target adapters. `test_platform.main`
continues to use `OpenAICompatibilityProbe`; the normal-composition contract is
part of the focused release gate.

## TDD record

| Phase | Observation | Closure |
|---|---|---|
| Browser contract red | Profile creation/publication and exact preview succeeded, but deterministic create reached the production provider probe and returned an unclassified provider response with zero Runs created | The testing server explicitly received `FakeCompatibilityProbe`; normal startup was left unchanged |
| Browser contract green | The same Console flow completed Single, Target Comparison, Execution Comparison, Retry, report/Baseline, reload/link, and fail-closed credential cases | New focused browser case passed, then the entire six-case Chromium module and full backend suite passed |
| Locator reconciliation | The first draft used an ambiguous Run Launch heading locator | The test now selects the semantic level-2 page heading; no product selector or assertion was weakened |

## Final verification

| Command or gate | Result |
|---|---|
| `uv run --with-requirements test_platform/requirements-dev.txt --with-requirements bench_env/requirements.txt pytest -c test_platform/pytest.ini test_platform/tests -q` | **PASS** - 518 passed, 1 existing Starlette/httpx deprecation warning |
| `uv run --with-requirements test_platform/requirements-dev.txt --with-requirements bench_env/requirements.txt pytest -c bench_env/tests/pytest.ini bench_env/tests/common -m "not live" -q` | **PASS** - 229 passed |
| `npm run platform:test` | **PASS** - 75 passed across 23 files; existing Node local-storage runner warning only |
| `npm test` | **PASS** - 177 passed across 50 files; existing Node local-storage runner warning only |
| `npm run platform:typecheck` | **PASS** - no diagnostics |
| `npx tsc --noEmit` | **PASS** - no diagnostics |
| `npm run lint` | **PASS** - 0 errors, 73 existing React Hook warnings; store-getter check clean |
| `pytest ... test_platform/tests/e2e/test_mvp_smoke.py -q` | **PASS** - 6 passed, 1 existing warning |
| Secret/production-probe/report/Baseline/Legacy focus | **PASS** - 52 passed, 1 existing warning |
| `git diff --check` | **PASS** - clean |

The focused 52-test gate comprises
`test_execution_profile_secrets.py`, `test_model_compatibility_api.py`,
`test_reports_api.py`, `test_baselines_api.py`, and
`test_execution_profile_legacy.py`.

### Canonical test inventory

The test-ID algorithm is unchanged from TP-EP08 and TP-EP09:

```bash
uv run --with-requirements test_platform/requirements-dev.txt \
  --with-requirements bench_env/requirements.txt \
  pytest -c test_platform/pytest.ini test_platform/tests --collect-only -q \
  | rg '::' | LC_ALL=C sort | shasum -a 256
npx vitest list --config vitest.platform.config.ts \
  | rg ' > ' | LC_ALL=C sort | shasum -a 256
```

| Phase | Backend count | Backend test-ID SHA-256 | Console count | Console test-ID SHA-256 |
|---|---:|---|---:|---|
| Fixed base (`a40dd9b`) | 517 | `4b75fcd4fd6801ce9965cbe0687100323f3093323b8fcfc74643564defd9092f` | 75 | `85de079b2d782aa7b395c055b02c94a0adaff4953e541660bb4ba6e1f852046f` |
| Candidate | 518 | `16f7619c389ced6d446b458a1c54814e907ffb061f02637d953095882b57fd99` | 75 | `85de079b2d782aa7b395c055b02c94a0adaff4953e541660bb4ba6e1f852046f` |

The candidate adds one browser release-contract test and does not change the
Console test inventory.

## Requirement evidence matrix

| Requirement | Mechanical evidence |
|---|---|
| `EP-FR-001` | TP-EP01 revision creation/publication plus TP-EP04 lifecycle/diff tests; release browser publishes three profiles |
| `EP-FR-002` | TP-EP02 Run Plan v2 Single compiler, persistence, HTTP, Console, and reload tests; release browser Single flow |
| `EP-FR-003` | TP-EP02 exact Target/Profile Lane Binding and public provenance tests; release browser exact preview and frozen detail |
| `EP-FR-004` | TP-EP02 typed subject settings and TP-EP09 removal of loose Console launch settings |
| `EP-FR-005` | TP-EP02 Single, TP-EP05 Target Comparison, TP-EP06 Execution Comparison, and all three release-browser modes |
| `EP-FR-006` | TP-EP05 same-profile Target Comparison and TP-EP06 same-target Execution Comparison invariants |
| `EP-FR-007` | TP-EP06 shared Target Revision and Prepared Episode identity across subject revisions |
| `EP-FR-008` | TP-EP05 one exact profile revision across the two Target Comparison lanes |
| `EP-FR-009` | TP-EP05/06 structured mixed-axis rejection and causal comparison metadata |
| `EP-FR-010` | TP-EP03 secret redaction/no-side-effect tests, TP-EP09 browser preference scrub, and release runtime sentinel scan |
| `EP-FR-011` | TP-EP03 per-Attempt redacted preflight plus TP-EP07 follow-up Attempt preflight; release detail rendering |
| `EP-FR-012` | TP-EP07 frozen Retry/Resume compatibility tests and release-browser Retry preview/attempt/reload |
| `EP-FR-013` | TP-EP08 report schema v3 and Strict Baseline provenance tests; release-browser report and Baseline flow |
| `EP-FR-014` | TP-EP00 honest Legacy identity, TP-EP08 legacy report/Baseline readers, and TP-EP09 create/read/follow-up characterization |
| `EP-FR-015` | TP-EP03/04/05/06/07 structured fail-closed tests and release-browser missing-secret/auth-failure zero-Run assertion |

## PRD acceptance scenario matrix

| Product scenario | Mechanical evidence |
|---|---|
| Publish two immutable revisions and inspect a redacted diff | TP-EP01/04/06 evidence and Execution Comparison browser diff |
| Single freezes one exact Target and profile revision | TP-EP02 evidence and release-browser Single preview/detail/reload |
| Execution Comparison shares Target and Prepared Episodes | TP-EP06 evidence and release-browser Execution Comparison |
| Target Comparison shares one exact profile revision | TP-EP05 evidence and release-browser Target Comparison |
| Mixed-axis comparison is rejected before creation | TP-EP05/06 integration and Console contract tests |
| Secrets and sensitive paths stay private across APIs, artifacts, links, and exports | TP-EP03/08/09 tests plus the release sentinel and incident-link scans |
| Compatible, incompatible, missing-secret, stale, archived, and cross-project outcomes are deterministic | TP-EP03/04 integration suites and release-browser authentication failure |
| Retry/Resume preserve historical bindings after later publication/archive | TP-EP07 full follow-up suite and release-browser Retry identity |
| Reports and new Strict Baselines freeze selected-Lane provenance | TP-EP08 evidence and release-browser promotion/historical source link |
| Workflow v1, Run Plan v1, reports, Baselines, follow-up, and imports remain readable | TP-EP00/07/08/09 compatibility suites and the 518-test release gate |
| Browser lifecycle works without a live model | Six-case Chromium module and the independent real-browser dogfood below |

## Slice evidence index

| Slice | Evidence |
|---|---|
| TP-EP00 | [`2026-07-13-tp-ep00-legacy-execution-identity.md`](2026-07-13-tp-ep00-legacy-execution-identity.md) |
| TP-EP01 | [`2026-07-15-tp-ep01-first-execution-profile-revision.md`](2026-07-15-tp-ep01-first-execution-profile-revision.md) |
| TP-EP02 | [`2026-07-15-tp-ep02-profile-aware-single-run.md`](2026-07-15-tp-ep02-profile-aware-single-run.md) |
| TP-EP03 | [`2026-07-15-tp-ep03-credentials-attempt-preflight.md`](2026-07-15-tp-ep03-credentials-attempt-preflight.md) |
| TP-EP04 | [`2026-07-15-tp-ep04-profile-lifecycle-revision-discovery.md`](2026-07-15-tp-ep04-profile-lifecycle-revision-discovery.md) |
| TP-EP05 | [`2026-07-16-tp-ep05-profile-aware-target-comparison.md`](2026-07-16-tp-ep05-profile-aware-target-comparison.md) |
| TP-EP06 | [`2026-07-17-tp-ep06-execution-comparison.md`](2026-07-17-tp-ep06-execution-comparison.md) |
| TP-EP07 | [`2026-07-17-tp-ep07-profile-aware-followups.md`](2026-07-17-tp-ep07-profile-aware-followups.md) |
| TP-EP08 | [`2026-07-17-tp-ep08-profile-aware-report-baseline-provenance.md`](2026-07-17-tp-ep08-profile-aware-report-baseline-provenance.md) |
| TP-EP09 | [`2026-07-17-tp-ep09-console-migration-legacy-compatibility.md`](2026-07-17-tp-ep09-console-migration-legacy-compatibility.md) |
| TP-EP10 | This record |

## Independent browser dogfood

An isolated `ego-browser` task space exercised the real Console against a fresh
deterministic API/Vite stack. It created and published a profile, passed the
Compatibility Check, previewed and launched an exact-revision Single Run,
observed completion, frozen identity, attempt preflight, and report provenance,
then reloaded the historical Run successfully. Browser storage contained only
the selected Project identity in this flow and no secret-bearing launch data.
The task space and temporary runtime were removed after verification.

This dogfood used deterministic adapters and no live commercial or local model.
It supplements, but does not replace, the repeatable Playwright release gate.

## Secret and startup audit

- The browser test uses a unique secret sentinel, verifies it is absent from
  local storage, and scans every file under the fresh runtime root after all
  processes stop.
- The same test rejects a sensitive transient Credential Reference path in
  browser storage and verifies the failed authentication preflight creates no
  Run.
- The 52-test focus verifies private Credential Reference redaction, public
  response/export behavior, report/Baseline provenance, Legacy identity, and
  the production-probe default composition.
- Automated browser test-only adapters are enabled by the explicit
  `test_platform.testing.server` composition. The default `create_app` and
  normal `test_platform.main` composition remain production-backed.

## Accepted warnings and deferred work

| Item | Owner | Release disposition |
|---|---|---|
| Starlette warns that `httpx` TestClient support is deprecated in favor of `httpx2` | Test Platform dependency maintenance | Accepted existing dependency warning; migrate separately |
| Node warns that `--localstorage-file` lacks a valid path in Vitest workers | Platform Console test infrastructure | Accepted existing runner warning; no assertion or inventory impact |
| 73 `react-hooks/exhaustive-deps` warnings across existing Apps and OS code | Respective App and OS maintainers | Accepted existing lint backlog; release lint has zero errors |
| Legacy `POST /api/platform/v1/runs` creation remains supported | Test Platform product and architecture owners | Active criteria-based compatibility window; TP-EP10 does not authorize removal |
| Broader provider registry/typed provider variants beyond the current first production adapter | Product and architecture backlog | Deferred product direction; requires its own requirements and delivery authorization |
| Mixed multi-axis comparison | Product and architecture backlog | Intentionally rejected for causal interpretation; any future design requires separate review |

## Release conclusion

All TP-EP10 acceptance criteria are met. TP-EP00 through TP-EP10 have durable,
reproducible evidence; the full backend, benchmark, Console, simulator,
type-check, lint, compatibility, secret, and browser gates are green without a
live model. The Versioned Execution Profiles release is complete. The Legacy
creation compatibility window and deferred provider/multi-axis directions
remain explicitly owned and outside this release's removal authority.
