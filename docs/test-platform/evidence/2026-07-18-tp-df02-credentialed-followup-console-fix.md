# TP-DF02 Credentialed Follow-up Console Fix - 2026-07-18

## Task contract

| Field | Value |
|---|---|
| Task | `TP-DF02` |
| Status | Complete |
| Type | Bug-fix vertical slice |
| Priority | Release-blocking for credential-bound profile-aware follow-ups |
| Base revision | `3a0522717fb657dbe5dcf5ef4fe103e4cd31f6c1` (`TP-DF01: record real-provider dogfood findings`) |
| Source revision | candidate commit containing this evidence |
| Authorization | User authorized planning, implementation, testing, acceptance evidence, and local commit |
| Not authorized | Push, deploy, Legacy removal, provider-registry expansion, or production-data changes |

## Deliverable

The Run detail Console derives transient follow-up credential requirements from
the original frozen Run Plan v2 execution snapshots. An operator can Retry or
Resume a credential-bound profile-aware Run by supplying the model API key
without persisting the secret or changing any frozen execution identity.

## Acceptance predicates

- [x] A terminal Run Plan v2 Run renders `Model API key` when any referenced
  frozen execution snapshot declares `model_api_key` in
  `public_spec.credentials.required_slots`.
- [x] Retry and Resume do not issue a request when that required transient key
  is empty, and the Console explains the requirement.
- [x] Retry and Resume submit only the canonical preview token plus
  `execution.model_api_key`; they do not submit Target, profile, Agent, model,
  endpoint, or other execution overrides.
- [x] The transient input is cleared after a successful follow-up submission
  and the secret is absent from browser local storage.
- [x] Credential-free Run Plan v2 Runs do not render the input or add an
  execution payload.
- [x] Legacy follow-up behavior remains unchanged.
- [x] A deterministic Chromium scenario exercises a credential-bound
  profile-aware Run and successful Console Retry through the public API.
- [x] No backend API, persistence schema, or production probe behavior changes.
- [x] Focused, full Console, full backend, TypeScript, canonical inventory,
  diff, worktree, and temporary-runtime cleanup gates pass.

## Exact verification commands

```bash
npx vitest run --config vitest.platform.config.ts \
  tests/testPlatformRetryResume.test.tsx
npm run platform:typecheck
npm run platform:test
uv run --with-requirements test_platform/requirements-dev.txt \
  --with-requirements bench_env/requirements.txt \
  pytest -c test_platform/pytest.ini \
  test_platform/tests/e2e/test_mvp_smoke.py \
  -k browser_closes_profile_aware_release_contract -q
uv run --with-requirements test_platform/requirements-dev.txt \
  --with-requirements bench_env/requirements.txt \
  pytest -c test_platform/pytest.ini test_platform/tests -q
npx vitest list --config vitest.platform.config.ts \
  | rg ' > ' | LC_ALL=C sort | shasum -a 256
uv run --with-requirements test_platform/requirements-dev.txt \
  --with-requirements bench_env/requirements.txt \
  pytest -c test_platform/pytest.ini test_platform/tests --collect-only -q \
  | rg '::' | LC_ALL=C sort | shasum -a 256
git diff --check
git status --short
```

## Baseline identity

| Test set | Count | Canonical SHA-256 |
|---|---:|---|
| Backend | 518 | `16f7619c389ced6d446b458a1c54814e907ffb061f02637d953095882b57fd99` |
| Console | 75 | `85de079b2d782aa7b395c055b02c94a0adaff4953e541660bb4ba6e1f852046f` |
| Focused Retry/Resume Console | 4 | recorded by the focused Vitest baseline |

## Runtime path

```text
credential-bound published Execution Profile Revision
  -> Run Plan v2 frozen execution snapshot
  -> terminal Run with retryable Lane episode
  -> Retry/Resume preview with frozen identity
  -> transient Model API key input
  -> public follow-up endpoint
  -> new Run Attempt with the same frozen Lane Binding
```

## Dependencies

- TP-EP07 frozen Retry/Resume identity and backend credential validation;
- TP-EP09 transient-only Console secret policy;
- TP-EP10 deterministic Chromium release scenario;
- TP-DF01 real-provider reproduction and root-cause evidence.

