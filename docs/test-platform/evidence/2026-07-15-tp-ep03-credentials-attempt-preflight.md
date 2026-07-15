# TP-EP03 Credentials and Attempt Preflight Evidence - 2026-07-15

## Status

| Field | Value |
|---|---|
| Task | `TP-EP03` |
| Status | Complete |
| Requirements | `EP-FR-010`, `EP-FR-011`, `EP-FR-015` |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-15` |
| Live model required | No |
| Next task | `TP-EP04` not started |

## Delivered vertical slice

Execution Profile drafts can now declare the `model_api_key` credential slot
and bind it to a project-scoped, request-backed Credential Reference. Draft and
revision reads expose only required, bound, and missing slot names, readiness,
and an opaque binding digest. The private backend locator is stored separately
from the immutable public revision and is never returned.

Credential Reference identity participates in the published revision identity.
Changing the reference changes the binding digest; changing a transient value
supplied behind the same reference changes neither the revision nor Run Plan
fingerprint. Updating public draft fields preserves an omitted private binding,
while an explicit empty binding list can clear it.

## Launch ordering and evidence

`RunLaunch.create` now follows the accepted secret/preflight ordering:

1. re-resolve and validate the preview without secret values;
2. compile Run Plan v2 in memory;
3. validate missing and extra request slots;
4. resolve only declared slots through `SecretResolver`;
5. run Compatibility Preflight on each distinct frozen subject tuple;
6. stop with no Run, Run Attempt, artifact, event, idempotency, secret-store, or
   dispatch side effect when resolution or preflight fails;
7. write and commit the Run graph with redacted initial Run Attempt evidence;
8. finalize the Run Plan artifact, register the transient lease, and dispatch.

Initial evidence records outcome, stable code and explanation, latency, cache
status, checked model, checked Image Input Format, and Lane keys. External
resolver/probe text is replaced by allowlisted public identity. Compatibility
evidence and raw secret values are absent from Run Plan v2 and all fingerprints.

## Console evidence

The Execution Profiles page can require a model credential, publishes only a
request-backed reference identity, and displays credential readiness without
the private locator. Run Launch renders password inputs from previewed required
slots, clears their in-memory values whenever preview identity changes, sends
them only in the create request, and never writes them to browser storage. Run
Detail displays the initial attempt's redacted Compatibility Preflight evidence.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Private publication | `CredentialReferenceBindingInput` could not be imported | Draft/revision publication stores private bindings and returns readiness/digest only |
| HTTP redaction | Profile HTTP create ignored credential bindings | HTTP accepts private binding input and returns only redacted readiness |
| Compatible launch | `RunLaunch` had no `SecretResolver` or preflight dependency | Declared secret is resolved, checked, recorded on attempt 1, then registered after artifact finalization |
| Unavailable secret | External resolver text and private details reached the API error | Stable allowlisted unavailable error returns with zero side effects |
| Failed preflight | Probe-controlled checked fields could leak provider text | Evidence and errors use the frozen public model and Image Input Format |
| Failure matrix | Missing, extra, incompatible, and indeterminate cases lacked profile-aware coverage | Every case fails before resolution or durable side effects as applicable |
| Identity stability | Secret rotation and reference changes were not distinguished | Reference changes alter the digest; transient value rotation leaves revision/plan identity stable |
| Console | Profile and launch pages supported only no-secret revisions | Readiness, transient password input, clearing, create-only submission, and attempt evidence are observable |
| Draft update | Omitting bindings during public draft update cleared the private reference | Omitted bindings are preserved; explicit binding input controls replacement/clearing |
| Unknown probe code | Provider-controlled codes could enter public evidence unchanged | Codes outside the compatibility allowlist become `indeterminate` with an allowlisted explanation |
| Unsupported backend | Runtime values outside the annotated request backend were accepted | Draft validation rejects unsupported backends with a stable redacted profile error |

## Verification

| Phase | Result |
|---|---|
| Focused backend (preflight unit/API and Execution Profile secrets) | 40 passed, 1 existing Starlette/httpx2 warning |
| Focused Console (Execution Profiles and Model Compatibility) | 5 passed across 2 files |
| Additional Run Launch Console | 1 passed |
| TypeScript type check | clean |
| Full Platform Console | 67 passed across 22 files |
| Full backend | 487 passed, 1 failed, 1 existing warning; the pre-existing paired browser smoke shared-SQLite race made follow-up previews temporarily unavailable in `test_browser_observes_paired_baseline_candidate_comparison_and_replay` |
| Paired browser smoke isolated rerun | 1 failed with the same pre-existing follow-up-preview shared-SQLite race |
| `git diff --check` | clean |

Manual dogfood and a live model were not required. TP-EP04 has not started.
