# TP-EP00 Legacy Execution Identity Evidence - 2026-07-13

## Status

| Field | Value |
|---|---|
| Task | `TP-EP00` |
| Status | Complete |
| Requirement | `EP-FR-014` |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-13` |
| Live model required | No |

## Versioned compatibility contract

Persisted Workflow and Run Plan documents now enter the domain through the
version-aware readers in `test_platform/domain/versioned_documents.py`.
Workflow v1 and Run Plan v1 receive typed parsing; every persisted Run Plan
consumer, including Run detail/list, execution, follow-up, report input, and
frozen gate reads, uses that interface. Reading does not rewrite stored JSON.

Unknown persisted versions return stable conflicts before partial
interpretation:

- `RUN_PLAN_SCHEMA_UNSUPPORTED`, HTTP 409;
- `WORKFLOW_SCHEMA_UNSUPPORTED`, HTTP 409; and
- error details include the received `schema_version` and
  `supported_schema_versions: [1]`.

The versioned v1 Run Plan model also represents the platform's existing
imported-run shape, where an unavailable repository revision is `null`.

## Honest legacy identity

Every Run Detail v1 response exposes this explicit DTO:

```json
{
  "kind": "legacy",
  "label": "Legacy Execution Identity",
  "schema_version": 1
}
```

The console consumes that DTO directly. Run overview facts, the Run Observatory
header, and the frozen Settings drawer display the marker without inferring an
Execution Profile from inline runner configuration. Imported CLI Runs expose
the same marker and retain their original import provenance.

No Execution Profile table, migration, row, or synthetic Execution Profile
Revision was added.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Run Detail identity | Public GET raised `KeyError: execution_identity` in the new assertion | v1 GET returned the exact legacy DTO through the versioned reader |
| Unknown Run Plan | Public GET surfaced an unhandled Pydantic literal error for schema 99 | GET returned `RUN_PLAN_SCHEMA_UNSUPPORTED` / 409 and the stored JSON remained byte-for-byte unchanged |
| Unknown Workflow | create-run continued past schema 99 and persisted drafts could leak through list responses | create-run, Workflow Version GET, and Workflow list returned `WORKFLOW_SCHEMA_UNSUPPORTED` / 409 with zero Run side effects or data rewrite |
| Console identity | Run Detail and Observatory tests could not find the marker | Overview, Observatory, and Settings render the server DTO explicitly |
| Imported v1 parity | Typed parsing exposed the existing nullable repository revision and historical imported Workflow shape | Imported Run, Workflow Version/list, report, and baseline compatibility passed without data rewrite |

All tests exercise public Run, Workflow, Retry/Resume, Report/Baseline, import,
and console interfaces. No private version-switch helper test was added.

## Compatibility evidence

- Existing Retry and Resume tests remain green, including frozen runner config,
  preview-token selection, and model API key reinjection without persistence.
- Existing report v1/v2 read/export and Strict Baseline eligibility, promotion,
  discovery, archive, and immutable report-link tests remain green.
- Existing imported Run source links, report provenance, and strict-provenance
  rejection remain green. Its historical Workflow Version and project Workflow
  list responses also remain readable without adding fields to stored or
  returned definitions.
- The deterministic paired browser smoke passes with the Legacy Execution
  Identity marker present through reload, report, replay, and follow-up preview.

## Verification

| Phase | Result |
|---|---|
| Focused backend compatibility (Run, Workflow, Retry/Resume, Report/Baseline, import, SSE) | 57 passed, 1 existing Starlette/httpx2 deprecation warning |
| Focused frontend (`testPlatformImportedRuns` + `testPlatformRunObservatory`) | 12 passed across 2 files |
| Deterministic paired browser smoke | 1 passed |
| Full frontend | 65 passed across 20 files |
| Full backend, including deterministic browser smoke | 435 passed, 1 existing warning |
| TypeScript type check | clean |
| `git diff --check` | clean |

Manual dogfood and a live model were not required. The behavior is fully
observable through deterministic HTTP, SQLite, console, reload, report,
baseline, Retry/Resume, and real-browser seams, and TP-EP00 introduces no new
profile-aware launch behavior to exercise manually.
