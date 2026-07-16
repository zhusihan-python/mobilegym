# Test Platform Product Backlog

## Document status

| Field | Value |
|---|---|
| Status | Active planning register |
| Scope | Product directions from backlog discovery through delivery authorization |
| Current delivery focus | TP-EP06 complete; TP-EP07 awaits a separate start request |

Backlog entries begin as opportunities, not accepted implementation contracts.
They must receive their own product and architecture review before entering a
delivery plan, and a reviewed delivery plan before implementation.

## TP-FUTURE-01: Versioned Execution Profiles and execution-aware lanes

| Field | Value |
|---|---|
| Status | Delivery active; TP-EP06 complete |
| Priority | TP-EP07 requires separate authorization before implementation |
| Requested direction | Make Execution Profiles immutable and versioned; let each lane reference both a target revision and an execution-profile revision |
| Product opportunity | Compare models or Agents on identical tasks, seeds, prepared state, and targets using the existing paired-comparison foundation |
| Product requirements | [`EXECUTION_PROFILES_PRD.md`](EXECUTION_PROFILES_PRD.md) |
| Accepted architecture | [`EXECUTION_PROFILES_ARCHITECTURE.md`](EXECUTION_PROFILES_ARCHITECTURE.md) |
| Approved delivery plan | [`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md) |
| Domain language | [`CONTEXT.md`](CONTEXT.md) |
| Not authorized yet | TP-EP07 and later implementation without their required explicit start requests |

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

The approved slices and dependencies are recorded in
[`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md).
The user accepted their granularity and dependency relationships on 2026-07-13.
No issue has been published. TP-EP06 was separately authorized and completed on
2026-07-17. TP-EP07 and later slices still require their own explicit start
requests.
