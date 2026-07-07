# VS-15 Run Observatory Design

## Context

The current Test Platform MVP can launch real workflow runs, stream lifecycle
events, persist attempts, build reports, classify diagnostics, and browse raw
artifacts. The run detail page is operationally complete, but it still reads
like a report table. It does not yet feel like an agent observatory where an
operator can watch, replay, and debug what the model saw and did.

The public MobileGym demo uses a strong interaction pattern for this problem:
a simulator-first stage, floating tool docks, a compact agent console, and a
settings drawer. VS-15 adapts that pattern to the Test Platform run detail page.
Unlike the demo, this page is primarily a workflow run observer and replay
surface, not a free-form public demo launcher.

## Goals

- Make the run detail page simulator-first: the primary viewport should show
  the active or replayed episode, not tables.
- Let an operator answer the first debugging question in seconds: what did the
  agent see, think, do, and why did the episode pass or fail?
- Support both live runs and completed run replay from the same surface.
- Preserve existing immutable run/attempt semantics. Replay must never mutate
  historical results.
- Reuse the existing event, diagnostics, report, and artifact systems where
  possible.
- Keep the first slice narrow enough to land safely after the MVP.

## Non-Goals

- Do not rebuild the public demo page.
- Do not add manual phone control to historical run replay.
- Do not implement multi-simulator paired comparison in the first slice.
- Do not change executor behavior or task judging.
- Do not require raw filesystem paths in the frontend.

## Borrowed Patterns From The Public Demo

- **Central phone stage**: one large, visually dominant simulator or screenshot
  frame.
- **Floating docks**: compact vertical controls around the stage instead of
  wide tables competing for attention.
- **Agent console**: a small anchored panel for task, model, current step,
  status, and controls.
- **Settings drawer**: model/agent/run configuration belongs in a drawer, not
  in the main scan path.
- **State/data dock concept**: adapted as read-only run evidence tools such as
  artifacts, diagnostics, answer sheet, prompts, and state diffs.

## Information Architecture

The run detail page becomes a two-mode page:

1. **Observatory mode**: default first viewport for all runs.
2. **Details mode**: existing tables and reports, moved behind tabs or lower
   page sections.

Top-level layout:

```text
Run header
  Back, run name/id, state badge, verdict badge, attempt selector, retry/resume/cancel

Observatory stage
  Left dock: episode/lane selector, diagnostics, artifacts, answer sheet
  Center: phone replay frame
  Right dock: step list, prompt/response, state diff, performance
  Bottom/right floating console: task, agent/model, current step, playback controls

Details tabs
  Report | Diagnostics | Attempts | Comparison | Artifacts | Events
```

The first implementation can keep existing details panels below the new
observatory stage. Moving them into tabs can be a follow-up if needed.

## Primary User Flows

### Live Run

1. Operator launches a run and lands on run detail.
2. The stage shows the selected active episode.
3. SSE updates the progress strip and step timeline.
4. When a step artifact lands, the stage advances to the newest available
   screenshot.
5. Terminal event updates the verdict badge and pins the last step.

### Completed Run Replay

1. Operator opens a completed run.
2. The page selects the most relevant episode by default:
   - first FAIL/ERROR episode,
   - otherwise first PASS episode,
   - otherwise first planned episode.
3. The stage loads `trajectory.json` plus screenshot artifacts.
4. Operator scrubs steps, toggles annotated/raw screenshot, and opens prompt or
   response evidence for the selected step.

### Debug Failed Episode

1. Operator clicks a diagnostic or failed episode.
2. Stage jumps to that episode attempt.
3. Step timeline highlights terminal reason such as `MAX_STEPS`,
   `ASSERTION_FAILURE`, or `answer_completion_accepted`.
4. Evidence drawer shows judge checks, answer sheet values, and recommended
   action.

### Retry With Modified Runtime Config

1. Operator opens run settings drawer.
2. Drawer shows frozen original execution config, redacting secrets.
3. Retry action copies original config and allows changing model endpoint,
   model id, API key, image format, and step budget if supported.
4. New attempt appears in attempt selector; old artifacts remain immutable.

## Data Model

### Existing Data To Reuse

- `RunDetail`
  - run state, lanes, target revisions, episode identities, attempts.
- `RunEvent`
  - live lifecycle, step progress, workers, shards, artifacts.
