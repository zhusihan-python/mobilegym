# TP-EP04 Profile Lifecycle and Revision Discovery Evidence - 2026-07-15

## Status

| Field | Value |
|---|---|
| Task | `TP-EP04` |
| Status | Complete |
| Requirements | `EP-FR-001`, `EP-FR-002` |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-15` |
| Live model required | No |
| Next task | `TP-EP05` not started |

## Delivered vertical slice

Execution Profiles now provide a complete project-scoped catalog lifecycle.
Active names are unique after whitespace/case normalization and are released
only when a profile is archived. Draft writes carry a monotonically increasing
`draft_version`; publish and archive commands carry exact draft and head
identity. Transaction-time mismatches return stable stale-state conflicts.

Publishing changed canonical public content or Credential Reference identity
creates the next immutable revision. Publishing unchanged public and binding
digests returns the current head without revision churn. Exact historical
revisions remain readable and discoverable after later publication or archive.

Revision discovery exposes ordered immutable history and a public field diff.
The diff contains public spec paths and an opaque binding-digest path only; it
never contains a Credential Reference ID, private locator, or secret value.
Cloning one exact revision creates a new editable draft with the same redacted
credential readiness and no copied secret value.

Archive removes a profile from default discovery, releases its active name,
and blocks new initial Run Launch previews. Runs created before a newer head or
archive keep the original Execution Profile Revision and frozen public snapshot.

## Console evidence

The Execution Profiles workspace now supports draft editing, subsequent
publication, exact revision history, latest-revision public diff, cloning any
listed revision, archive, and an explicit archived-discovery toggle. Every
draft, publish, and archive mutation sends the displayed draft/head identity.
Archived profiles are read-only while their history and clone actions remain
available.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Revision lifecycle | Profile views had no draft concurrency identity and second publication was rejected | Draft versions and exact head tokens support idempotent unchanged publication and monotonic revisions |
| Name/archive lifecycle | `ArchiveProfile` did not exist and active names were not constrained | Partial unique index, stable conflicts, archived discovery, and released-name reuse |
| Revision discovery | `CloneProfileRevision`, history, and public diff were absent | Ordered revision history, redacted leaf diff, and exact revision clone |
| HTTP lifecycle | Revision history endpoint returned 404 | HTTP exposes token-aware update/publish, history, diff, clone, archive, and archived discovery |
| Run boundary | Archive behavior was not covered through Run Launch | Archived profiles block new initial Runs while an existing Run Plan remains exact |
| Console tokens | Publish sent no concurrency body | Console sends exact draft/head identity and permits subsequent revisions |
| Console lifecycle | No edit/history/diff/clone/archive controls existed | One deterministic user flow exercises the complete workspace lifecycle |

## Verification

| Phase | Result |
|---|---|
| Focused backend (Execution Profile and Run Launch APIs) | 30 passed, 2 existing warnings |
| Final service/API/Run Launch focused regression | 34 passed, 2 existing warnings |
| Focused Console (Execution Profiles and Run Launch) | 3 passed across 2 files |
| Execution Profile service/secret/launch regression | 25 passed, 1 existing Starlette/httpx2 warning |
| Migration regression | 11 passed, 1 existing Starlette/httpx2 warning |
| TypeScript type check | clean |
| Full Platform Console | 68 passed across 22 files |
| Full backend | 493 passed, 1 failed, 2 warnings; the pre-existing paired browser smoke shared-SQLite race again made follow-up previews temporarily unavailable in `test_browser_observes_paired_baseline_candidate_comparison_and_replay` |
| `git diff --check` | clean |

Manual dogfood and a live model were not required. TP-EP05 has not started.
