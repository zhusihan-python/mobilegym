# TP-H10 Fail Fast Before Incompatible Execution Creation Evidence - 2026-07-12

## Status

| Field | Value |
|---|---|
| Task | `TP-H10` |
| Status | Complete |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-12` |
| Live model required | No |

## What was built

A compatibility preflight gate that checks whether a model endpoint can accept
the screenshot image format used by a vision-capable agent, BEFORE the run
creation or retry/resume transaction commits. Incompatible or indeterminate
checks block creation with zero persistent side effects.

### Design

- **Agent requirement**: `SCREENSHOT_REQUIRED_AGENTS = {"generic_v2"}` in the
  domain layer. Agents not in this set retain their existing launch behavior.
- **Preflight service** (`test_platform/services/compatibility_preflight.py`):
  `CompatibilityPreflight` wraps the TP-H09 probe with a process-local TTL
  cache (300s). Only `compatible` results are cached; failures always recheck.
  Cache key = `agent|base_url|model|image_url_format` (excludes api_key).
- **Skip field**: `skip_compatibility_check: bool = False` on `CreateRunRequest`
  and `FollowupRunRequest`. The console never defaults to skip. A skipped check
  is recorded as `"skipped"` in provenance.
- **Provenance**: The compatibility summary (`outcome`, `code`, `explanation`,
  `latency_ms`, `image_format`, `cached`) is written into
  `RunPlan.agent["compatibility"]`. It is excluded from the fingerprint payload
  so latency/cache metadata does not break reproducibility.

### Insertion points

- **create_run**: preflight runs AFTER plan compilation (so all distinct lane
  effective configs from `plan.lanes` can be checked) but BEFORE artifact write
  or DB transaction. A failed check raises `RUN_COMPATIBILITY_CHECK_FAILED` (409)
  with zero persistent effects (no artifact, no DB rows, no secret store entry).
- **retry/resume**: preflight runs BEFORE secret registration (using a candidate
  API key extracted without mutating the secret store) and BEFORE
  `_create_followup_attempt`'s transaction. A failed check leaves the secret
  store unchanged.

## Verification

| Phase | Result |
|---|---|
| Unit tests (preflight outcomes + TTL cache + provenance + credential-aware caching + delimiter collision + api_key disables cache read) | 12 passed |
| Integration tests (incompatible 409 + zero persistence incl. artifact root + provenance + skip + non-screenshot agent + fingerprint stability + retry skip provenance + retry secret ordering + incompatible retry/resume zero side effects incl. events + secret store snapshot + retry provenance via public API + no raw column leak + multi-lane same-config merge + multi-lane incompatible zero side effects) | 12 passed |
| Full Test Platform regression | 368 passed |
| Frontend vitest | 59 passed |
| TypeScript type check | clean |
| `git diff --check` | clean |

## Review gap fixes

Six P1/P2 issues found in post-commit review were closed:

1. **Process-scoped preflight**: `create_app` constructs a single
   `CompatibilityPreflight` in `app.state`, shared across all RunService
   instances. The cache is thread-safe via a `threading.Lock`.
2. **Follow-up secret ordering**: retry/resume now extract the candidate API
   key WITHOUT registering it, run preflight, and only register secrets after
   preflight passes. A blocked retry/resume leaves the secret store unchanged.
3. **Follow-up provenance**: `_preflight_followup` returns per-lane summaries
   that are persisted in `run_attempts.compatibility_json` (migration 0013).
4. **All-lane checking**: create-run and retry/resume now check ALL distinct
   lane effective configs, not just the first lane.
5. **Credential-aware cache + skip in idempotency**: cache is disabled when an
   API key is present (avoids cross-key reuse); `skip_compatibility_check` is
   included in the request hash.
6. **Multi-lane provenance**: create-run records ALL distinct lane checks as a
   `{"checks": [...]}` list (each with `lane_keys`), not just the first lane's
   result. Follow-up provenance is visible via `GET /runs/{id}` as
   `run_attempts[].compatibility`.

## Observable demo

Launch with a vision-rejecting endpoint and see a structured
`RUN_COMPATIBILITY_CHECK_FAILED` error with zero new runs. Launch the same
workflow with a compatible endpoint and see the redacted compatibility summary
in the run plan's agent block.
