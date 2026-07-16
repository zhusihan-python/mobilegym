# TP-EP06 Execution Comparison Evidence - 2026-07-17

## Status

| Field | Value |
|---|---|
| Task | `TP-EP06` |
| Status | Complete |
| Requirements | `EP-FR-003`, `EP-FR-005`, `EP-FR-006`, `EP-FR-007`, `EP-FR-008`, `EP-FR-009` |
| Base revision | `61344079a572fc748e3145ccc571d997feb815a4` |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-17` |
| Live model required | No |
| Next task | `TP-EP07` not started; separate authorization required |

## Delivered vertical slice

Run Launch now accepts `execution_comparison` for paired Workflow v2 Lane
Slots. Preview and create bind both Lanes to one exact Target Revision and two
different immutable Execution Profile Revisions. The compiled Run Plan v2 keeps
the Workflow Version, task source, seed, episode identities, evaluation/Judge
protocol, orchestration policy, and one shared set of Prepared Episodes equal
across the pair.

Preview exposes a redacted public field diff between the baseline and candidate
profile revisions. Authoritative create rejects an identical pair as
`RUN_COMPARISON_NO_VARIATION` and a pair that changes both Target and profile as
`RUN_COMPARISON_CONFOUNDED` before Run, attempt, artifact, idempotency, dispatch,
or run-directory writes.

The explicitly enabled deterministic test composition can derive pass/fail
behavior from the frozen `runner_config.model_name` snapshot for two reserved
test model names. Normal production composition does not enable or import that
adapter. Subject variation therefore cannot re-sample tasks, alter instruction,
or re-prepare initial state.

## Console evidence

The paired Run Launch workspace can switch between Target Comparison and
Execution Comparison. Execution Comparison renders one Shared Target Revision,
separate baseline/candidate Execution Profile Revision selectors, and the public
profile diff. Preview/create requests carry one target ID and two profile IDs.

Created and reloaded Run detail retains both exact profile identities while
comparison classification, gate verdict, diagnostics, candidate/baseline replay
switching, and immutable incident selection remain available. Incident URLs
carry Run/Lane/Episode/attempt selection and do not copy profile specs,
credentials, or redundant revision identifiers into the query string.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Public launch intent | Preview rejected `execution_comparison` with HTTP 422 | HTTP/domain/compiler contracts accept and persist the new causal intent |
| Public profile diff | Preview had no profile-diff field | Preview returns the redacted cross-profile revision diff |
| Deterministic subject behavior | The opt-in resolver rejected frozen `generic_v2` profile subjects | Reserved test-only model snapshots produce deterministic stable/regression outcomes |
| Console launch | Paired launch exposed only Target Comparison and no Shared Target selector | Execution Comparison binds one target, two profiles, previews diff, creates, and reloads |
| Invalid axes | No TP-EP06 regression asserted write-free rejection | No-variation and confounded requests return structured 409 errors with zero durable writes |
| Public consumers | No Execution Comparison fixture proved exact identity retention | Comparison, gate, diagnostics, replay, incident selection, and reload keep both revisions visible |

## Verification

### Exact test inventory

| Suite | Count | Test-ID SHA-256 |
|---|---:|---|
| Backend recursive collection | 505 | `5b3a82548a11ec11819c3cb5e0e50592fecfbf6f576d64291580a08efd49ae90` |
| Platform Console Vitest list | 71 | `13ef5ee16146720ec7e252870c86cf8dac8d1327c07d235b82c4ef01283b5c90` |

The inventory is exactly four backend cases and two Console cases larger than
TP-EP05. Collection artifacts were written under `/tmp`, not the repository.

| Phase | Result |
|---|---|
| Delivery-plan focused backend | 10 passed, 1 existing warning |
| Delivery-plan focused Console | 15 passed across 3 files; existing Node runner warning only |
| TypeScript type check | clean |
| Full Test Platform backend | 505 passed, 1 existing Starlette/httpx deprecation warning |
| Full Platform Console | 71 passed across 23 files; existing Node local-storage runner warning only |
| `git diff --check` | clean |

Manual dogfood and a live model were not required. The verification sequence
followed the repository handoff plus the external harness development guide:
state reconciliation, contract freeze, exact test inventory, focused tests,
full recursive suites, evidence capture, and a read-only specification/standards
review before commit.

## Read-only review

The fixed candidate was reviewed against repository boundaries and TP-EP06's
accepted contract after tests completed. The Standards Review found no secret
material in the public diff, no production startup dependency on the
deterministic adapter, no mutable profile-head lookup during execution, and no
new persistence or provider callback seam. The Spec Review confirmed exactly
one varied axis, one shared Prepared Episode set, structured write-free invalid
axis rejection, and exact two-profile identity across the required public
Console consumers. No blocking findings remained before commit.
