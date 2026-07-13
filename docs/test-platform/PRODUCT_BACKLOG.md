# Test Platform Product Backlog

## Document status

| Field | Value |
|---|---|
| Status | Active planning register |
| Scope | Product directions from backlog discovery through delivery authorization |
| Current delivery focus | TP-FUTURE-01 delivery plan approved; awaiting an explicit TP-EP00 start request |

Backlog entries begin as opportunities, not accepted implementation contracts.
They must receive their own product and architecture review before entering a
delivery plan, and a reviewed delivery plan before implementation.

## TP-FUTURE-01: Versioned Execution Profiles and execution-aware lanes

| Field | Value |
|---|---|
| Status | Product, architecture, and delivery plan accepted |
| Priority | Ready for TP-EP00 when implementation is explicitly started |
| Requested direction | Make Execution Profiles immutable and versioned; let each lane reference both a target revision and an execution-profile revision |
| Product opportunity | Compare models or Agents on identical tasks, seeds, prepared state, and targets using the existing paired-comparison foundation |
| Product requirements | [`EXECUTION_PROFILES_PRD.md`](EXECUTION_PROFILES_PRD.md) |
| Accepted architecture | [`EXECUTION_PROFILES_ARCHITECTURE.md`](EXECUTION_PROFILES_ARCHITECTURE.md) |
| Approved delivery plan | [`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md) |
| Domain language | [`CONTEXT.md`](CONTEXT.md) |
| Not authorized yet | Issue publication or implementation before an explicit TP-EP00 start request |

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

### Remaining delivery gate

The review selects the product contract, domain language, two-module seam,
module interfaces, Workflow v2 Lane Slots, Run Plan v2 identity, causal
comparison modes, and compatibility principles. It does not authorize or fully
specify SQL migrations, HTTP payloads, file layout, a production secret adapter,
vertical delivery slices, rollout, or compatibility-window removal.

The approved slices and dependencies are recorded in
[`EXECUTION_PROFILES_DELIVERY_PLAN.md`](EXECUTION_PROFILES_DELIVERY_PLAN.md).
The user accepted their granularity and dependency relationships on 2026-07-13.
No issue has been published, and implementation begins only after an explicit
request to start TP-EP00.