## Out of scope

- generic multi-slot credential forms beyond the currently supported
  `model_api_key` slot;
- changes to Credential Reference persistence or secret lease semantics;
- changes to Run Plan v2, follow-up API, executor, provider adapter, report, or
  Baseline contracts;
- Legacy compatibility removal;
- provider registry or mixed-axis comparison work.

## TDD record

| Cycle | RED | GREEN |
|---|---|---|
| Frozen snapshot requirement | A new public-DTO tracer test failed because `Model API key` was absent; 1 failed and 4 passed | `runRequiresModelApiKey` followed each Lane's `execution_snapshot_key` into `execution_snapshots.*.public_spec.credentials.required_slots`; 5 passed |
| Resume and transient-secret contract | The next protection test was added after the shared requirement path was green; it needed no new production behavior | Resume rejected an empty key without a request, sent only the preview token and transient key, cleared the input, and left local storage secret-free; 6 passed |
| Credential-free and Legacy guards | Existing fixtures did not model an empty public v2 snapshot | The credential-free v2 fixture now uses the public DTO shape and asserts no input; the schema-v2 branch does not fall through to Legacy `runner_config`; all 6 focused tests passed |
| Chromium regression closure | The TP-EP10 browser scenario used a credential-free failing subject, so its Retry could not detect the DF01 defect | The failing subject now requires a credential; Run Launch and Retry each accept the transient key, Retry queues Attempt 2, clears the field, and passes the existing local-storage/runtime sentinel scans |

The production change is deliberately small: one safe public-DTO reader and a
schema guard. No request contract, backend service, database, executor,
provider probe, report, or Baseline code changed.

## Verification evidence

| Command or gate | Result |
|---|---|
| Focused Retry/Resume Vitest | **PASS** - 6 passed; fixed base was 4 passed |
| `npm run platform:typecheck` | **PASS** - no diagnostics |
| `npm run platform:test` | **PASS** - 77 passed across 23 files |
| Credential-bound Chromium release-contract focus | **PASS** - 1 passed, 5 deselected, 1 existing warning in 15.63s |
| Full backend suite | **PASS** - 518 passed, 1 existing warning in 112.89s |
| Backend canonical test identity | **PASS** - unchanged from the fixed base |
| Console canonical test identity | **PASS** - two intentional tests added |
| `git diff --check` | **PASS** - clean |
| Temporary browser/runtime cleanup | **PASS** - pytest scratch stack closed and the recursive sentinel scan passed |

The only backend warning is the existing Starlette/httpx TestClient
deprecation. Console tests retain the existing Node `--localstorage-file`
warning. Neither warning was introduced by TP-DF02.

### Canonical test inventory

The canonical commands are the exact sorted test-ID pipelines recorded above.

| Phase | Backend count | Backend test-ID SHA-256 | Console count | Console test-ID SHA-256 |
|---|---:|---|---:|---|
| Fixed base (`3a05227`) | 518 | `16f7619c389ced6d446b458a1c54814e907ffb061f02637d953095882b57fd99` | 75 | `85de079b2d782aa7b395c055b02c94a0adaff4953e541660bb4ba6e1f852046f` |
| Candidate | 518 | `16f7619c389ced6d446b458a1c54814e907ffb061f02637d953095882b57fd99` | 77 | `f7fda5cfe99161eff2e5d6b9bff9aa4b4e5a9cc7a8da1d12adf40e837078a3c7` |

Backend identity is unchanged because the existing Chromium test was
strengthened in place. Console identity adds exactly two behavior tests.

## Acceptance conclusion

TP-DF02 is complete. Credential-bound profile-aware Retry and Resume now use
the frozen Run Plan v2 credential declaration through the normal Console,
while credential-free v2 and Legacy behavior remain unchanged. The transient
key is still request-only, cleared after successful submission, and covered by
both local-storage and complete scratch-runtime sentinel scans.

This mechanically closes the Console defect found by TP-DF01. A separate live
commercial-provider rerun is supplementary; it is not required to prove this
public DTO/UI fix because the strengthened Chromium scenario exercises the
same Console, public Run Plan v2 response, follow-up endpoint, secret boundary,
and cleanup contract deterministically.
