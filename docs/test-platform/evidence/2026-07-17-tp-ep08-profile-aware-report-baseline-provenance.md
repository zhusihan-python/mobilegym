# TP-EP08 Profile-aware Report and Baseline Provenance Evidence - 2026-07-17

## Status

| Field | Value |
|---|---|
| Task | `TP-EP08` |
| Status | Complete |
| Requirements | `EP-FR-013`, `EP-FR-014` |
| Base revision | `3ae6895a71b9c14c2ca40db0ee1e6cc6cf431c6d` (`TP-EP07: preserve profile-aware followups`) |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-17` |
| Live model required | No |
| Next task | `TP-EP09` not started; separate authorization required |

## Delivered vertical slice

New profile-aware Runs emit report schema v3. Its provenance carries the frozen
Execution Identity, per-Lane Target Revision IDs, Execution Profile Revision
IDs/hashes, Lane fingerprints, and the selected completed Run Attempt's
redacted Compatibility Preflight. Report section builders, gates, replay links,
JSON/HTML export, and diagnostics continue to consume the same selected Attempt
input. Legacy Runs continue to emit or read report schema v2, and stored report
v1/v2 JSON is returned and exported without rewrite or backfill.

Profile-aware Strict Baseline eligibility now requires a report v3 identity,
complete and internally consistent selected-Lane revision/fingerprint maps, the
matching immutable report/Run Attempt, selected-Lane preflight evidence, and the
existing every-planned-episode PASS rule. Eligibility returns the immutable
`report_id`; promotion consumes that exact report rather than re-reading a
possibly newer Attempt report. The new baseline persistence fields freeze the
selected Execution Profile Revision ID/hash, Lane fingerprint, source Run
Attempt, strictness version, and selected-Lane redacted preflight.

Existing Baselines migrate additively with nullable profile-aware fields and
render as `Legacy strictness`; they remain listable, readable, named,
archivable, replayable, and linked to their immutable source report. A legacy
report cannot create a new Strict Baseline and is never described as
profile-aware. Report and baseline history render from frozen Run/Lane/Attempt
and report records, not current Execution Profile heads or archive state.

## Console evidence

The Report panel renders a frozen per-Lane provenance table from the report DTO,
including exact Target Revision, Execution Profile name/revision/ID, revision
hash, and Lane fingerprint. Baseline catalog and detail views distinguish
`Profile-aware strictness v1` from `Legacy strictness`; profile-aware rows show
the exact Execution Profile Revision, and detail also shows the frozen revision
hash, Lane fingerprint, and source Run Attempt.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Report provenance | A completed profile-aware Run returned report schema v2 | New profile-aware reports emit schema v3 with frozen Lane and Attempt provenance |
| Strict Baseline persistence | Promotion returned no `strict_provenance` | Promotion freezes selected Profile revision/hash, Lane fingerprint, Attempt, and preflight |
| Legacy promotion boundary | A legacy report remained eligible for a new baseline | Legacy reports receive `PROFILE_AWARE_STRICT_PROVENANCE_REQUIRED`; historical baselines remain readable |
| Completeness gate | Removing profile hash, Lane fingerprint, or preflight still left eligibility true | Missing or inconsistent selected-Lane provenance fails closed with `STRICT_PROVENANCE_INCOMPLETE` |
| Report Console | The Report panel had no report-owned Lane provenance view | Report v3 provenance renders in its own immutable table |
| Baseline Console | Baseline views showed only Target revision and report version | List/detail distinguish exact profile-aware identity from Legacy strictness |
| Attempt selection review | Eligibility exposed no immutable report identity | Eligibility returns `report_id`; promotion reads that exact report |

Transient credential tests use a committed fake value only as negative evidence:
neither the raw value nor its private locator appears in report read/export
responses. The production report and baseline payloads contain only redacted
preflight fields.

## Verification

### Canonical test inventory

The inventory algorithm is explicit and identical for base and candidate:

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
| Fixed base (`3ae6895`) | 512 | `355a859ab5c10411a3d3bc05d282ed572d1f94a5ba1d91c7791ccb824f93af6d` | 72 | `d19a58ed4c4e03107d6fad7a392c1e7750051b09dfd53aa29e6291d0e6ca7aa2` |
| Candidate | 516 | `1e4ba9f19023849efda696267268063d0c11d7b3becf411889d465b50324249a` | 73 | `2f9bcba0a3d8946ccd7ddd1efb8f72e93d6921b7af7862fc52779453709613cb` |

The candidate adds four backend public-interface cases and one Console case.

| Phase | Result |
|---|---|
| Delivery-plan focused backend | 24 passed, 1 existing warning |
| Focused backend plus migrations | 35 passed, 1 existing warning |
| Delivery-plan focused Console | 6 passed across 2 files; existing Node runner warning only |
| TypeScript type check | clean |
| Full Test Platform backend | 516 passed, 1 existing Starlette/httpx deprecation warning |
| Full Platform Console | 73 passed across 23 files; existing Node local-storage runner warning only |
| `git diff --check` | clean |

Manual dogfood and a live model were not required for this deterministic
acceptance path. Verification followed state reconciliation, contract freeze,
TDD tracer bullets, focused and full recursive suites, canonical evidence
capture, then read-only Standards and Spec reviews.

## Read-only review

The Standards Review found two in-scope fail-closed issues before evidence was
frozen: promotion could re-read a newer report after eligibility, and an
incomplete Lane Binding object could reach promotion despite complete-looking
maps. Both received public-interface RED tests and were fixed before final
verification. No raw secret, Credential Reference private locator, current
profile lookup, historical JSON rewrite, or synthetic legacy profile identity
remains in the report/baseline path.

The Spec Review confirmed report v3 provenance, selected-Attempt consistency,
all-PASS plus complete-provenance promotion, exact baseline identity,
profile-head/archive independence, report v1/v2 and historical baseline
readability, structured legacy rejection, Console provenance, and immutable
source/replay links. No blocking finding remained before commit.
