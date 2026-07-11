# Test Platform Product Backlog

## Document status

| Field | Value |
|---|---|
| Status | Active backlog |
| Scope | Product directions that are recorded but not yet specified |
| Current delivery focus | [`HARDENING_PRD.md`](HARDENING_PRD.md) |

Backlog entries describe opportunities, not accepted implementation contracts.
They must receive their own product and architecture review before entering a
delivery plan.

## TP-FUTURE-01: Versioned Execution Profiles and execution-aware lanes

| Field | Value |
|---|---|
| Status | Deferred TODO |
| Priority | Discuss after the current hardening and completion plan |
| Requested direction | Make Execution Profiles immutable and versioned; let each lane reference both a target revision and an execution-profile revision |
| Product opportunity | Compare models or Agents on identical tasks, seeds, prepared state, and targets using the existing paired-comparison foundation |
| Not authorized yet | Schema, migration, secret, UI, comparison-policy, retry, or rollout implementation |

### Opportunity

The Test Platform currently treats target and App comparison as the primary lane
dimension while launch-time Agent and model settings are applied as execution
configuration. A future product direction could make an Execution Profile a
first-class immutable revision and define a lane as the combination of:

- a frozen target revision; and
- a frozen execution-profile revision.

That would allow the existing prepared-episode and pairing guarantees to support
model A/B or Agent A/B evaluation on the same simulator state. This direction is
more aligned with MobileGym's agent-benchmarking purpose than expanding toward a
general arbitrary-DAG workflow system.

### Discussion gate

Do not begin detailed design until the current items in
[`HARDENING_PLAN.md`](HARDENING_PLAN.md) are complete and their acceptance
evidence is recorded.

The later discussion must resolve at least:

- profile identity, revision, publication, and archival semantics;
- which Agent, model, image, generation, judge, and timeout settings belong in a
  profile;
- secret references and redaction without storing secret values in immutable
  revisions;
- whether a lane references exactly one target revision and one profile revision;
- allowed comparison dimensions and invariants when target and execution differ;
- same-prepared-state guarantees for model or Agent comparisons;
- run fingerprint and provenance changes;
- clone, retry, and resume behavior when a profile changes;
- migration and compatibility for existing workflows and run plans;
- authoring, launch, report, baseline, and comparison UI;
- how the current model-compatibility preflight result relates to a future
  profile revision without making preflight itself the profile contract.

### Explicit non-decision

This backlog entry does not select a storage schema, interface, adapter, workflow
shape, or rollout plan. Those decisions belong in the future PRD and architecture
review.
