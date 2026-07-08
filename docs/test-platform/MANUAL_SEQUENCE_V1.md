# Manual Sequence Workflow v1

## Document status

| Field | Value |
|---|---|
| Status | Accepted for M00 implementation planning |
| Product phase | Manual mode MVP |
| Scope | Linear ordered execution, single lane, isolated state |
| Non-goal | General workflow canvas, arbitrary DAG runtime, runtime LLM scheduler |

## 1. Purpose

Manual Sequence v1 adds a constrained manual workflow mode to the Test Platform.
It lets a test engineer choose an explicit ordered list of existing task
templates and run them in that order. The feature is intentionally narrow: it
improves workflow authoring and observability without changing the task
contract, introducing state handoff, or replacing `BaseTask` composition.

The core promise is:

- tasks run in the user-specified order;
- each task remains an independent benchmark episode;
- each episode keeps the current setup, judge, teardown, artifact, retry, and
  resume boundaries;
- reports and replay can group the episodes as an ordered sequence.

## 2. Goals

- Support a `Manual sequence` workflow authoring mode in the console.
- Preserve the exact order of `task_ids` selected by the user.
- Compile the ordered task list into immutable `RunPlan` episodes.
- Execute the ordered episodes on one lane with one worker.
- Record sequence metadata so APIs, replay, and reports can display stage order.
- Keep v1 reproducible, auditable, and compatible with existing task execution.

## 3. Non-goals

- No branching.
- No multi-lane or paired comparison.
- No `parallel > 1` or `processes > 1`.
- No `repeat_n > 1`.
- No state or artifact handoff from one task to the next.
- No runtime LLM planner or scheduler.
- No arbitrary Python, shell, or user-defined workflow node execution.
- No replacement for composite `BaseTask` definitions.

## 4. Workflow definition contract

Manual Sequence v1 uses the existing workflow DAG document shape, but the stored
graph remains a constrained linear shape:

```json
{
  "schema_version": 1,
  "name": "Manual smoke sequence",
  "mode": "manual",
  "nodes": [
    {
      "id": "tasks",
      "type": "task_selection",
      "depends_on": [],
      "config": {
        "task_ids": ["task.alpha", "task.beta", "task.gamma"],
        "order_policy": "manual",
        "sample_n": 1
      }
    },
    {
      "id": "matrix",
      "type": "matrix",
      "depends_on": ["tasks"],
      "config": {
        "lanes": {
          "candidate": {
            "target_id": "target_id"
          }
        },
        "repeat_n": 1
      }
    },
    {
      "id": "execute",
      "type": "execute",
      "depends_on": ["matrix"],
      "config": {
        "execution_strategy": "linear_sequence",
        "state_policy": "isolated",
        "failure_policy": "continue",
        "parallel": 1,
        "processes": 1
      }
    }
  ]
}
```

The platform may keep `mode` optional for existing batch workflows. The
authoritative v1 signal for manual ordered execution is:

```json
{
  "execution_strategy": "linear_sequence"
}
```

## 5. Validation rules

A workflow using `execution_strategy = "linear_sequence"` is valid only when all
of the following are true:

- `task_selection.config.task_ids` is a non-empty explicit list.
- `task_selection.config.order_policy` is absent or equals `"manual"`.
- Suite, split, difficulty, or taxonomy filters are not used as the sequence
  source in v1.
- Exactly one matrix lane is present.
- The only lane role is effectively `candidate`.
- `matrix.config.repeat_n` is absent or equals `1`.
- `execute.config.parallel` is absent or equals `1`.
- `execute.config.processes` is absent or equals `1`.
- `execute.config.state_policy` is absent or equals `"isolated"`.
- `execute.config.failure_policy` is absent or equals `"continue"`.
- No `compare` node is present.
- No branching node semantics are present.

Invalid definitions must fail before run creation with structured workflow
issues. Create-run is still the authoritative gate for frozen target validity.

## 6. Ordering and reproducibility

In Manual Sequence v1, `task_ids` is an ordered list, not a set.

The compiler must preserve user order when generating `RunPlan.episodes`.
Therefore:

