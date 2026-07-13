# MobileGym Test Platform

The MobileGym Test Platform context defines reusable benchmark intent, exact
execution identity, run history, evidence, reports, and strict comparison
provenance over the existing Benchmark Engine and Simulator.

## Language

### Authoring

**Project**:
A named workspace that owns workflows, targets, Execution Profiles, runs, and
baselines.
_Avoid_: Workspace, tenant

**Workflow**:
A named, editable benchmark definition whose published history is preserved as
Workflow Versions.
_Avoid_: Pipeline, DAG, test script

**Workflow Version**:
An immutable published benchmark protocol defining tasks, Lane Slots,
preparation policy, evaluation, comparison, gates, and orchestration policy.
_Avoid_: Workflow snapshot, pipeline version

**Lane Slot**:
A named role in a Workflow Version, such as baseline or candidate, that awaits
exact resource revisions at launch.
_Avoid_: Lane template, target slot

### Execution identity

**Target**:
A named, mutable identity for an executable simulator or device environment.
_Avoid_: Environment profile, device profile

**Target Revision**:
An immutable observation of a Target's App, device, data, and environment
identity at a point in time.
_Avoid_: Target version, live target

**Execution Profile**:
A named, project-scoped identity for the Agent and model configuration being
measured as the subject of a run.
_Avoid_: Profile, Model Profile, Agent configuration

**Execution Profile Revision**:
An immutable published snapshot of an Execution Profile's public behavior and
credential-reference identity.
_Avoid_: Profile version, latest profile, runtime config

**Image Input Format**:
The representation used to send simulator screenshots to the subject model.
_Avoid_: Image, image mode, container image

**Credential Reference**:
A non-secret identity that names credentials required by a published execution
contract without containing the credential value.
_Avoid_: API key, secret value, credential

**Lane Binding**:
The exact pair of one Target Revision and one Execution Profile Revision bound
to a Lane Slot for a run.
_Avoid_: Lane config, latest binding

**Target Comparison**:
A paired comparison in which Target Revision is the only varying identity and
Execution Profile Revision is held equal.
_Avoid_: Environment comparison, mixed comparison

**Execution Comparison**:
A paired comparison in which Execution Profile Revision is the only varying
identity and Target Revision is held equal.
_Avoid_: Model-only comparison, Agent-only comparison, mixed comparison

**Legacy Execution Identity**:
Historical inline execution provenance that has no genuine Execution Profile
Revision and must not be presented as one.
_Avoid_: Synthetic profile, migrated profile

### Runs and evidence

**Prepared Episode**:
A run-scoped, immutable task instance and prepared initial state shared by every
Lane Binding that evaluates it.
_Avoid_: Lane episode, attempt fixture

**Run Plan**:
The immutable execution contract that freezes a Workflow Version, task source,
Lane Bindings, Prepared Episode identities, and comparison policy before work
begins.
_Avoid_: Live plan, workflow instance

**Run**:
The logical history of executing one Run Plan, including all of its attempts.
_Avoid_: Job attempt, execution attempt

**Run Attempt**:
One initial, retry, or resume attempt to execute work from a frozen Run Plan.
_Avoid_: Run, rerun

**Compatibility Preflight**:
Time-bound, redacted evidence that a Run Attempt's effective subject model can
accept its required screenshot request.
_Avoid_: Profile validation, compatibility guarantee

**Strict Baseline**:
A named, immutable reference to one fully successful Lane Binding with complete
provenance.
_Avoid_: Golden run, mutable baseline
