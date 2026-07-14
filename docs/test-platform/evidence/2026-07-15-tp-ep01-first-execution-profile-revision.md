# TP-EP01 First Execution Profile Revision Evidence - 2026-07-15

## Status

| Field | Value |
|---|---|
| Task | `TP-EP01` |
| Status | Complete |
| Requirements | `EP-FR-001`, `EP-FR-002` first-revision slice |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-15` |
| Live model required | No |
| Next task | `TP-EP02` not started |

## Delivered vertical slice

The Test Platform now exposes a project-scoped `ExecutionProfiles` module and
matching HTTP and console adapters. An operator can create a named draft,
publish immutable revision 1, discover the profile, reload the revision, and
inspect its normalized public contract and exact identity.

The module interface exercised by tests is product-oriented:

- `save_draft` and `update_draft` validate typed public input;
- `publish` creates revision 1 and advances the profile head;
- `list` and `get` discover normalized project-visible profiles; and
- `get_revision` returns the immutable published public contract.

Temporary real SQLite databases back the direct module test. The
`ProjectRepository` is used only to establish the Project fixture; assertions
observe behavior through `ExecutionProfiles`, not repository helpers.

## Typed public contract and identity

Revision 1 records these typed namespaces: Agent, model protocol/endpoint/name,
Image Input Format, generation parameters, streaming behavior, inference
timeout, and an empty credential-slot requirement. The TP-EP01 subject supports
`generic_v2`, `openai_chat_completions`, and `data_url | bare_base64`.

All spec models reject unknown fields and coercive values. Names and model
strings are normalized before persistence. The known canonical public identity
for the demonstrated contract is:

```text
sha256:3d71ef81a34bc6d78054f83d6c094360be1893f79b2d1ffac1bc79a90d29c37d
```

Publishing performs static validation only. An exploding compatibility probe
records zero calls. A second publication returns
`EXECUTION_PROFILE_ALREADY_PUBLISHED` / HTTP 409 so TP-EP01 does not implement
TP-EP04 multiple-revision, unchanged-publication idempotency, uniqueness,
concurrency, diff, clone, or archive behavior early.

## Security and isolation evidence

- Secret-like field names are rejected without echoing their values.
- Model URLs containing user-info credentials or any query component are
  rejected without echoing sensitive URL content. The conservative TP-EP01
  query rule prevents signed and otherwise unknown credential parameters from
  entering persistence.
- Credential slots remain empty until credential-reference binding support is
  delivered; no raw secret value field is persisted or returned.
- List, profile detail, and revision reads return normalized public settings.
- Another Project receives an empty collection and not-found responses when it
  attempts to discover or publish the owning Project's profile.
- Revision reads use the same revision-not-found response for a cross-Project
  identifier and an identifier that does not exist, preventing ownership
  enumeration through public error codes.

## Console evidence

`/test-platform/execution-profiles` is reachable from the Test Platform
navigation. Its typed form creates the no-secret draft, publishes revision 1,
and displays the exact revision ID and canonical public hash. The focused test
unmounts and reloads the console, then observes the same durable identity.

## TDD record

| Slice | Red | Green |
|---|---|---|
| First profile revision | Public create/publish/discovery and console interfaces did not exist | One SQLite/HTTP/console vertical slice creates, publishes, reloads, and displays revision 1 |
| Normalized profile name | A whitespace-only name returned HTTP 201 | Create and update share `EXECUTION_PROFILE_NAME_REQUIRED` validation after normalization |
| Strict typed settings | `generation.stream: 1` was silently coerced to `true` | Strict Pydantic models return `EXECUTION_PROFILE_SPEC_INVALID` for coercive input |
| URL secret safety | Unknown signed/query URL forms were persisted and echoed | User info and every query component are rejected before typed normalization or persistence |
| Revision isolation | Cross-Project and missing revision IDs returned different public error codes | Both return the same `EXECUTION_PROFILE_REVISION_NOT_FOUND` shape |
| Slice boundary | A second publish created revision 2 | A second publish returns `EXECUTION_PROFILE_ALREADY_PUBLISHED` / 409, leaving TP-EP04 unstarted |
| Deep module seam | Coverage existed only through HTTP | A temporary-SQLite test now exercises `save_draft`, `publish`, `list`, and `get_revision` directly |

## Verification

| Phase | Result |
|---|---|
| Focused backend (`ExecutionProfiles` module, API, migrations) | 27 passed, 1 existing Starlette/httpx2 deprecation warning |
| Focused console | 1 passed |
| Full frontend | 66 passed across 21 files |
| Full backend, including deterministic browser smoke | 454 passed, 1 existing warning |
| TypeScript type check | clean |
| `git diff --check` | clean |

The first concurrent full-suite attempt produced one timeout in the existing
paired browser smoke while the frontend suite ran in parallel. The page already
contained the report and comparison groups but its asynchronous attempt state
had not settled within the assertion timeout. The smoke passed alone, and the
entire backend suite then passed when rerun serially.

Manual dogfood and a live model were not required. The complete TP-EP01 behavior
is observable through deterministic module, SQLite, HTTP, console, reload, and
browser-tested platform seams. TP-EP02 has not started.
