# MobileGym Test Platform Product Requirements Document

## Document status

| Field | Value |
|---|---|
| Status | Implemented; hardening release accepted 2026-07-13 |
| Product phase | Simulator-first MVP, hardened |
| Primary repository | `mobilegym` |
| Primary execution engine | `bench_env` |
| Real-device support | Interface and configuration reserved; execution deferred |
| Intended audience | Product, platform, frontend, benchmark, and test engineers |

## 1. Summary

MobileGym Test Platform turns the existing simulator and `bench_env` benchmark
framework into a test development and execution product.

The platform will let test engineers:

- discover and organize existing task templates;
- define reusable workflow versions;
- run functional tests against one or more simulator targets;
- monitor execution progress, screenshots, logs, metrics, and errors in real time;
- compare simulator profiles or App versions with deterministic paired runs;
- generate functional, reliability, and performance reports;
- retain enough provenance to reproduce every result;
- add real-device targets later without replacing the workflow or reporting model.

The product reuses the existing MobileGym task lifecycle, state injection,
programmatic judges, parallel runners, trajectory recorder, and monitor. It does
not introduce a second UI-action test language.

## 2. Background

MobileGym already provides the core execution primitives required by a test
platform:

| Existing capability | Current owner |
|---|---|
| Browser-hosted mobile simulator | `os/`, `apps/`, `system/` |
| Reset, state injection, snapshot, input, and OS control | Browser runtime APIs |
| Parameterized task templates | `bench_env/task/` |
| Deterministic state-based judging | `bench_env/task/judge.py` |
| Serial, parallel, and multi-process execution | `bench_env/runner/` |
| Screenshots, trajectories, prompts, responses, and result artifacts | `bench_env/env/recorder.py` |
| Episode phase timing | `bench_env/env/stopwatch.py` |
| Host, GPU, and vLLM monitoring | `bench_env/monitor.py` |
| Post-run trajectory inspection | `public/run_explorer.html` |

The missing product layer is a persistent control plane that can define
workflows, address multiple targets, schedule runs, stream events, normalize
errors, compare paired results, and present reports.

## 3. Problem statement

Today, test execution is primarily CLI-driven and run output is stored as files.
This creates several limitations:

1. Test selection and runner configuration are not reusable product objects.
2. A run cannot natively contain baseline and candidate target lanes.
3. All workers in one `EnvPool` use the same simulator URL and device shape.
4. A simulator bundle contains one compile-time implementation per App ID.
5. App manifest versions are not included in run provenance.
6. Live progress is exposed through console output and incrementally written
   JSONL files, but not through a stable external event contract.
7. Errors from setup, environment, model, action, App, judge, and infrastructure
   are not normalized for product-level diagnosis.
8. The existing Run Explorer is read-only and optimized for post-run inspection,
   not test authoring or live operations.

## 4. Product goals

### 4.1 MVP goals

- Provide a simulator-first test control console.
- Preserve existing `bench_env` CLI behavior and artifact formats.
- Treat existing `BaseTask` definitions as the functional test case catalog.
- Support reusable, versioned workflows at orchestration granularity.
- Support one-target runs and paired baseline/candidate comparison runs.
- Support these comparison modes:
  - different simulator profiles running the same App revision;
  - equivalent simulator profiles running different App revisions;
  - different simulator deployments running the same task set.
- Use identical task instances, seeds, initial conditions, time, location, and
  execution settings across paired lanes.
- Stream run, lane, episode, and error progress to the console.
- Generate functional, reliability, performance, and comparison reports.
- Record simulator, App, data, workflow, task, model, and configuration
  provenance.
- Define target and environment adapter contracts that can later support real
  Android devices.

### 4.2 Longer-term goals

- Real-device pools and ADB-based execution.
- App artifact installation and lifecycle management.
- Scheduled runs and CI/CD integrations.
- Multi-user access control and audit logs.
- Distributed workers and object storage.
- Trend analysis across many releases.
- Real-device performance collection through Android platform tools.

## 5. Non-goals for the MVP

- Replacing `BaseTask`, task sampling, or judge definitions.
- Building a click-by-click workflow language.
- Loading two implementations of the same App ID into one simulator bundle.
- Building or deploying App bundles inside the platform.
- Full real-device execution.
- Multi-tenant billing, quotas, or enterprise identity integration.
- Treating browser simulator performance as equivalent to physical Android
  device performance.
