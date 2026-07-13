# Test Platform Hardening and Completion PRD

## Document status

| Field | Value |
|---|---|
| Status | Accepted and complete 2026-07-13 |
| Product phase | Post-MVP hardening release complete |
| Parent requirements | [`PRD.md`](PRD.md) |
| Existing delivery plan | [`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md) |
| Detailed delivery plan | [`HARDENING_PLAN.md`](HARDENING_PLAN.md) |
| Deferred product direction | [`PRODUCT_BACKLOG.md`](PRODUCT_BACKLOG.md#tp-future-01-versioned-execution-profiles-and-execution-aware-lanes) |
| Delivery method | Test-driven vertical slices |

The problem statement below records the pre-hardening state that motivated this
program. TP-H00 through TP-H16 are complete; the final reproducible acceptance
record is [`evidence/2026-07-13-tp-h16-release-acceptance.md`](evidence/2026-07-13-tp-h16-release-acceptance.md).

## Priority order

This order is normative. Lower-priority work must not delay correctness or the
model-independent acceptance path.

| Priority | Outcome | Why it comes now |
|---|---|---|
| P0 | Correct episode outcomes, run completion, verdict presentation, and strict baseline eligibility | Incorrect outcomes and baselines can invalidate every later report or comparison. |
| P1 | Deterministic API and browser smoke coverage plus current full-regression evidence | The platform needs an acceptance path that is independent of external model availability. |
| P2 | Screenshot-model compatibility preflight | Operators should learn that an endpoint cannot accept the configured image payload before a run is created. |
| P3 | Reliability, infrastructure, and named-baseline report completion | These capabilities were included in the original MVP plan but remain incomplete. |
| P4 | Complete diagnostic ingestion, filtering, recovery actions, and shareable incident locations | Diagnostic identity and persistence must be trustworthy before the UI can lead to an exact recovery action. |

## Accepted product decisions

| Decision | Status | Contract |
|---|---|---|
| Strict baseline quality | Accepted 2026-07-11 | The selected lane must have a complete episode grid and every planned episode must have terminal outcome `PASS`. Overall run gate verdict remains a separate fact and cannot weaken or replace selected-lane eligibility. |

## Problem Statement

The Test Platform now supports projects, targets, versioned workflows, serial,
parallel and multi-process execution, paired comparison, Manual Sequence v1,
retry and resume, reports, diagnostics, replay, and artifact browsing. The core
feature surface is broad enough for real use, but several correctness and
acceptance gaps make the product less trustworthy than its feature list
suggests.

Operators currently face five related problems:

1. A missing worker result can be classified inconsistently across executor
   modes. An infrastructure crash can carry a crash error code while appearing
   as a functional failure in reports and retry selection.
2. The run lifecycle state `completed` is easy to read as "tests passed" even
   though it only means that execution reached a terminal workflow state. A
   Manual Sequence may complete after every episode reports `ERROR`.
3. Reports and quality gates are generated lazily. A completed run may therefore
   have no persisted verdict until a user opens its Report panel, and strict
   baseline promotion is not tied to a successful selected lane.
4. The planned end-to-end smoke path was never completed. Current platform
   acceptance depends either on injected integration fakes below the public
   interface or on an external vision model deployment.
5. Model image compatibility is discovered during the first episode instead of
   before run creation, while reliability, infrastructure reporting, named
   baseline management, and actionable diagnostics remain partial.

These gaps compound each other. A model capability failure can create a
completed run, the UI can present only the lifecycle state, a report can be
created later with inconsistent outcome facts, and the run can then be mistaken
for a suitable baseline. The platform needs a hardening phase before it grows a
new workflow or comparison model.

## Solution

Deliver a prioritized hardening and completion program that establishes the
following user-visible guarantees:

- every planned lane episode receives one canonical terminal interpretation;
- worker crashes, user cancellation, functional failure, and model or judge
  errors remain distinct through events, persistence, reports, and follow-up
  selection;
- a completed run always has a persisted report and quality-gate verdict;
- lifecycle state and quality verdict are displayed as separate facts;
- strict baselines can be created only from a complete, successful selected
  lane with reproducible provenance;
- the full control-plane path can be exercised deterministically without a real
  model endpoint;
- screenshot-requiring agents fail compatibility checks before a run is
  persisted or simulator work begins;
- repeated trials produce reliability and Pass@k results, monitor artifacts
  produce separately labelled infrastructure results, and baselines are named
  and discoverable;
- browser, page, network, episode, comparison, and gate diagnostics have stable
  persisted identity, explain the next valid action, and link to the most exact
  replay evidence available.

The program deliberately completes and deepens existing modules. It does not
introduce a general workflow canvas, state carry-forward, real-device execution,
or versioned Execution Profiles.

## User Stories

1. As a test engineer, I want a missing worker result to become an `ERROR`, so
   that infrastructure failure is never counted as a functional assertion
   failure.
2. As a test engineer, I want a user-cancelled episode to remain `CANCELLED`, so
   that cancellation is never reported as a worker crash.
3. As a test engineer, I want serial, parallel, multi-process, and paired runs to
   apply the same terminal outcome rules, so that changing concurrency does not
   change report semantics.
4. As a platform maintainer, I want one lane-outcome interface to own result
   normalization, reconciliation, persistence, terminal events, and lane
   finalization, so that a fix applies to every executor caller.
5. As a platform maintainer, I want duplicate and unknown results to fail with
   stable structured errors, so that executor corruption is never silently
   ignored.
6. As an operator, I want run lifecycle state and quality verdict shown
   separately, so that `completed` cannot be mistaken for `passed`.
7. As an operator, I want pass, fail, error, cancelled, and incomplete counts on
   the Runs page and Run Detail header, so that I can triage a run without first
   opening its full report.
8. As an operator, I want a completed run to have a persisted report and gate
   verdict immediately, so that list, detail, export, and automation consumers
   observe the same facts.
9. As an operator, I want report or gate generation failure to be explicit, so
   that a lifecycle failure is not hidden behind a successful execution phase.
10. As a release owner, I want strict baseline eligibility evaluated for the
    selected lane, so that a failing candidate lane does not invalidate a
    successful baseline lane and an unsuccessful selected lane cannot be
    promoted.
11. As a release owner, I want baseline rejection reasons returned as structured
    data, so that I know whether the problem is outcome, completeness, or
    provenance.
12. As a contributor, I want a deterministic platform smoke scenario, so that I
    can validate API, persistence, events, artifacts, replay, and reports without
    downloading or serving a model.
13. As a contributor, I want a browser smoke scenario for Manual Sequence, so
    that ordered authoring, launch, live progress, replay, and sequence reporting
    are verified through the same interface used by operators.
14. As a contributor, I want a deterministic paired-run smoke scenario, so that
    prepared episode identity, lane integrity, comparison, and gates are covered
    without an external model.
15. As a maintainer, I want the official full regression commands executed and
    recorded after the smoke path lands, so that the operational MVP has current
    acceptance evidence on the active platform.
16. As an operator, I want to test a model connection from the launch form, so
    that I can correct endpoint, model, authentication, or image-format settings
    before launching.
17. As an operator, I want screenshot compatibility tested with the exact
    message representation used by the selected agent, so that a metadata-only
    model listing cannot produce a false positive.
18. As an API client, I want stable compatibility error codes, so that automation
    can distinguish unreachable endpoint, authentication failure, missing model,
    unsupported vision input, and rejected image format.
19. As a security-conscious operator, I want compatibility probes to avoid
    persisting or logging secrets, so that preflight does not expand credential
    exposure.
20. As an operator, I want a failed required compatibility check to prevent run
    or follow-up-attempt creation, so that no new execution records or artifacts
    are created for an unusable endpoint.
21. As a benchmark engineer, I want repeated trials grouped by stable task
    instance, so that reliability and Pass@k use identical sampled parameters
    and seeds.
22. As a benchmark engineer, I want invalid or errored trials reported
    separately from valid Pass@k trials, so that infrastructure failures cannot
    inflate or silently reduce model reliability.
23. As a benchmark engineer, I want flaky task and target annotations only when
    enough valid trials exist, so that a single observation is not labelled as
    variance.
24. As an operator, I want host, process, GPU, and model-server monitor metrics
    labelled as infrastructure metrics, so that they are not confused with
    Agent or simulator execution timing.
25. As a release owner, I want to name, list, and archive strict baselines, so
    that a baseline record remains manageable after promotion.
26. As an operator, I want diagnostics to display retryability and recommended
    actions, so that the platform explains what I can do next.
27. As an operator, I want Retry and Resume controls to preview the selected
    episode count and reasons, so that I do not create an empty or surprising
    follow-up attempt.
28. As an operator, I want a diagnostic, report row, or attempt row to open the
    exact lane, episode, attempt, step, screenshot mode, and evidence tab, so
    that investigation does not require reconstructing UI state manually.
29. As a collaborator, I want to copy a stable incident URL, so that another
    person can inspect the same immutable evidence.
30. As an operator, I want browser, page, network, runner, comparison, and gate
    diagnostics persisted with stable identity and complete filters, so that
    important evidence is not lost or attached to the wrong episode.
31. As a maintainer, I want existing CLI behavior and artifact formats preserved,
    so that platform hardening does not fork the benchmark execution contract.

## Implementation Decisions

### Program and terminology

- Work is delivered in the priority order in this document. P0 and P1 form the
  minimum hardening release.
- `lifecycle state`, `episode outcome`, `quality-gate verdict`, and `baseline
  eligibility` are separate domain facts. UI copy, event payloads, DTOs, and
  documentation must not use them interchangeably.
- `completed` means the run execution and completion pipeline finished. It does
  not mean that every episode passed or that a release gate passed.
- A report with no configured quality thresholds has verdict
  `not_configured`, not `passed`. This makes the absence of an approval policy
  visible without changing the run lifecycle state.
- Existing immutable run plans, prepared tasks, target revisions, episode
  identities, and raw attempt artifacts remain unchanged.

### Canonical lane outcome commit

- Introduce a deep `LaneOutcomeCommitter` module with one external operation for
  committing an executor's observed results against the expected lane episodes.
- Its interface owns object/dictionary normalization, plan-order reconciliation,
  unknown and duplicate detection, missing-result synthesis, episode-attempt
  insertion, synthetic terminal event production, and lane finalization.
- Executors remain responsible for executing work and reporting whether the lane
  was cancelled or failed as a whole. They must not reimplement terminal episode
  interpretation.
- A missing result under operator cancellation becomes `CANCELLED` with a
  cancellation error code and a non-error result body.
- A missing result without operator cancellation becomes `ERROR` with
  `WORKER_CRASH` and an error result body.
- Unknown or duplicate results are hard structured errors. They are not dropped,
  guessed, or matched by list position.
- Serial result order may be used only as an input adapter. The canonical commit
  representation is keyed by episode identity.
- Paired execution adopts the same module after the single-lane adapters are
  proven, while preserving one comparison commit and one run finalization.

### Run completion and verdicts

- Introduce a deep `RunCompletionPipeline` module. Its external operation moves
  a successfully executed run through evaluation, report persistence, quality
  gate persistence, reporting, and terminal completion.
- A run cannot enter `completed` until its report and gate result are durable.
- `RunSupervisor` remains the sole owner of terminal run-event emission. It
  invokes the completion module and emits exactly one terminal run event from
  the returned durable result; the completion module must not create a second
  owner for `run.completed`, `run.failed`, or `run.cancelled`.
- Report and gate generation remain idempotent for the same immutable report
  input. Read endpoints may retain an idempotent repair fallback, but normal
  reads do not own first-time generation.
- Completion failure emits a stable error and leaves episode attempts and raw
  artifacts intact for diagnosis and recovery.
- Run summaries expose lifecycle state, gate verdict, and terminal outcome
  counts. The console presents those facts together on list and detail surfaces.

### Strict baseline eligibility

- Introduce `BaselineEligibility` as the single module that evaluates whether a
  selected lane may become a strict baseline.
- Eligibility requires a terminal completed run attempt, a persisted report,
  complete strict provenance, a complete selected-lane episode grid, and a
  successful terminal outcome for every planned episode in that selected lane.
- For strict baseline eligibility, `successful terminal outcome` means exactly
  `PASS`. `FAIL`, `ERROR`, `CANCELLED`, and missing or incomplete attempts are
  all ineligible, regardless of overall gate configuration or verdict.
- Overall paired-run gate failure does not automatically reject a healthy
  baseline lane, because the gate may describe the candidate lane. Eligibility
  is evaluated against the lane being promoted.
- Baseline creation returns structured rejection reasons and the UI disables or
  explains an unavailable promotion action.
- A future non-strict reference snapshot, if desired, must be a distinct concept
  rather than a bypass flag on strict baseline promotion.

### Deterministic acceptance adapter

- Add a deterministic adapter available only to tests and explicitly enabled
  development environments. It must not appear as a production model option.
- The adapter crosses the same runner event and artifact seams as production
  execution and produces deterministic pass, fail, error, action, screenshot,
  trajectory, and judge evidence fixtures.
- The highest acceptance seam is the public HTTP/SSE/artifact/replay/report
  interface. Browser smoke tests use the real console against a temporary API,
  database, and artifact root.
- The deterministic smoke suite covers one-lane execution, a three-step Manual
  Sequence with isolated state, and paired comparison. Model-quality claims are
  explicitly outside this suite.

### Model compatibility preflight

- Introduce a deep `ModelCompatibility` module with a typed request and a typed
  compatibility snapshot. It is not a persisted or versioned Execution Profile.
- The module takes current launch-time execution settings plus explicit agent
  requirements and delegates remote calls through an OpenAI-compatible adapter.
  Tests use a fake adapter at the same seam.
- Screenshot compatibility is verified with a minimal image request encoded by
  the same message-building implementation used during real inference.
- Stable outcomes include compatible, endpoint unreachable, authentication
  failed, model missing, vision unsupported, image format unsupported, timeout,
  and indeterminate provider response.
- The console exposes an explicit Test connection action. Run creation and
  Retry/Resume attempt creation for an agent with a known screenshot requirement
  perform or reuse an exact-match, short-lived successful check before new
  execution records are persisted.
- An explicit API-only skip may be supported for provider troubleshooting, but
  it is recorded in run provenance as `skipped`; it is never the console default.
- Probe requests are bounded by strict time and token limits. Secrets are
  accepted only for the request, redacted from logs and errors, and never stored
  in compatibility snapshots.
- Endpoint URLs receive the same scheme, credential, origin, and local-trust
  validation expected of other platform-controlled remote calls.

### Report completion

- Reliability reporting reuses the benchmark framework's canonical unbiased
  Pass@k estimator instead of introducing a second formula.
- Trials are grouped by stable materialization identity. Errors, cancelled
  attempts, and missing attempts remain visible but are excluded from valid
  Pass@k trial counts according to the existing benchmark contract.
- Flakiness requires at least two valid trials and at least one success plus one
  functional failure. Infrastructure errors are reported separately.
- Infrastructure reporting ingests versioned monitor samples and labels host,
  process, GPU, TCP, and model-server metrics separately from Agent and simulator
  execution phases.
- The expanded report is a new report schema version. Existing persisted report
  versions remain readable and exportable.
- Strict baselines gain a required display name, list, detail, and archive
  interfaces. Active names are unique within a project, and archiving releases a
  name for later reuse. Existing anonymous baseline rows receive deterministic,
  collision-safe legacy display names.
- Cross-release trend charts are not part of this PRD; named and discoverable
  baseline records are the prerequisite.

### Actionable diagnostics and incident locations

- Browser, page, network, and runner diagnostic events are connected to the
  platform event sink and normalized with stable run-attempt, lane-attempt,
  episode, worker, and step identity whenever that identity exists.
- Diagnostic persistence and query interfaces support category, severity,
  target, App, task, retryability, lane, episode, and attempt filters. Unknown
  identity is explicit and never guessed from display order.
- Diagnostics expose category, severity, retryability, recommended action,
  identity, and artifact facts without requiring the UI to reclassify raw error
  text.
- Retry and Resume expose a read-only selection preview before mutation. Empty
  selections disable the corresponding action and explain why.
- Observatory selection state is URL-addressable: lane, episode, attempt, step,
  screenshot mode, and evidence tab are represented by validated query
  parameters.
- Diagnostics, sequence report rows, comparison rows, and attempt history link
  through that URL contract. Invalid or stale parameters fall back safely and
  visibly.
- Incident URLs contain stable identities only. They never contain secrets or
  raw filesystem paths.

## Testing Decisions

- Tests assert externally observable outcomes across each module's interface.
  They do not assert private helper ordering or duplicate old shallow-module
  tests after a deeper interface replaces them.
- `LaneOutcomeCommitter` is tested with a temporary SQLite database and an
  in-memory event adapter. Its table-driven contract covers object and dictionary
  results, ordered and unordered delivery, missing results, cancellation,
  unknown results, duplicate results, artifact identity, terminal events, and
  exactly-once lane finalization.
- Executor integration tests prove that serial, parallel, multi-process, paired
  serial, and paired parallel adapters produce identical persisted facts for the
  same logical result matrix.
- `RunCompletionPipeline` tests use persisted episode attempts and assert state
  transitions, report/gate durability, idempotency, failure recovery, summary
  visibility, empty-policy `not_configured` behavior, and baseline eligibility
  only through its interface and public read interfaces.
- Deterministic acceptance tests run against temporary storage and real HTTP
  interfaces. Browser tests exercise the built console rather than mocking
  `fetch` or `EventSource`.
- The model compatibility production adapter is covered by a local fake
  OpenAI-compatible server that can simulate model listing, authentication,
  image acceptance, provider-specific rejection, timeout, and malformed
  responses. No live provider is required in CI.
- Model preflight tests assert that a failed required check creates no run or
  artifact records and that all secrets are absent from snapshots, logs, error
  payloads, and exports.
- Report modules remain pure transformations from immutable report input to
  versioned output. Golden tests cover Pass@k, valid-trial denominators,
  flakiness, missing monitor dimensions, infrastructure labelling, and v1/v2
  compatibility.
- Diagnostic and incident-link tests cover browser/page/network event ingestion,
  stable identity, filters, selection previews, disabled empty actions,
  query-parameter round trips, invalid parameter fallback, and links from every
  investigation entry point.
- Every vertical slice records its red test, focused verification, and observable
  demo. P1 concludes with the complete Milestone D regression commands on the
  active development platform.

## Out of Scope

- Versioned, immutable Execution Profiles.
- Lanes that select different model or Agent configurations for A/B comparison.
- Secret-provider selection or durable credential vaulting.
- Manual Sequence state or artifact carry-forward.
- Sequence-level judges and resume from an arbitrary carry-forward checkpoint.
- A general workflow canvas, arbitrary DAG execution, or arbitrary code nodes.
- Real-device execution, device pools, ADB collection, or App installation.
- Distributed workers, object storage, multi-user authorization, quotas, or
  cross-host scheduling.
- Cross-release trend dashboards; this PRD delivers the named-baseline
  prerequisite only.
- Incremental live screenshot and prompt/response evidence before episode
  termination. The current counter-only waiting state remains supported.
- Full replay timing controls, paired side-by-side live playback, or historical
  manual phone control.
- Claims about real-model task quality from deterministic platform smoke tests.

## Further Notes

- The future Execution Profile direction is intentionally recorded without a
  detailed contract in the product backlog. Design discussion begins only after
  this hardening plan's current work is complete.
- Existing design documents contain both prescriptive contracts and stale
  implementation-era descriptions. This PRD treats required lifecycle,
  reliability, monitoring, baseline, security, and end-to-end acceptance
  statements as prescriptive; implementation status text must be updated as the
  slices land.
- No external tracker issues are created by this document. The local task
  breakdown remains reviewable in `HARDENING_PLAN.md` before optional issue
  publication.
