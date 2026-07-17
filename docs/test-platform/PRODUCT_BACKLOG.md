# Test Platform Product Backlog

## Document status

| Field | Value |
|---|---|
| Status | Active planning register |
| Scope | Product directions from backlog discovery through delivery authorization |
| Current delivery focus | Versioned Execution Profiles release complete; future directions require separate review |

Backlog entries begin as opportunities, not accepted implementation contracts.
They must receive their own product and architecture review before entering a
delivery plan, and a reviewed delivery plan before implementation.

## TP-FUTURE-01: Versioned Execution Profiles and execution-aware lanes

| Field | Value |
|---|---|
| Status | Released; TP-EP00 through TP-EP10 complete |
| Priority | Maintain compatibility window; separately review deferred provider and comparison directions |
| Requested direction | Make Execution Profiles immutable and versioned; let each lane reference both a target revision and an execution-profile revision |
| Product opportunity | Compare models or Agents on identical tasks, seeds, prepared state, and targets using the existing paired-comparison foundation |
| Product requirements | [`EXECUTION_PROFILES_PRD.md`](EXECUTION_PROFILES_PRD.md) |
| Accepted architecture | [`EXECUTION_PROFILES_ARCHITECTURE.md`](EXECUTION_PROFILES_ARCHITECTURE.md) |
| Approved delivery plan | [`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md) |
| Domain language | [`CONTEXT.md`](CONTEXT.md) |
| Release evidence | [`evidence/2026-07-17-tp-ep10-execution-profiles-release-acceptance.md`](evidence/2026-07-17-tp-ep10-execution-profiles-release-acceptance.md) |

### Opportunity

The Test Platform currently treats target and App comparison as the primary lane
dimension while launch-time Agent and model settings are applied as execution
configuration. The accepted product direction makes an Execution Profile a
first-class immutable revision and defines a profile-aware Lane Binding as the
combination of:

- a frozen target revision; and
- a frozen execution-profile revision.

This allows the existing Prepared Episode and pairing guarantees to support
model A/B or Agent A/B evaluation on the same simulator state. It is more
aligned with MobileGym's agent-benchmarking purpose than expansion toward a
general arbitrary-DAG workflow system.

### Product and architecture decision

The hardening plan is complete and its acceptance evidence is recorded. On
2026-07-13, the user authorized product and architecture review and accepted the
recommended product and module shape. The accepted requirements and architecture
are linked above.

The review resolved the original discussion list:

- [x] profile identity, revision, publication, and archival semantics;
- [x] Agent, model, Image Input Format, generation, Judge, timeout, target, and
      orchestration ownership;
- [x] private Credential Reference binding and public redaction without durable
      secret values;
- [x] exactly one Target Revision and one Execution Profile Revision per
      profile-aware Lane Binding;
- [x] Single, Target Comparison, and Execution Comparison invariants, with mixed
      comparisons rejected;
- [x] profile-independent, Run-scoped Prepared Episodes;
- [x] Run Plan, Lane, report, and Strict Baseline fingerprint/provenance changes;
- [x] clone, Retry, Resume, relaunch, archive, and new-head behavior;
- [x] versioned readers and honest Legacy Execution Identity;
- [x] separate profile authoring and Run Launch user flows;
- [x] Compatibility Preflight as redacted Run Attempt evidence rather than a
      profile contract.

### Delivery status

The review selected the product contract, domain language, two-module seam,
module interfaces, Workflow v2 Lane Slots, Run Plan v2 identity, causal
comparison modes, and compatibility principles. TP-EP00 protected Legacy
Execution Identity, TP-EP01 delivered the first project-scoped, no-secret
Execution Profile Revision, and TP-EP02 delivered the first exact
profile-aware Single Run through Workflow v2, Run Plan v2, SQLite, HTTP, and the
console. TP-EP03 added immutable private Credential Reference bindings,
transient launch resolution, and redacted initial Run Attempt Compatibility
Preflight evidence. TP-EP04 completed active-name and concurrency rules,
immutable revision history and redacted diff, clone, archive, archived
discovery, and the complete Execution Profiles console lifecycle.
TP-EP05 moved paired Target Comparison onto Workflow v2 Lane Slots and exact
same-profile Lane Bindings while preserving constraints, shared preparation,
preflight evidence, deterministic paired results, and reload identity.
TP-EP06 added same-target Execution Comparison across two immutable Execution
Profile Revisions, a public revision diff, deterministic subject-specific
outcomes, causal rejection, and exact profile identity across Console reload,
comparison, gates, diagnostics, replay, and incident selection.
TP-EP07 made Retry and Resume expose and reuse the original frozen Lane
Bindings, enforce original credential slots and profile-aware executability
before Attempt creation, preserve new-head/archive independence, record
per-Attempt preflight, and render immutable follow-up identity in the Console.
TP-EP08 added report schema v3 with frozen per-Lane Target/Profile identity,
Lane fingerprints, and selected-Attempt preflight; new Strict Baselines now
freeze complete selected-Lane provenance while historical Baselines remain
honestly readable as Legacy strictness.
TP-EP09 removed loose execution settings from Workflow and Runs authoring,
made Run Launch the normal exact-revision Console path, limited launch browser
persistence to a recent profile revision identity, added explicit reviewed
one-time conversion of deprecated non-secret preferences into an unpublished
profile draft, and documented the Legacy HTTP compatibility window and exit
criteria.
TP-EP10 closed the release with a fresh deterministic Chromium acceptance
scenario, complete requirement/scenario-to-evidence mapping, full backend,
benchmark, Console, simulator, type-check, and lint gates, an independent real-
browser dogfood pass, secret/runtime scans, and explicit warning/debt owners.

The approved slices and dependencies are recorded in
[`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md).
The user accepted their granularity and dependency relationships on 2026-07-13.
No issue has been published. TP-EP06 through TP-EP10 were separately authorized
and completed on 2026-07-17. The Legacy create-run compatibility window remains
active under its criteria-based exit contract; provider-registry expansion and
mixed multi-axis comparison remain deferred directions rather than authorized
implementation work.
