# TP-EP09 Console Migration and Legacy Compatibility Evidence - 2026-07-17

## Status

| Field | Value |
|---|---|
| Task | `TP-EP09` |
| Status | Complete |
| Requirements | `EP-FR-001`, `EP-FR-002`, `EP-FR-003`, `EP-FR-004`, `EP-FR-006`, `EP-FR-009`, `EP-FR-010`, `EP-FR-015` |
| Base revision | `5839a7bc82acc9a0c53547539e7ae3fa2ce44fab` (`TP-EP08: add report and baseline provenance`) |
| Source revision | implementation commit containing this evidence |
| Branch | `main` |
| Recorded at | `2026-07-17` |
| Live model required | No |
| Next task | `TP-EP10` not started; separate authorization required |

## Delivered vertical slice

Workflow authoring now emits Workflow v2 only. It owns tasks, Lane Slots,
comparison/evaluation policy, and orchestration, but no longer loads Targets or
renders Target, Agent, model endpoint/name, Image Input Format, seed, or raw
API-key launch controls. Published workflows route every operator to the
dedicated Run Launch feature.

The Runs page also removes its loose inline create-run form and exposes Run
Launch as the normal creation path while retaining the separate legacy artifact
import action. Run Launch lists only published Workflow v2 heads, healthy exact
Target Revisions, and published exact Execution Profile Revisions. Preview and
create continue to send those exact immutable IDs and create profile-aware Run
Plan v2 identity.

Browser launch persistence is now limited to
`test-platform.run-launch.recent-profile-revision-id`. The stored identity is
validated against the current Project's published profile heads before use.
Target, Agent, model, endpoint, Image Input Format, Credential Reference
payloads, and raw secret values are not stored as launch preferences.

On first Console load, deprecated `test-platform.launch.*` keys are scrubbed.
Only a complete non-secret `generic_v2` endpoint/model/image tuple is retained
in memory for that page session. The operator must open Execution Profiles and
choose **Review saved launch preferences** before a prefilled profile draft
appears. Saving creates a normal unpublished draft with no credential slots;
publication and Credential Reference binding remain separate explicit actions.
Unknown deprecated keys, including a raw API-key-shaped key, are deleted and
never enter the request payload.

The model Compatibility Check remains available in the typed `generic_v2`
Execution Profile draft rather than in a loose launch form. Its optional API
key is transient, excluded from profile draft requests and browser storage, and
protected from late-response races.

## Legacy compatibility boundary

The existing `POST /api/platform/v1/runs` HTTP contract remains available. A
public-interface characterization creates a Workflow v1 Run through this
adapter, reads explicit Legacy Execution Identity, previews a retry, performs
the retry, and reads the same Legacy identity afterward. No Execution Profile
Revision is fabricated or persisted.

Existing coverage continues to protect imported Runs, report v1/v2,
historical Baselines, replay links, incident links, and Run Observatory views:
missing profile provenance remains visibly Legacy rather than being inferred
from current profile state. The supported legacy surface, security boundaries,
rollback rule, and criteria-based exit are documented in
[`../EXECUTION_PROFILES_COMPATIBILITY.md`](../EXECUTION_PROFILES_COMPATIBILITY.md).
Removing new Legacy creation requires a separately authorized change even after
TP-EP10.

## TDD record

| Slice | Red | Green |
|---|---|---|
| Workflow ownership | Workflow authoring still rendered Target and loose Agent/model/key/image launch state and emitted Workflow v1 for Single Runs | Workflow authoring emits Workflow v2 Lane Slots only and routes publication to Run Launch |
| Normal Runs path | Runs still rendered the legacy create-run form and persisted loose subject preferences | Runs exposes only the exact-revision Run Launch CTA plus the independent legacy import action |
| Recent identity | Selecting a profile revision left no recoverable launch identity | Run Launch stores and restores only a validated recent Execution Profile Revision ID |
| Reviewed conversion | Deprecated launch keys remained in browser storage and no explicit draft conversion existed | Shell scrubs the whole deprecated namespace and exposes an in-memory, explicit reviewed draft action with no auto-publish |
| Secret boundary | A deprecated raw API-key-shaped key could remain alongside old preferences | The prefix scrub deletes it before rendering; neither storage nor profile-create requests contain the sentinel |
| Compatibility placement | Removing loose launch controls broke the existing Compatibility Check interface tests | The typed profile draft owns the transient check and preserves compatible, auth-failure, fixed-Agent, and stale-response behavior |
| Legacy adapter | Legacy create/read and follow-up guarantees were distributed across older fixtures | One public HTTP characterization covers create, read, retry preview, retry, and retained Legacy identity |

The implementation passed each focused test after its corresponding failing
contract was observed. The initial full Console reconciliation exposed seven
tests tied to the removed loose launch UI; they were migrated to Run Launch or
typed profile-draft contracts before the full suite was rerun.

## Verification

### Canonical test inventory

The inventory algorithm is explicit and identical to TP-EP08:

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
| Fixed base (`5839a7b`) | 516 | `1e4ba9f19023849efda696267268063d0c11d7b3becf411889d465b50324249a` | 73 | `2f9bcba0a3d8946ccd7ddd1efb8f72e93d6921b7af7862fc52779453709613cb` |
| Candidate | 517 | `4b75fcd4fd6801ce9965cbe0687100323f3093323b8fcfc74643564defd9092f` | 75 | `85de079b2d782aa7b395c055b02c94a0adaff4953e541660bb4ba6e1f852046f` |

The candidate adds one backend public HTTP compatibility characterization and
two Console contract cases.

| Phase | Result |
|---|---|
| Delivery-plan focused backend | 10 passed, 1 existing warning |
| Delivery-plan focused Console | 8 passed across 3 files; existing Node runner warning only |
| TypeScript type check | clean |
| Full Test Platform backend | 517 passed, 1 existing Starlette/httpx deprecation warning |
| Full Platform Console | 75 passed across 23 files; existing Node local-storage runner warning only |
| `git diff --check` | clean |

Manual dogfood and a live model were not required for this deterministic slice.
Verification followed contract reconciliation, TDD tracer bullets, focused and
full suites, canonical evidence capture, then read-only Standards and Spec
reviews.

## Read-only review

The Standards Review confirmed that Workflow and Runs pages no longer own loose
subject launch state; the normal path passes exact immutable IDs through the
existing Run Launch boundary; old storage is scrubbed before child routes
render; raw compatibility secrets remain transient; and the change introduces
no automatic publication, profile backfill, synthetic identity, or new Legacy
creation behavior.

The Spec Review confirmed all seven TP-EP09 acceptance criteria: Workflow v2
Lane Slots, exact-revision normal launch, identity-only browser persistence,
explicit visible preference conversion, stable Legacy HTTP create/read/follow-
up behavior, honest Legacy downstream views, and documented security/exit
contracts. TP-EP10 behavior, browser acceptance expansion, live-model dogfood,
automatic migration, and legacy-adapter removal remain out of scope.