- Replacing the existing CLI, Run Explorer, or JSONL artifacts.

## 6. Product principles

| Principle | Requirement |
|---|---|
| Reuse before replacement | Existing tasks, runners, judges, and artifacts remain authoritative. |
| Reproducibility first | Every comparison must freeze inputs and record target provenance. |
| Paired comparison | Baseline and candidate results are matched by a stable episode key. |
| State-based truth | Simulator functional verdicts continue to use structured state judges. |
| Explicit performance domains | Agent, harness, simulator UI, host, and real-device metrics are labeled separately. |
| Artifact compatibility | Existing run directories remain readable by current tools. |
| Adapter boundaries | Simulator and future real-device targets implement the same lifecycle contract. |
| Constrained workflows | Workflows orchestrate runs and gates, not individual UI gestures. |

## 7. Users and primary jobs

| Persona | Primary jobs |
|---|---|
| Test engineer | Select tasks, build workflows, run regressions, inspect failures, export reports. |
| App engineer | Compare App versions, identify newly failing tasks, inspect screenshots and logs. |
| Agent engineer | Compare models or prompts, monitor inference latency, analyze false completion and loops. |
| Platform operator | Manage simulator targets, concurrency, run health, storage, and infrastructure errors. |
| Benchmark author | Validate new task coverage and inspect judge outcomes without changing the execution model. |

## 8. Core product concepts

| Concept | Definition |
|---|---|
| Project | Top-level namespace for targets, workflows, runs, reports, and baselines. |
| Task template | Existing registered `BaseTask` class and its metadata. |
| Task selection | Immutable query or explicit task list captured in a workflow version. |
| Target | Addressable execution destination, such as a simulator deployment or future real device. |
| Target revision | Immutable metadata snapshot discovered from a target at run start. |
| Workflow | User-owned reusable orchestration definition. |
| Workflow version | Immutable DAG definition used by a run. |
| Run | One execution of one workflow version. |
| Lane | One target-specific branch of a run, for example `baseline` or `candidate`. |
| Episode | One task instance and trial executed on one lane. |
| Comparison | Pairing and delta analysis between two lanes. |
| Baseline | Approved target or historical run used as the comparison reference. |
| Artifact | Screenshot, trajectory, prompt, response, browser log, monitor file, or raw result. |
| Report | Derived functional, reliability, performance, or comparison output. |
| Event | Ordered state transition emitted during execution. |

## 9. Target model

### 9.1 Common target contract

Every target must expose or resolve the following fields:

```yaml
id: target-id
name: Human-readable name
kind: simulator | real_device
enabled: true
connection: {}
device_profile: {}
app_artifact: {}
labels: {}
capabilities: []
```

The execution service will use an environment adapter with these conceptual
operations:

```text
health()
resolve_metadata()
prepare(run_context)
create_environment(worker_context)
reset(environment, episode_context)
collect_observation(environment)
collect_diagnostics(environment)
close_environment(environment)
cleanup(run_context)
```

### 9.2 Simulator target

Required simulator configuration:

```yaml
kind: simulator
connection:
  env_url: http://localhost:4173
device_profile:
  name: pixel7-default
  viewport_width: 360
  viewport_height: 800
  physical_width: 1080
  physical_height: 2400
  device_scale_factor: 3
runtime:
  storage_isolation: tab
  headless: true
```

Optional simulator scenario configuration includes:

- OS build and telephony overrides;
- time and location presets;
- display settings;
- network proxy;
- simulator skin;
- labels describing browser, OS profile, or deployment environment.

### 9.3 Simulator target revision

At run start, the service must resolve and persist:

| Metadata | Example |
|---|---|
| Target URL | `http://localhost:4173` |
| Simulator build ID | Git SHA, build ID, or immutable image tag |
| Simulator version | Product release version |
| App versions | App ID, package name, `version`, and `versionCode` |
| App bundle hash | Build-provided immutable bundle identifier |
| Data revision | Dataset or world-data revision |
| Runtime config hash | Canonical hash of relevant simulator configuration |
| Device profile | Viewport, physical size, DPR, skin, OS build, carrier |
| Health timestamp | Time at which target metadata was verified |

The simulator runtime currently exposes App IDs but not App versions in its state
snapshot. The implementation must add a metadata API or service-side manifest
collection so reports never infer versions from user-entered labels alone.

### 9.4 Reserved real-device target

