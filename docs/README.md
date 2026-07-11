# MobileGym Documentation

Welcome. This index points you at the right doc for what you're trying to do. The repository [`README.md`](../README.md) is the project overview — these docs go deeper.

All docs here are in English. Pick a track:

## Where to start

| If you want to… | Read this |
|---|---|
| 🚀 Install and run your first task | [`getting-started.md`](getting-started.md) |
| 🏗️ Understand how the layers fit together | [`platform/architecture.md`](platform/architecture.md) |
| 📱 Add a new app to the simulator | [`guides/add-an-app.md`](guides/add-an-app.md) |
| 🧪 Add a new task and judge | [`guides/add-a-task.md`](guides/add-a-task.md) |
| 🤖 Plug in a new agent (model adapter) | [`guides/add-an-agent.md`](guides/add-an-agent.md) |
| 📊 Benchmark an agent on MobileGym-Bench | [`guides/bench-an-agent.md`](guides/bench-an-agent.md) |
| 🔌 Look up the browser-console debug API | [`api/runtime-api.md`](api/runtime-api.md) |
| 🤝 Contribute code or report a bug | [`../CONTRIBUTING.md`](../CONTRIBUTING.md) |

## Test platform planning

| Topic | Document |
|---|---|
| Simulator-first workflow, comparison, monitoring, and reporting product requirements | [`test-platform/PRD.md`](test-platform/PRD.md) |
| Control service, runner integration, persistence, API, event, and frontend architecture | [`test-platform/TECHNICAL_ARCHITECTURE.md`](test-platform/TECHNICAL_ARCHITECTURE.md) |
| Code-level contracts, schemas, algorithms, transaction boundaries, and test seams | [`test-platform/IMPLEMENTATION_DESIGN.md`](test-platform/IMPLEMENTATION_DESIGN.md) |
| TDD-driven, independently verifiable vertical-slice development plan | [`test-platform/DEVELOPMENT_PLAN.md`](test-platform/DEVELOPMENT_PLAN.md) |
| Post-MVP correctness, deterministic acceptance, model preflight, reporting, and diagnostics requirements | [`test-platform/HARDENING_PRD.md`](test-platform/HARDENING_PRD.md) |
| Prioritized hardening milestones and agent-ready vertical task slices | [`test-platform/HARDENING_PLAN.md`](test-platform/HARDENING_PLAN.md) |
| Deferred Test Platform product directions that are recorded but not yet specified | [`test-platform/PRODUCT_BACKLOG.md`](test-platform/PRODUCT_BACKLOG.md) |
| Manual Sequence v1 product and execution contract | [`test-platform/MANUAL_SEQUENCE_V1.md`](test-platform/MANUAL_SEQUENCE_V1.md) |
| Run Observatory replay and live-inspection design | [`test-platform/VS-15_RUN_OBSERVATORY_DESIGN.md`](test-platform/VS-15_RUN_OBSERVATORY_DESIGN.md) |
| Local operation, legacy import, Run Explorer, and regression commands | [`test-platform/OPERATOR_GUIDE.md`](test-platform/OPERATOR_GUIDE.md) |

## Platform deep dive

For implementation-level rules and contracts — when you're extending the simulator itself, debugging unusual behavior, or building a new app whose needs aren't covered by the tutorial. See [`platform/README.md`](platform/README.md) for the orientation.

| Topic | Spec |
|---|---|
| OS internals — TaskManager, BackDispatcher, IntentResolver, lifecycle | [`platform/os/overview.md`](platform/os/overview.md) |
| App module contract — how apps integrate, file conventions, runtime hooks | [`platform/app/module-contract.md`](platform/app/module-contract.md) |
| State model — layered runtime/world data, snapshots, diffs, side effects | [`platform/state/model.md`](platform/state/model.md) |
| Declarative navigation — FSM, transitions, actions, graph generation | [`platform/navigation/declaration.md`](platform/navigation/declaration.md) |
| Intent system — cross-app calls, launchMode, choosers | [`platform/os/intent-system.md`](platform/os/intent-system.md) |
| Cross-app launch — Task/back-stack traces, `openApp`, `newTask` | [`platform/os/cross-app-launch.md`](platform/os/cross-app-launch.md) |
| OS services — Time, Location, Network, SMS, Display, Clipboard, etc. | [`platform/os/services/README.md`](platform/os/services/README.md) |
| Display scaling — system zoom, font size, per-app design width | [`platform/os/services/display-scaling.md`](platform/os/services/display-scaling.md) |
| Android mapping — which AOSP concepts we model, which we simplify | [`platform/android-mapping.md`](platform/android-mapping.md) |

## Benchmark / task authoring

Pair `bench_env/` and its docs when authoring tasks:

| Doc | Read for |
|---|---|
| [`../bench_env/docs/task/TASK_AUTHORING_GUIDE.md`](../bench_env/docs/task/TASK_AUTHORING_GUIDE.md) | Task authoring workflow — audit app state, add helpers, write the task, tune sampling |
| [`../bench_env/docs/task/TASK_CODE_SPEC.md`](../bench_env/docs/task/TASK_CODE_SPEC.md) | Hard task-code spec — taxonomy, templates, parameters, judge rules, forbidden patterns, checklist |
| [`../bench_env/docs/REFERENCE.md`](../bench_env/docs/REFERENCE.md) | Formal field reference — `JudgeInput`/`JudgeResult` shapes, CLI flags, metadata vocab |
| [`../bench_env/docs/task/GROUNDED_MODE.md`](../bench_env/docs/task/GROUNDED_MODE.md) | The AnswerSheet / grounded-mode protocol |
| [`../bench_env/docs/task/TASK_TESTING_GUIDE.md`](../bench_env/docs/task/TASK_TESTING_GUIDE.md) | Task testing guide (`OFFLINE_JUDGE_POSITIVE_CASES`, `NEGATIVE_CASES`, fixtures) |
| [`../bench_env/docs/FRAMEWORK.md`](../bench_env/docs/FRAMEWORK.md) | Runner lifecycle, Episode pipeline, sampling, parallel execution |

## Conventions used in these docs

- **Code paths and identifiers** are real — copy-paste them into `grep` and you'll find what's referenced.
- **Tables** are the load-bearing format. We avoid prose for things that are really enumerations.
- **`(advanced)` or **`(internal)`** tags mark sections most readers don't need.
- These docs are written against the **current main branch**. If a doc disagrees with the code, file an issue — the code wins.
