# TP-EP07 Profile-aware Follow-up Evidence - 2026-07-17

## Status

| Field | Value |
|---|---|
| Task | `TP-EP07` |
| Status | Complete |
| Requirements | `EP-FR-011`, `EP-FR-012` |
| Base revision | `6da89c3c3a1478a9b13f68eeb6525752c3887769` |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-17` |
| Live model required | No |
| Next task | `TP-EP08` not started; separate authorization required |

## Delivered vertical slice

Retry and Resume preview now return the canonical Execution Identity already
frozen in the original Run Plan. Profile-aware previews therefore expose exact
Target Revision IDs/hashes, Execution Profile Revision IDs/hashes, public
profile hashes, and Lane fingerprints without accepting replacement selectors.
Legacy previews continue to expose honest Legacy Execution Identity.

The follow-up request boundary rejects unknown Target, profile, Agent, model,
generation, Judge, scheduling, and other top-level overrides. Existing nested
execution validation still permits only transient `model_api_key`, and Run Plan
v2 accepts that secret only when its frozen profile snapshot declared the
original credential slot.

Profile-aware Retry now performs the same frozen Target Revision and task-source
executability gate as Resume before compatibility preflight or Attempt creation.
Missing/undeclared credentials, stale Targets, and incompatible frozen subjects
return structured errors with no new Run Attempt. Legacy Retry behavior remains
unchanged; existing Legacy Resume validation remains intact.

Publishing a newer profile revision and archiving the profile does not alter or
block a follow-up. A successful Retry continues to use the original two profile
revisions and Prepared Episodes and records redacted Compatibility Preflight
evidence on its own Run Attempt.

## Console evidence

The Retry/Resume panel renders a read-only Frozen follow-up Lane Bindings card
for profile-aware Runs. It shows the exact Target Revision, Execution Profile
Revision, and Lane fingerprint for each Lane and provides no revision selector.
Reload retains the same identities. Historical attempt evidence URLs keep their
original Lane/Episode/attempt query selection after newer attempts exist.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Follow-up identity | Retry preview raised `KeyError: execution_identity` | Retry and Resume return the original canonical Execution Identity |
| Override boundary | Unknown `target_revision_id` was ignored and Retry returned 202 | Unknown top-level overrides fail validation before the service runs |
| Retry executability | A profile-aware Retry with a newer Target Revision returned 202 | Run Plan v2 Retry rejects stale frozen Target/task-source identity before Attempt creation |
| Required credential | Clearing a v2 secret lease still allowed Retry to return 202 | Required slots are read from frozen profile snapshots and missing secrets fail write-free |
| Undeclared credential | A v2 Run accepted an undeclared `model_api_key` and returned 202 | Only credential slots declared by the frozen Run Plan may be reinjected |
| Console identity | Follow-up UI had episode selection only | Console renders immutable Lane Bindings without Target/Profile selectors and survives reload |

The new-head/archive and incompatible-preflight cases were added as public
characterization/acceptance tests after their underlying frozen-snapshot and
preflight seams were confirmed GREEN.

## Verification

### Exact test inventory

| Phase | Backend count | Backend test-ID SHA-256 | Console count | Console test-ID SHA-256 |
|---|---:|---|---:|---|
| Fixed base | 505 | `89284ff9f4ce050459f00d3fbb88a4aa1679e9ba010d629d70f9f8d0b08090b5` | 71 | `2d95247e6264e39d46eb1b81897acf4e6f08fdf5a3d47961062164b809c7d5fa` |
| Candidate | 512 | `3caaffafa6cbb85a150bc6ef59f105eeec7946a4e8361bcbcea7f60c016daf4b` | 72 | `4a120dff91d40b5f8f8c1db0dc1319dbe961976df05e67e02a04a7dfb1bfafe0` |

The candidate adds exactly seven backend cases and one Console case. Test-ID
lists were captured under `/tmp`, not the repository.

| Phase | Result |
|---|---|
| Delivery-plan focused backend | 20 passed, 1 existing warning |
| Delivery-plan focused Console | 14 passed across 2 files; existing Node runner warning only |
| TypeScript type check | clean |
| Full Test Platform backend | 512 passed, 1 existing Starlette/httpx deprecation warning |
| Full Platform Console | 72 passed across 23 files; existing Node local-storage runner warning only |
| `git diff --check` | clean |

Manual dogfood and a live model were not required. Verification followed the
approved harness-development sequence: state reconciliation, contract freeze,
baseline/final test inventory, TDD tracer bullets, focused and full recursive
suites, evidence capture, then read-only Standards and Spec reviews.

## Read-only review

The candidate was reviewed against repository boundaries and TP-EP07's accepted
contract after full verification. The Standards Review found no secret values
in durable/public identity, no profile-head lookup in follow-up execution, no
new persistence seam, and no Legacy Retry regression. The Spec Review confirmed
exact frozen identity in both previews, write-free failure boundaries,
transaction-time token revalidation, original credential-slot enforcement,
per-Attempt preflight, and immutable historical Console links. No blocking
finding remained before commit.