The MVP stores and validates real-device configuration but does not schedule it:

```yaml
kind: real_device
connection:
  provider: adb
  serial: emulator-or-device-serial
device_profile:
  manufacturer: optional
  model: optional
  android_version: optional
app_artifact:
  package_name: optional
  version_name: optional
  version_code: optional
  artifact_uri: optional
runtime:
  install_policy: reuse | reinstall | upgrade
  reset_policy: app_data | snapshot | none
```

The API must return a clear `TARGET_KIND_NOT_EXECUTABLE` validation result while
real-device scheduling is disabled.

## 10. Comparison model

### 10.1 Supported comparison scenarios

#### Scenario A: different simulators, same App revision

Use two simulator targets with different device profiles, OS scenarios, browser
revisions, or simulator deployments while pinning the same App build metadata.

```yaml
baseline:
  target: pixel7-simulator
candidate:
  target: small-screen-simulator
constraints:
  require_same_app_revision: true
```

#### Scenario B: equivalent simulators, different App revisions

Deploy App revision A and B as separate simulator bundles or endpoints while
using equivalent device profiles and runtime configuration.

```yaml
baseline:
  target: app-v1-simulator
candidate:
  target: app-v2-simulator
constraints:
  require_same_device_profile: true
```

Two App versions are not loaded into the same browser bundle. "Same simulator"
means equivalent simulator profile and configuration across two separately
addressable target revisions.

### 10.2 Pairing rules

Every comparison episode pair uses this logical key:

```text
task_base_id
+ sampled_instance_id
+ sample_seed
+ trial_id
+ instruction_revision
```

Both lanes must share:

- task selection and ordering;
- task template revision;
- `sample_seed`;
- sampled parameters;
- initial state fixture or snapshot;
- simulated time and location;
- Agent adapter, model parameters, and prompt configuration unless the workflow
  explicitly declares an Agent comparison;
- maximum step budget;
- judge mode and evaluation mode;
- data revision unless the workflow explicitly declares a data comparison.

If a pair cannot be constructed, the report must mark it `UNPAIRED` rather than
silently comparing aggregate values.

### 10.3 Comparison classifications

| Classification | Baseline | Candidate |
|---|---:|---:|
| Regression | Pass | Fail |
| Fixed | Fail | Pass |
| Stable pass | Pass | Pass |
| Stable fail | Fail | Fail |
| Baseline error | Error | Any non-error |
| Candidate error | Any non-error | Error |
| Unpaired | Missing or present | Present or missing |
| Flaky | Inconsistent repeated trials | Inconsistent repeated trials |

## 11. Workflow model

### 11.1 Workflow boundary

A workflow orchestrates task sets, target lanes, matrices, execution, gates,
comparison, and publication. UI gestures remain inside the existing task and
Agent loop.

### 11.2 MVP node types

| Node type | Purpose |
|---|---|
| `task_selection` | Resolve explicit tasks, suites, splits, or taxonomy filters. |
| `matrix` | Expand targets, Agents, seeds, or repeat counts. |
| `execute` | Run the compiled episode plan through `bench_env`. |
| `quality_gate` | Evaluate functional or performance thresholds. |
| `compare` | Pair baseline and candidate lanes and calculate deltas. |
| `publish_report` | Persist report output and expose export artifacts. |

### 11.3 Workflow constraints

- A workflow must be a directed acyclic graph.
- A workflow version is immutable after its first run.
- Editing a workflow creates a new version.
- Execution operates on a compiled immutable `RunPlan`.
- Node inputs and outputs use typed schemas.
- Cycles, unknown node types, missing targets, and incompatible comparisons fail
  validation before execution.
- MVP branching is limited to matrix expansion and quality-gate outcomes.
- Arbitrary Python or shell execution is not an MVP node type.

### 11.4 Example workflow

```yaml
name: WeChat App version regression
nodes:
  - id: tasks
    type: task_selection
    config:
      suites: [wechat]
      difficulty: [L1, L2, L3]

  - id: versions
    type: matrix
    depends_on: [tasks]
    config:
      lanes:
        baseline: wechat-v1
        candidate: wechat-v2
      sample_seed: 42
      repeat_n: 3

  - id: execute
    type: execute
    depends_on: [versions]
    config:
      parallel: 8
      isolation: pages

  - id: compare
    type: compare
    depends_on: [execute]

  - id: gate
    type: quality_gate
    depends_on: [compare]
    config:
      max_regressions: 0
      max_success_rate_drop: 0.02
      max_runtime_p95_increase: 0.10

  - id: report
    type: publish_report
    depends_on: [compare, gate]
```

