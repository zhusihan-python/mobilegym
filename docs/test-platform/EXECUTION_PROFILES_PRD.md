# Versioned Execution Profiles Product Requirements Document

## Document status

| Field | Value |
|---|---|
| Status | Accepted; delivery complete through TP-EP01 on 2026-07-15 |
| Product phase | Post-hardening product expansion |
| Backlog source | [`PRODUCT_BACKLOG.md#tp-future-01-versioned-execution-profiles-and-execution-aware-lanes`](PRODUCT_BACKLOG.md#tp-future-01-versioned-execution-profiles-and-execution-aware-lanes) |
| Parent requirements | [`PRD.md`](PRD.md) |
| Architecture | [`EXECUTION_PROFILES_ARCHITECTURE.md`](EXECUTION_PROFILES_ARCHITECTURE.md) |
| Approved delivery plan | [`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md) |
| Domain language | [`CONTEXT.md`](CONTEXT.md) |
| Implementation authorization | TP-EP00 and TP-EP01 complete; TP-EP02 and later slices require separate authorization |
| Current evidence | [`evidence/2026-07-15-tp-ep01-first-execution-profile-revision.md`](evidence/2026-07-15-tp-ep01-first-execution-profile-revision.md) |

## 1. Summary

The Test Platform will make Agent and model execution configuration a reusable,
versioned product object. An **Execution Profile** is a named project identity;
an **Execution Profile Revision** is an immutable published snapshot of the
subject configuration used by a run.

Every profile-aware Lane binds exactly:

- one exact Target Revision; and
- one exact Execution Profile Revision.

This lets the existing paired-run foundation compare Agents or models on the
same Target Revision and the same Prepared Episodes without conflating subject,
target, evaluation, or scheduling changes.

## 2. Decision context

The hardened platform already freezes a Workflow Version, Target Revisions,
task-source identity, episode identities, and effective runner configuration in
an immutable Run Plan. However, Agent and model settings are currently entered
as launch-time execution overrides, merged into workflow execution settings,
and exposed as a flat runner configuration. They are not reusable named
identities and cannot be selected independently per Lane through the product.

The accepted direction is to preserve the Run Plan as the authoritative frozen
contract while giving the subject-under-test configuration its own immutable
revision identity.

## 3. Problem statement

The current launch model has five product limitations:

1. Agent and model settings must be re-entered or recovered from browser-local
   preferences rather than selected as a reviewed project object.
2. A Lane has precise Target Revision provenance but no equally precise subject
   configuration revision.
3. Model A/B and Agent A/B runs cannot declare a causal comparison axis while
   holding Target Revision, prepared state, evaluation, and orchestration equal.
4. Reports and Strict Baselines cannot identify the Execution Profile Revision
   that produced a selected Lane.
5. Retry, Resume, clone, and relaunch semantics are ambiguous when launch-time
   subject settings change.

## 4. Product goals

### 4.1 Goals

- Make Execution Profiles project-scoped, named, discoverable, cloneable, and
  archivable.
- Publish immutable Execution Profile Revisions with deterministic public
  content identity.
- Bind every profile-aware Lane to exact Target and Execution Profile revisions.
- Support causal Target Comparison and Execution Comparison with explicit
  equality invariants.
- Preserve one shared Prepared Episode identity across every paired Lane.
- Keep secret values outside immutable revisions, Run Plans, artifacts, reports,
  logs, URLs, events, and idempotency payloads.
- Treat Compatibility Preflight as Run Attempt evidence, not profile identity.
- Preserve existing Workflow Versions, Run Plans, reports, baselines, and legacy
  inline execution provenance without inventing false identities.
- Give operators exact revision and field-diff visibility before launch.

### 4.2 Non-goals

- A general provider plugin marketplace or arbitrary provider-defined workflow
  stages.
- Arbitrary-DAG workflows, N-dimensional sweeps, or more than one causal change
  axis in a paired comparison.
- Versioning Target configuration inside Execution Profiles.
- Storing Judge identity, task protocol, scheduling capacity, or secret values
  inside Execution Profiles.
- Making remote Compatibility Preflight success a permanent property of a
  revision.
- Rewriting historical Run Plans or manufacturing Execution Profile Revisions
  for historical inline configuration.
- Implementing this PRD before a delivery plan is separately reviewed and
  approved.

## 5. Product ownership of settings

| Product object | Owns | Must not own |
|---|---|---|
| Target Revision | App, device, data, environment connection, and target-runtime identity | Agent/model behavior, generation, Judge, task protocol |
| Execution Profile Revision | Agent identity, model protocol/endpoint/name, Image Input Format, generation parameters, streaming behavior, inference timeout, and Credential Reference identity | Target connection, tasks, max steps, Judge, evaluation protocol, parallelism, process count, gates, secret values |
| Workflow Version | Tasks, order, repeat, max steps, preparation policy, Judge and evaluation protocol, Lane Slots, comparison/gates, and orchestration policy | Exact launch resource revisions and secret values |
| Run Plan | Exact Lane Bindings, effective public snapshots, task source, Prepared Episode identities, and comparison policy | Secret values and time-varying preflight results |
| Run Attempt | Attempt selection, redacted Compatibility Preflight, secret-resolution status, execution evidence, and outcome | Mutable revision selection |

Judge configuration remains part of the Workflow Version. The subject being
measured must be independently replaceable without silently replacing the
evaluation mechanism used to score it.

## 6. Domain behavior

### 6.1 Execution Profile lifecycle

- An Execution Profile belongs to one Project and has a project-visible name.
- Active names are unique under the same normalization policy used for other
  named Test Platform resources.
- The editable draft is not an Execution Profile Revision and cannot be bound to
  a Run.
- Publishing a valid draft creates an immutable, monotonically numbered
  Execution Profile Revision.
- Publishing unchanged canonical content is idempotent and returns the existing
  head revision instead of creating revision churn.
- A published revision cannot be edited, deleted, or repointed to different
  credential-reference identity.
- Archiving the Execution Profile hides it from default discovery and blocks it
  from new initial Runs. Historical revisions remain readable and remain valid
  inputs to historical Run, Report, Baseline, Retry, and Resume views.
- Cloning a revision creates a new editable draft. It never copies a secret
  value.

### 6.2 Lane Slots and Lane Bindings

- A Workflow Version defines Lane Slots and roles, not mutable resource heads.
- Launch binds every Lane Slot to one exact Target Revision and one exact
  Execution Profile Revision.
- The launch request and confirmation view must use concrete revision IDs.
  `latest`, `current`, or an unversioned profile identity is never a frozen
  binding.
- Target and Execution Profile revisions must belong to the same Project as the
  Workflow Version.
- A revision that becomes stale between preview and create causes a structured
  conflict; the platform never silently substitutes a newer revision.

### 6.3 Comparison modes

The first profile-aware release supports three modes:

| Mode | Lane count | Required equality | Allowed variation |
|---|---:|---|---|
| Single | 1 | Not applicable | No comparison |
| Target Comparison | 2 | Execution Profile Revision | Target Revision, subject to declared target constraints |
| Execution Comparison | 2 | Target Revision | Execution Profile Revision |

The first release rejects a paired Run when both Target Revision and Execution
Profile Revision vary. Such a Run is confounded and cannot produce a causal
regression classification or Strict Baseline comparison.

Two identical Lane Bindings are also rejected as a comparison because they have
no declared variation.

### 6.4 Same-prepared-state guarantee

- Prepared Episodes are Run-scoped and independent of Execution Profile
  Revision.
- Every paired Lane evaluates the same task source, task identity, seed,
  sampled parameters, instruction, initial-state projection, time, and location.
- Agent/model selection, model preflight, or credential resolution cannot cause
  task re-sampling or state re-preparation.
- Execution Comparison additionally requires the same exact Target Revision on
  both Lanes.
- Pair-integrity evidence remains authoritative when the live simulator cannot
  reproduce the prepared projection.

### 6.5 Secrets and Compatibility Preflight

- An Execution Profile Revision records required credential slots and immutable,
  non-secret Credential Reference identity.
- Raw secret values are resolved only for a launch or follow-up attempt and are
  never returned by profile read interfaces.
- A Credential Reference identity change creates a new Execution Profile
  Revision. Rotating the value behind the same reference does not.
- Public errors expose only Project, profile, revision, provider, slot, and
  stable compatibility codes. They never expose a secret value or sensitive
  backend path.
- Publishing performs static validation only. It does not contact the model
  provider.
- Compatibility Preflight runs against each distinct effective subject
  configuration after the Run Plan is compiled and before durable Run or Run
  Attempt side effects.
- Compatibility Preflight outcome, latency, cache use, checked model, checked
  Image Input Format, and affected Lane keys are redacted Run Attempt evidence.
- Preflight evidence does not enter Execution Profile Revision identity or Run
  Plan fingerprints.

### 6.6 Retry, Resume, clone, and relaunch

- Retry and Resume load the original frozen Run Plan and accept no Target,
  Execution Profile, Agent, model, generation, Judge, or scheduling override.
- A newer or archived Execution Profile head does not change an existing Run and
  does not by itself block Retry or Resume.
- Follow-up attempts may resolve the same declared Credential References again.
  Missing credentials fail with a structured error before a new attempt exists.
- Both Retry and Resume verify that the frozen Target Revision, task source, and
  subject implementation remain executable. They never upgrade to a newer
  revision.
- An action that selects a different Target Revision or Execution Profile
  Revision creates a new initial Run with a new fingerprint. It is not Retry or
  Resume.
- Any future Run clone action defaults to the original exact Lane Bindings. An
  explicit revision change is presented as a new-run fork with a revision diff.

### 6.7 Fingerprints and provenance

- An Execution Profile Revision has a canonical public-spec hash plus immutable
  private Credential Reference binding identity.
- Each profile-aware Lane records Target Revision ID/hash, Execution Profile
  Revision ID/hash, and a Lane fingerprint over its effective public contract.
- The Run Plan fingerprint includes Workflow Version, task source, Lane Slots,
  exact Lane Bindings, Prepared Episode identities, comparison policy, and
  effective public configuration.
- Fingerprints exclude secret values, remote preflight results, cache/latency
  metadata, timestamps, and queue state.
- Reports expose Target and Execution Profile revision mappings per Lane.
- A new profile-aware Strict Baseline stores the selected Lane's Target Revision,
  Execution Profile Revision, Lane fingerprint, Workflow Version, Run Plan hash,
  task-source digest, completed Run Attempt, and required preflight evidence.

### 6.8 Legacy compatibility

- Existing Workflow Version schema v1, Run Plan schema v1, report schemas, and
  Strict Baselines remain readable and exportable.
- Existing Runs continue to use their frozen inline runner configuration for
  Retry and Resume.
- Historical inline configuration is shown as Legacy Execution Identity. The
  platform never creates or displays a synthetic Execution Profile Revision for
  it.
- The existing create-run interface remains available through a legacy adapter
  during an explicit compatibility window.
- The profile-aware launch interface creates only the new Run Plan schema.
- Existing Strict Baselines retain their historical status and names. Missing
  execution provenance prevents them from claiming profile-aware strict
  comparability or being newly promoted under the new provenance rules.
- Migration is additive. Existing Run Plan or report JSON is not rewritten in
  place.

### 6.9 User experience

The console adds:

- an Execution Profiles page with active/archived discovery, draft editing,
  validation, publication history, clone, and archive;
- a dedicated Run Launch flow separated from Workflow authoring;
- exact Target and Execution Profile revision selectors per Lane Slot;
- a default Single flow and explicit Target Comparison / Execution Comparison
  flows;
- a public field diff for paired Execution Profile Revisions;
- credential readiness without secret disclosure;
- a launch preview showing exact revisions, fingerprints, comparison mode,
  prepared-state policy, and structured violations;
- Run, Observatory, Report, Baseline, replay, and incident views that show exact
  Execution Profile Revision identity alongside Target Revision identity.

Browser persistence may remember a recently selected Execution Profile identity
for convenience. It must not persist raw secret values or reconstruct launch
identity from loose Agent/model fields.

## 7. Functional requirements

| ID | Requirement |
|---|---|
| EP-FR-001 | The platform shall create, edit, discover, publish, clone, and archive project-scoped Execution Profiles. |
| EP-FR-002 | The platform shall bind Runs only to immutable Execution Profile Revisions, never drafts or `latest`. |
| EP-FR-003 | Every profile-aware Lane shall bind exactly one Target Revision and one Execution Profile Revision. |
| EP-FR-004 | Workflow Versions shall define Lane Slots while launch supplies exact Lane Bindings. |
| EP-FR-005 | The platform shall support Single, Target Comparison, and Execution Comparison modes. |
| EP-FR-006 | The platform shall reject paired Runs in which both Target and Execution Profile revisions vary. |
| EP-FR-007 | Execution Comparison shall share exact Target Revision and Prepared Episode identity across Lanes. |
| EP-FR-008 | Target Comparison shall share exact Execution Profile Revision across Lanes. |
| EP-FR-009 | Judge and evaluation protocol shall remain frozen by Workflow Version and equal across paired Lanes. |
| EP-FR-010 | Secret values shall never enter durable or public product identity. |
| EP-FR-011 | Compatibility Preflight shall be recorded per Run Attempt and excluded from revision and plan fingerprints. |
| EP-FR-012 | Retry and Resume shall preserve original Lane Bindings and reject non-secret configuration overrides. |
| EP-FR-013 | Reports and new Strict Baselines shall include per-Lane Execution Profile Revision provenance. |
| EP-FR-014 | Legacy Runs, reports, and baselines shall remain readable without synthetic profile identity. |
| EP-FR-015 | Profile-aware launch shall fail without durable side effects when revision, secret, comparison, or preflight validation fails. |

## 8. Acceptance criteria

- [ ] A user can publish two immutable Execution Profile Revisions and inspect a
      redacted field diff.
- [ ] A Single Run freezes one exact Target Revision and one exact Execution
      Profile Revision in its Run Plan and public provenance.
- [ ] An Execution Comparison runs two profiles against one exact Target
      Revision and one shared set of Prepared Episodes.
- [ ] A Target Comparison runs one exact profile revision against two permitted
      Target Revisions.
- [ ] A comparison that varies both axes is rejected before Run creation with
      structured violations.
- [ ] Publishing, reading, launching, reporting, copying incident links, and
      exporting never expose raw secret values or sensitive secret paths.
- [ ] Compatible, incompatible, missing-secret, stale-preview, archived-profile,
      and cross-project cases have deterministic state and error outcomes.
- [ ] Retry and Resume reuse exact original Lane Bindings after a newer profile
      revision is published.
- [ ] Profile-aware reports and Strict Baselines show complete selected-Lane
      profile provenance.
- [ ] Existing Workflow v1, Run Plan v1, report, baseline, and Retry/Resume
      behavior remains readable and mechanically verified.
- [ ] Deterministic browser smoke covers profile creation/publication, Single
      launch, Execution Comparison, revision display, reload, and immutable
      historical links without a live commercial or local model.

## 9. Success measures

- Operators can launch a previously reviewed subject configuration without
  re-entering public Agent/model settings.
- Every new profile-aware report identifies the exact subject revision for each
  Lane.
- An Execution Comparison has zero Target Revision or Prepared Episode drift.
- No historical identity is silently upgraded or fabricated.
- Provider or secret failures occur before durable execution side effects and
  remain diagnosable through stable redacted evidence.

## 10. Delivery gate

This PRD authorizes delivery planning only. The proposed slices are recorded in
[`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md).
Before implementation begins, that plan must be reviewed and must define
independently verifiable vertical slices, migration ordering, compatibility
gates, deterministic adapters, rollback expectations, and final acceptance
evidence.
