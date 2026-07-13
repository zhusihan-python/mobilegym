# TP-H15 Recovery and Incident URL Evidence - 2026-07-13

## Status

| Field | Value |
|---|---|
| Task | `TP-H15` |
| Status | Complete |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-13` |
| Live model required | No |

## Server-authoritative follow-up previews

Retry and Resume now expose typed, read-only preview endpoints. Both previews
delegate selection to the existing retry/resume selectors and return the source
run attempt, every selected lane episode and reason, an executable flag, an
empty-state explanation, and a canonical preview token.

The console loads both previews before enabling either action and submits the
corresponding token when the operator confirms. The service validates the token
again inside the follow-up transaction. If the source attempt or selection
changed, it returns the structured `RUN_FOLLOWUP_PREVIEW_STALE` conflict instead
of creating a different attempt. Empty selections remain non-mutating and the UI
shows the server explanation.

## Observatory URL contract

Run Observatory state is represented by validated `lane`, `episode`, `attempt`,
`step`, `screenshot`, and `evidence` query parameters managed through React
Router. Manual episode, step, screenshot, and evidence changes replace the query
state without changing the run pathname. Invalid or stale values select a
deterministic fallback and display a concise notice; a missing replay step falls
back to the final recorded step.

Diagnostics link to the most exact available replay identity and open the
Diagnostics tab. Manual-sequence report rows, baseline/candidate comparison
rows, episode attempts, and run-attempt history use the same typed URL builder.
Historical links include the original run-attempt number, so a later Retry or
Resume cannot redirect them to the newest attempt.

`Copy incident link` rebuilds the URL from the selected public identities only.
It does not copy unrelated query keys, secrets, artifact roots, or filesystem
paths.

## Deterministic browser coverage

The paired deterministic smoke now starts Vite directly through the local Node
entry point, avoiding the Windows `CreateProcess` inability to execute the
`npx.cmd` shim. In a real Chromium session it:

1. verifies the Retry preview's exact lane episode and reason;
2. selects a replay step, raw screenshot, and Response evidence tab;
3. copies and validates the incident URL whitelist;
4. confirms Retry through the UI and compares the public mutation response with
   the previewed selection;
5. opens the copied attempt-1 link in a clean page after attempt 2 completes and
   verifies both initial navigation and reload preserve the immutable evidence.

## Mechanical coverage

| Contract | Coverage |
|---|---|
| Preview selector delegation and empty explanations | Backend integration assertions for Retry and Resume |
| Exact mutation or stale conflict | Canonical preview token tests plus transaction-time stale validation |
| Preview UI and disabled empty actions | `testPlatformRetryResume.test.tsx` |
| URL round trip and deterministic fallback | `testPlatformRunObservatory.test.tsx` |
| Diagnostic exact-evidence links | `testPlatformDiagnostics.test.tsx` |
| Sequence, comparison, and historical attempt links | Reports and Retry/Resume frontend suites |
| Copy, mutation, clean-page open, reload, immutable attempt | Deterministic Playwright smoke |

## Verification

| Phase | Result |
|---|---|
| Focused backend diagnostics and Retry/Resume | 15 passed, 1 existing Starlette/httpx2 deprecation warning |
| Focused frontend TP-H15 suites | 16 passed across 4 files |
| Deterministic smoke module | 5 passed, including both browser scenarios |
| Full frontend | 64 passed across 20 files |
| Backend excluding deterministic smoke module | 425 passed, 1 existing warning |
| Full backend including deterministic browser smoke | 430 passed, 1 existing warning |
| TypeScript type check | clean |
| `git diff --check` | clean |

No live model or manual dogfood was required: selection, mutation, clipboard,
clean-page navigation, and reload are all covered by deterministic interfaces.