## 12. Functional requirements

### 12.1 Project and catalog

| ID | Requirement |
|---|---|
| FR-001 | Users can create, rename, view, and archive projects. |
| FR-002 | The platform lists registered task templates without duplicating task definitions in the database. |
| FR-003 | Task catalog filters include suite, App, difficulty, scope, objective, composition, capability, and AnswerSheet usage. |
| FR-004 | Task detail shows templates, parameters, Apps, maximum steps, taxonomy, and optimal-path metadata. |
| FR-005 | The platform identifies task registry or source revision used by every workflow version and run. |

### 12.2 Target management

| ID | Requirement |
|---|---|
| FR-010 | Users can create and edit simulator targets. |
| FR-011 | Users can test target connectivity and view health results. |
| FR-012 | Target health resolves simulator, App, data, and device-profile metadata. |
| FR-013 | Target secrets and proxy credentials are never returned to the browser after creation. |
| FR-014 | Users can clone a target configuration to create an equivalent comparison profile. |
| FR-015 | Users can create disabled real-device targets using the reserved schema. |
| FR-016 | Runs persist immutable target revisions rather than mutable target references alone. |

### 12.3 Workflow authoring

| ID | Requirement |
|---|---|
| FR-020 | Users can create a workflow from task selection, target lanes, execution settings, gates, and report settings. |
| FR-021 | Users can validate a workflow without running it. |
| FR-022 | Workflow validation returns structured field and node errors. |
| FR-023 | Users can save drafts and publish immutable workflow versions. |
| FR-024 | Users can duplicate a workflow or create a new version. |
| FR-025 | Users can preview the compiled task count, episode count, target lanes, and estimated artifact volume. |
| FR-026 | Workflow definitions remain exportable and importable as JSON or YAML. |

### 12.4 Run orchestration

| ID | Requirement |
|---|---|
| FR-030 | Users can start a run from a published workflow version. |
| FR-031 | The service creates an immutable `RunPlan` before allocating environments. |
| FR-032 | The service supports serial, parallel, and multi-process simulator execution through existing runners. |
| FR-033 | Users can cancel queued or running runs. |
| FR-034 | Users can retry errors, failed episodes, or selected episodes without losing the original result. |
| FR-035 | Interrupted runs can resume missing episodes when the target revision and workflow revision remain compatible. |
| FR-036 | Runs expose queued, preparing, running, evaluating, reporting, completed, cancelled, and failed states. |
| FR-037 | Lane execution is isolated so one target failure does not corrupt the other lane's artifacts. |
| FR-038 | Existing CLI execution remains usable without the platform service. |

### 12.5 Live monitoring

| ID | Requirement |
|---|---|
| FR-040 | The browser receives ordered run events over SSE. |
| FR-041 | The run view shows completed, passed, failed, errored, and remaining episode counts. |
| FR-042 | The run view shows lane and worker health. |
| FR-043 | The run view shows the latest available screenshot and action for active episodes when trajectory saving is enabled. |
| FR-044 | The run view streams normalized errors and links them to logs and artifacts. |
| FR-045 | The run view displays current host, GPU, and vLLM samples when monitoring is enabled. |
| FR-046 | SSE reconnection resumes from the last acknowledged event ID. |
| FR-047 | A page refresh does not lose run state because the database and artifact files remain authoritative. |

### 12.6 Error diagnosis

| ID | Requirement |
|---|---|
| FR-050 | Errors are normalized into the taxonomy defined in section 13. |
| FR-051 | Every error includes phase, lane, task, trial, worker, timestamp, retryability, and artifact references when available. |
| FR-052 | App console errors, page errors, failed requests, and HTTP failures are searchable by task context. |
| FR-053 | Users can filter errors by category, target, App, task, and retryability. |
| FR-054 | A failed functional assertion is distinguished from infrastructure and judge errors. |

### 12.7 Reports and comparisons