- `RunDiagnostics`
  - diagnostic items with artifact references and recommended actions.
- `RunReport`
  - functional, performance, gate, comparison summary.
- `ArtifactItem`
  - artifact id, relative path, kind, media type, size.
- Artifact content endpoint
  - `GET /api/platform/v1/runs/{run_id}/artifacts/{artifact_id}/content`.

### New DTO: Episode Replay

Add a backend aggregation endpoint:

```http
GET /api/platform/v1/runs/{run_id}/episodes/{episode_key}/replay?lane_key=candidate&attempt_no=latest
```

Response:

```ts
type EpisodeReplay = {
  run_id: string;
  episode_key: string;
  lane_key: string;
  attempt_no: number;
  episode_attempt_id: string;
  artifact_root: string;
  outcome: string | null;
  error_code: string | null;
  result: Record<string, unknown> | null;
  steps: EpisodeReplayStep[];
  artifacts: {
    trajectory_artifact_id: string | null;
    root_artifact_ids: string[];
  };
};

type EpisodeReplayStep = {
  step: number;
  route: Record<string, unknown>;
  action_type: string;
  action_data: Record<string, unknown>;
  thought: string;
  explain: string;
  summary: string;
  screenshot_artifact_id: string | null;
  screenshot_annotated_artifact_id: string | null;
  model_response_artifact_id: string | null;
  model_prompt_artifact_id: string | null;
};
```

Why backend aggregation:

- The recorder already writes `trajectory.json`, screenshots, annotated
  screenshots, model responses, and prompts.
- The frontend should not infer filesystem layout or join artifacts by string
  prefix.
- The backend can enforce artifact path safety and produce stable artifact ids.

### Optional Live Step Evidence

The first slice can show live step count from SSE and load artifacts after they
are registered. A later slice can add an event payload field with artifact ids
for each `episode.step_recorded` event, but that is not required for VS-15.

## Component Design

### `RunDetailPage`

Keep ownership of run loading, SSE subscription, report, diagnostics, and
follow-up actions. Delegate the observatory surface to child components.

New structure:

```tsx
<RunHeader />
<RunObservatory
  run={run}
  live={liveRef.current}
  diagnostics={diagnostics}
  report={report}
/>
<RunDetailsSections />
```

### `RunObservatory`

State owned here:

- selected lane key
- selected episode key
- selected attempt number
- selected step index
- screenshot mode: raw or annotated
- active evidence drawer tab

Responsibilities:

- choose default episode/attempt
- fetch `EpisodeReplay`
- reconcile live state with replay state
- coordinate stage, docks, and console

### `PhoneReplayStage`

Displays one screenshot frame with phone-like chrome.

States:

- loading replay
- no screenshot yet
- screenshot loaded
- artifact missing
- live run waiting for first step

Controls:

- raw/annotated toggle
- fit/actual-size toggle
- open artifact link

### `StepTimeline`

Vertical list or compact rail of steps.

Each step shows:

- step number
- action type
- concise action data
- duration when available later
- warning/error marker if related diagnostic exists
- selected state

Terminal markers:

- `COMPLETE`
- `MAX_STEPS`
- `REPETITIVE_LOOP`
- `CANCELLED`
- `ERROR`
- `answer_completion_accepted`

### `AgentConsole`

Compact panel anchored near the stage.

Shows:

- run state and SSE connection state
- selected task instruction
- current step
- action type and action data
- thought summary
- agent/model configuration summary
- playback controls

Playback controls:

- previous step
- next step
- play/pause
- step scrubber
- jump to terminal step

### `EvidenceDock`

Tabbed drawer/dock.

Tabs:

- Judge
- Answer sheet
- Prompt
- Response
- State/route
- Diagnostics
- Artifacts

The first version can render JSON with light formatting. Follow-up work can add
structured state diffs and prompt redaction views.

### `EpisodePicker`

Selector for:

- lane
- episode
- attempt

Default ordering:

- failed/error attempts first
- then cancelled
- then pass
- then pending/planned

Search should match task id, episode key, outcome, and error code.

## Visual Design Notes

- This is an operational tool, so keep it dense and scan-friendly.
- Use a restrained light UI like the demo started state, not the dark landing
  hero.
- The simulator frame should be visually central, but the rest of the page
  should remain a dashboard, not marketing.