- `[A, B, C]` and `[C, B, A]` compile to different ordered episode lists.
- `[A, B, C]` and `[C, B, A]` produce different run fingerprints.
- compile preview reports the same count for both examples but preserves order
  in any ordered preview fields added for v1.

`RunPlan` and run detail APIs should expose sequence metadata per episode:

```json
{
  "sequence_index": 0,
  "sequence_group_id": "manual_sequence"
}
```

Existing batch workflows may expose `null` for these fields.

## 7. Execution semantics

Manual Sequence v1 executes ordered episodes through the single-lane serial
path. The executor must submit work items to the runner in ascending
`sequence_index`.

Each sequence item is still a normal episode:

- `task.setup()` runs for that episode.
- The agent acts within that episode.
- The judge evaluates that episode.
- `task.teardown()` runs for that episode.
- A distinct `episode_attempt` is ingested.
- Episode artifacts remain under that episode's artifact root.

The run finalizes only after all selected sequence episodes have produced an
attempt or have been reconciled as cancelled/error according to existing runner
rules.

## 8. State policy

Manual Sequence v1 fixes:

```json
{
  "state_policy": "isolated"
}
```

`isolated` means:

- task 2 does not inherit task 1's App state;
- task 2 does not inherit task 1's OS state;
- task 2 does not inherit task 1's AnswerSheet state;
- task 2 does not receive task 1's artifacts or judge output as inputs;
- ordering affects execution order and observability only.

When a workflow needs real business state handoff in v1, implement it as a
composite `BaseTask` instead of a manual sequence. Existing cross-app task
definitions remain the preferred short-term model for complex user journeys.

## 9. Failure policy

Manual Sequence v1 fixes:

```json
{
  "failure_policy": "continue"
}
```

`continue` means:

- a `FAIL` or `ERROR` episode does not prevent later sequence episodes from
  running;
- the run-level terminal state can still become `completed` after all episodes
  finish;
- quality gate and report logic decide whether the completed run is acceptable;
- retry and resume operate on the failed, errored, missing, or restarted
  episodes selected by the existing follow-up rules.

## 10. Console behavior

The console should expose two authoring modes:

- Batch: the current task-set workflow behavior.
- Manual sequence: explicit ordered task list.

Manual sequence v1 UI must:

- let users add tasks to an ordered sequence;
- let users remove tasks;
- let users move tasks up or down;
- disable paired comparison;
- force `parallel = 1`;
- force `processes = 1`;
- force `repeat_n = 1`;
- show `state_policy = isolated` as read-only;
- show `failure_policy = continue` as read-only or implicit;
- make clear that each task starts from its own setup state.

## 11. Run detail behavior

Run detail should continue to treat each item as an episode while adding ordered
sequence presentation:

- episode picker order follows `sequence_index`;
- labels include the sequence position, for example `Step 1: task.alpha`;
- replay and Evidence Dock still inspect one episode attempt at a time;
- report summaries can group the ordered episodes under the manual sequence.

## 12. v2 carry-forward reservation

`state_policy = "carry_forward"` is reserved for a later design. It must not be
accepted in Manual Sequence v1.

Before carry-forward is implemented, the platform needs a separate contract for:

- task typed inputs and outputs;
- context handoff artifacts;
- final snapshot capture after each episode;
- restoring the previous successful snapshot before downstream execution;
- per-task judge versus sequence-level judge;
- retry and resume from the middle of a sequence;
- UI and API presentation of context handoff.

## 13. Acceptance checks for M01-M12

- Validator rejects unsupported manual sequence configurations before run
  creation.
- Compile preview preserves task order and reports the expected total episode
  count.
- RunPlan episodes include stable sequence metadata.
- Run fingerprint changes when task order changes.
- Single-lane serial execution runs three ordered tasks and ingests three
  episode attempts.
- A failing first task does not prevent later tasks from running.
- A state mutation from one task is not visible to the next task under
  `state_policy = isolated`.
- Retry and resume preserve sequence metadata.
- Console sequence editing changes the compiled task order.
- Run Detail displays sequence order while preserving per-episode replay and
  evidence inspection.