| ID | Requirement |
|---|---|
| FR-060 | A completed run produces a functional report. |
| FR-061 | Repeated trials produce reliability and Pass@k analysis. |
| FR-062 | Timing data produces an execution performance report with percentiles. |
| FR-063 | Monitor samples produce host and inference infrastructure charts. |
| FR-064 | A two-lane run produces paired comparison classifications and deltas. |
| FR-065 | Comparison reports show functional regressions, fixes, side-effect changes, step deltas, runtime deltas, and error deltas. |
| FR-066 | Episode comparison provides side-by-side screenshots, actions, judge details, and artifact links. |
| FR-067 | Reports can be exported as JSON and a printable HTML document. |
| FR-068 | A run can be promoted as a named baseline. |
| FR-069 | Quality-gate results are persisted with the exact threshold configuration. |

### 12.8 Artifact and provenance

| ID | Requirement |
|---|---|
| FR-070 | The platform preserves current `meta.json`, `results.jsonl`, `summary.json`, `errors.jsonl`, and trajectory artifacts. |
| FR-071 | Platform metadata references existing artifact paths instead of storing screenshots in the database. |
| FR-072 | A run records workflow version, task source revision, target revisions, App versions, data revision, Agent configuration, sampling seed, and effective runner configuration. |
| FR-073 | The system calculates a canonical reproducibility fingerprint for every lane. |
| FR-074 | Secrets are excluded from run metadata and exported reports. |
| FR-075 | Existing Run Explorer can open platform-created run artifacts. |

## 13. Error taxonomy

| Code | Category | Default retryability |
|---|---|---:|
| `TARGET_UNREACHABLE` | Target health or connection failure | Yes |
| `TARGET_METADATA_INVALID` | Missing or inconsistent target provenance | No |
| `ENV_START_ERROR` | Browser or environment creation failed | Yes |
| `ENV_RESET_ERROR` | Simulator reset or state cleanup failed | Yes |
| `TASK_SETUP_ERROR` | Task preparation, sampling, or state injection failed | Depends |
| `APP_LOAD_ERROR` | App module or data loader failed | Depends |
| `APP_CONSOLE_ERROR` | App emitted a captured runtime error | No automatic retry |
| `NETWORK_ERROR` | Required request failed | Yes |
| `MODEL_TIMEOUT` | Agent inference exceeded timeout | Yes |
| `MODEL_RESPONSE_ERROR` | Model endpoint or response was invalid | Yes |
| `ACTION_FORMAT_ERROR` | Agent emitted an invalid action payload | No |
| `ACTION_EXECUTION_ERROR` | Environment could not execute a valid action | Yes |
| `MAX_STEPS` | Episode exhausted its step budget | No |
| `REPETITIVE_LOOP` | Loop detector terminated the episode | No |
| `JUDGE_ERROR` | Judge implementation or VLM judge failed | Depends |
| `ASSERTION_FAILURE` | Functional goal was not achieved | No |
| `UNEXPECTED_SIDE_EFFECT` | Goal may be achieved but run is not clean | No |
| `PERFORMANCE_REGRESSION` | Configured performance gate failed | No |
| `WORKER_CRASH` | Runner process or browser worker crashed | Yes |
| `RUN_CANCELLED` | User or system cancelled execution | No |

The implementation may retain lower-level messages and stack traces, but product
logic and filters use these stable codes.

## 14. Event model

Events are append-only and ordered per run.

```json
{
  "event_id": 1024,
  "run_id": "run_123",
  "type": "episode.completed",
  "timestamp": "2026-07-03T12:00:00Z",
  "lane_id": "candidate",
  "episode_key": "wechat.ReadMyWxid|i0|seed42|t0",
  "payload": {}
}
```

Required event families:

- `run.created`, `run.state_changed`, `run.cancel_requested`, `run.completed`;
- `lane.preparing`, `lane.running`, `lane.completed`, `lane.failed`;
- `worker.started`, `worker.health`, `worker.stopped`;
- `episode.queued`, `episode.started`, `episode.step`, `episode.evaluating`,
  `episode.completed`, `episode.error`;
- `metric.sample`;
- `artifact.created`;
- `report.started`, `report.completed`;
- `gate.passed`, `gate.failed`.

High-volume step events may contain references rather than embedded screenshots
or prompt content.

## 15. Reporting requirements

### 15.1 Functional report

- Success rate and counts.
- Progress rate.
- False completion rate.
- Overdue termination rate.
- Unexpected side-effect rate.
- Average and percentile step counts.
- Breakdown by suite, App, difficulty, scope, objective, composition, and
  capability.
- Goal issues and unexpected state changes.
- Failed and errored task lists.

### 15.2 Reliability report