- Docks should be compact icon + tooltip controls, with expanded drawers for
  rich content.
- Do not nest cards inside cards. Use a stage band with docks and separate
  detail sections.
- On mobile or narrow windows, collapse docks into bottom tabs and keep the
  screenshot visible above the fold.

## Backend Changes

### Replay Endpoint

Add route:

```http
GET /runs/{run_id}/episodes/{episode_key}/replay
```

Query params:

- `lane_key`: optional; defaults to candidate or first lane.
- `attempt_no`: optional; `latest` default.

Repository responsibilities:

- validate run exists
- find matching `episode_attempts` row
- load `trajectory.json` under `artifact_root`
- map relative step paths to artifact ids
- return result_json and basic attempt metadata

Failure codes:

- `RUN_NOT_FOUND`
- `EPISODE_NOT_FOUND`
- `EPISODE_ATTEMPT_NOT_FOUND`
- `REPLAY_ARTIFACT_MISSING`
- `REPLAY_ARTIFACT_INVALID`

### Artifact Repository Helper

Add lookup helper:

```py
find_by_relative_path(run_id: str, relative_path: str) -> dict | None
```

or build an in-memory relative-path map from `list_for_run()`.

## Frontend Changes

Add API client functions:

```ts
getEpisodeReplay(input: {
  runId: string;
  episodeKey: string;
  laneKey?: string;
  attemptNo?: number | 'latest';
}): Promise<EpisodeReplay>
```

Add files:

- `web/test-platform/features/runs/RunObservatory.tsx`
- `web/test-platform/features/runs/PhoneReplayStage.tsx`
- `web/test-platform/features/runs/StepTimeline.tsx`
- `web/test-platform/features/runs/AgentConsole.tsx`
- `web/test-platform/features/runs/EvidenceDock.tsx`
- `web/test-platform/features/runs/episodeReplay.ts`

Keep `RunDetailPage.tsx` as the data owner during the first implementation to
avoid a broad routing refactor.

## Testing Plan

### Backend

- replay endpoint returns steps from a real `trajectory.json`
- maps screenshot/model prompt/model response paths to artifact ids
- rejects missing run
- rejects missing episode
- handles missing trajectory artifact with a stable error
- path traversal remains impossible

### Frontend Unit / Component

- default episode selection prefers FAIL/ERROR
- step timeline selects and highlights steps
- screenshot mode chooses annotated image when available
- evidence dock opens prompt/response artifact links
- `answer_completion_accepted` renders a success compatibility badge
- live SSE step events update active step count without breaking replay

### Integration / Smoke

- launch a one-episode workflow
- open run detail while running
- observe step count growth
- after completion, scrub screenshots
- open model response artifact
- retry failed episode and verify attempt selector includes the new attempt

## Rollout Slices

### VS-15A: Replay DTO

- Add backend `EpisodeReplay` endpoint.
- Add tests for trajectory/artifact mapping.
- Add frontend API types/client.

### VS-15B: Simulator-First Run Detail

- Add observatory stage above existing panels.
- Add episode picker and phone replay stage.
- Load and display screenshots from replay DTO.

### VS-15C: Step Timeline And Evidence Dock

- Add step list, playback controls, prompt/response links, judge evidence.
- Add `answer_completion_accepted` badge.

### VS-15D: Live Polish

- Integrate SSE progress with selected active episode.
- Show live waiting states.
- Add compact agent console and settings drawer.

## Acceptance Criteria

- A completed run with trajectory artifacts can be replayed step by step from
  the run detail page.
- A failed/error episode is selected by default.
- The operator can see screenshot, action, thought, prompt, response, judge
  result, and diagnostics without opening Run Explorer manually.
- Existing report/diagnostics/attempt/comparison content remains reachable.
- Live runs still stream progress and can be cancelled.
- Retry/resume flows still work and do not mutate old attempts.
- The `alipay.CheckBalance` pattern is understandable from the UI: correct
  answer submitted, max-step termination preserved, compatibility pass badge
  visible when applicable.

## Open Questions

- Should paired runs start with lane tabs or side-by-side replay?
- Should replay preload all screenshots or lazy-load per selected step?
- Should model prompts be redacted further before display, beyond existing
  image stripping?
- Should step playback use wall-clock timing from stopwatch data when
  available, or a fixed interval?