- Trial distribution per task.
- Pass@k.
- Per-task success variance.
- Flaky task and flaky target detection.
- Retry outcome analysis.

### 15.3 Execution performance report

Report percentiles for available episode phases:

- total runtime;
- simulator reset and readiness;
- inference queue and execution;
- action execution and post-action delay;
- observation capture;
- screenshot, route, and state collection;
- recording;
- judge evaluation.

The report must label these as harness, Agent, or simulator execution metrics.

### 15.4 Infrastructure report

- Host load and memory.
- Browser, benchmark, gateway, and model-server process counts and RSS.
- TCP connection states.
- GPU utilization, memory, and temperature.
- vLLM running and waiting requests.
- Prompt and generation throughput.
- End-to-end latency, time to first token, and queue time.
- Prefix-cache hit rate.

### 15.5 Simulator UI performance extension

Browser rendering metrics are not complete in the existing framework. A later
simulator phase may add:

- long tasks;
- layout shifts;
- JavaScript heap;
- frame and jank estimates;
- network resource timing;
- action-to-stable-render latency.

These metrics must be reported separately from real Android device performance.

### 15.6 Comparison report

- Target revision summary.
- Pair coverage and unpaired episodes.
- Regression, fixed, stable-pass, stable-fail, and error counts.
- Success-rate and progress-rate delta.
- Side-effect and termination delta.
- Per-phase performance delta and configured threshold breaches.
- Side-by-side episode drill-down.
- Gate verdict and reasons.

## 16. Information architecture

The primary console navigation:

| View | Purpose |
|---|---|
| Runs | Active queue, live progress, recent runs, and run actions. |
| Workflows | Draft, validate, version, and launch workflows. |
| Tasks | Browse task templates and metadata. |
| Targets | Configure simulator targets and inspect health or revisions. |
| Reports | Functional, performance, comparison, and baseline reports. |
| Project settings | Storage, defaults, retention, and future integrations. |

The first screen is the operational Runs view, not a marketing landing page.

## 17. High-level service API

The exact transport schema will be specified separately. The MVP requires these
resource groups:

```text
GET/POST/PATCH /api/projects
GET              /api/tasks
GET/POST/PATCH   /api/targets
POST             /api/targets/{id}/health
GET/POST/PATCH   /api/workflows
POST             /api/workflows/{id}/validate
POST             /api/workflows/{id}/versions
POST             /api/runs
GET              /api/runs/{id}
POST             /api/runs/{id}/cancel
POST             /api/runs/{id}/retry
GET              /api/runs/{id}/events
GET              /api/runs/{id}/events/stream
GET              /api/runs/{id}/artifacts/*
GET              /api/reports/{id}
POST             /api/runs/{id}/baseline
```

The service invokes `bench_env` through Python APIs, not by parsing terminal
output. CLI subprocess execution may be retained only as a compatibility or
diagnostic fallback.

## 18. Persistence and artifact layout

### 18.1 MVP persistence

- SQLite stores projects, targets, target revisions, workflows, workflow
  versions, runs, lanes, events, gates, report indexes, and artifact references.
- Existing run directories store raw execution artifacts.
- Database migrations are versioned.
- Database rows use generated IDs and timestamps independent from artifact
  directory names.

### 18.2 Run artifact compatibility

Platform-created runs retain the current structure and may add files:

```text
runs/<run-id>/
  meta.json
  results.jsonl
  summary.json
  errors.jsonl
  monitor.csv
  console.log
  browser_logs/
  trajectory/
  platform/
    run-plan.json
    target-revisions.json
    events.jsonl
    functional-report.json
    performance-report.json
    comparison-report.json
    gate-result.json
```

## 19. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-001 | A persisted run can recover its displayed state after service restart. |
| NFR-002 | Event ordering is stable within a run and SSE delivery is resumable. |
| NFR-003 | Existing benchmark runs remain readable without migration. |
| NFR-004 | No model, proxy, or judge API key is persisted in raw run metadata. |
| NFR-005 | Target revision collection fails closed when required comparison provenance is missing. |
| NFR-006 | The control service does not block the asyncio event loop with runner or file operations. |
| NFR-007 | MVP supports at least the current single-host parallel execution limits of `bench_env`. |
| NFR-008 | Cancelling a run releases browser, worker, and monitor resources. |
| NFR-009 | One failed episode does not stop unrelated queued episodes unless policy requires fail-fast. |
| NFR-010 | Report calculations are deterministic for identical persisted inputs. |
| NFR-011 | The UI remains usable while large result and trajectory files are loading. |
| NFR-012 | Runtime APIs validate paths and prevent artifact directory traversal. |

## 20. Compatibility requirements

- `python -m bench_env.run` remains supported.
- `RunnerConfig` remains the source for effective runner options.
- Existing task discovery and filters remain authoritative.
- Existing `EpisodeResult` and `JudgeResult` semantics remain unchanged unless a
  separately reviewed migration is introduced.
- Existing run files remain valid inputs to the report importer.
- Platform event hooks must be additive to runner behavior.
- The Run Explorer remains available as an artifact-level trajectory viewer.

## 21. MVP acceptance criteria

The simulator-first MVP is accepted when all of the following are demonstrated:

1. A user can register two simulator targets and verify their target revisions.
2. Target revisions include App `version` and `versionCode`.
3. A user can create and publish a workflow selecting existing tasks.
4. A one-target workflow can start, stream progress, cancel, complete, and
   produce artifacts compatible with the existing Run Explorer.
5. A two-target workflow executes identical paired task instances with the same
   seed and initial conditions.
6. The platform can compare different simulator profiles using the same App
   revision.
7. The platform can compare equivalent simulator profiles using different App
   revisions hosted at separate endpoints.
8. The comparison report identifies regressions, fixes, stable outcomes, errors,
   and unpaired episodes.
9. Functional reports include existing MobileGym metrics and taxonomy
   breakdowns.
10. Performance reports include episode phase percentiles and available monitor
    metrics.
11. Errors use stable codes and link to task artifacts.
12. A service restart does not lose completed run metadata or reports.
13. Existing CLI runs can be imported or opened without rewriting their raw
    artifacts.
14. A real-device target can be stored and validated as unsupported for
    execution without changing workflow schemas.

## 22. Delivery phases

### Phase 0: contracts and foundations

- Product and architecture specifications.
- Domain schemas and database migrations.
- Target adapter and event contracts.
- Simulator metadata endpoint.
- Runner event-hook design.

### Phase 1: simulator run control

- Projects, task catalog, and simulator targets.
- Workflow drafts and immutable versions.
- One-target run execution.
- SSE progress and cancellation.
- Artifact browsing and existing Run Explorer integration.

### Phase 2: comparison and reports

- Baseline/candidate lanes.
- Frozen paired episode plans.
- Target revision constraints.
- Functional and performance reports.
- Comparison drill-down and quality gates.

### Phase 3: operational maturity

- Retry and resume UX.
- Baselines and trend history.
- Retention controls.
- CI-friendly API and report exports.
- Improved browser UI performance instrumentation.

### Phase 4: real-device execution

- Real-device adapter implementation.
- Device pool and lease management.
- App installation and reset policies.
- ADB diagnostics and Android performance collection.
- Simulator-to-device comparison policies.

## 23. Risks and mitigations

| Risk | Mitigation |
|---|---|
| App versions are user-entered and inaccurate | Resolve versions from the running target and fail comparison validation when missing. |
| Paired lanes sample different parameters | Compile and persist sampled parameters once, then clone them into both lanes. |
| Dataset differences look like App regressions | Record data revision and require equality by default. |
| Target configuration mutates during a run | Persist immutable target revisions before execution. |
| Multi-process progress is inconsistent | Introduce one event sink contract and adapt existing shard progress aggregation. |
| Large artifacts overload the database or UI | Store files on disk, index metadata, paginate results, and lazy-load trajectory content. |
| Browser performance is mistaken for Android performance | Label metric domains and keep real-device performance as a separate adapter capability. |
| Platform changes break CLI users | Keep hooks additive and preserve `RunnerConfig` plus artifact formats. |
| Run cancellation leaks browser processes | Require lifecycle cleanup acceptance tests for every runner mode. |

## 24. Assumptions and deferred decisions

The following defaults are approved for initial design and may be revisited:

- local single-user operation;
- React and Vite for the console;
- Python control service close to `bench_env`;
- SQLite for metadata;
- SSE for server-to-browser events;
- filesystem artifacts under `runs/`;
- simulator deployments and App builds are prepared outside the platform;
- no arbitrary-code workflow nodes;
- real-device configuration is accepted but execution is disabled.

Detailed architecture, API schemas, database schemas, and frontend interaction
specifications will be derived from this PRD.
