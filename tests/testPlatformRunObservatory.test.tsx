import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import { chooseDefaultReplayOption } from '../web/test-platform/features/runs/episodeReplay';

const project = {
  id: 'project-1',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-03T00:00:00.000Z',
  updated_at: '2026-07-03T00:00:00.000Z',
};

const run = {
  id: 'run-observatory',
  project_id: project.id,
  workflow_version_id: 'version-1',
  name: 'Run observatory',
  state: 'running',
  fingerprint: 'sha256:observatory',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 2,
    completed_episodes: 1,
    completed_lane_episodes: 1,
  },
  lanes: [
    {
      id: 'lane-1',
      lane_key: 'candidate',
      role: 'candidate',
      target_id: 'target-1',
      target_revision_id: 'revision-1',
    },
  ],
  target_revisions: [
    {
      target_id: 'target-1',
      target_revision_id: 'revision-1',
      metadata_hash: 'metadata-vs15',
    },
  ],
  episode_identities: [
    {
      episode_key: 'fake.Pass|i0|s1|r1|t0',
      pair_key: 'fake.Pass|i0|s1|r1|t0',
      task_base_id: 'fake.Pass',
      task_id: 'fake.Pass',
      instance_id: 0,
      instance_seed: 1,
      template_index: null,
      trial_id: 0,
      max_steps: 15,
      sequence_index: null,
      sequence_group_id: null,
    },
    {
      episode_key: 'fake.Fail|i0|s1|r1|t0',
      pair_key: 'fake.Fail|i0|s1|r1|t0',
      task_base_id: 'fake.Fail',
      task_id: 'fake.Fail',
      instance_id: 0,
      instance_seed: 1,
      template_index: null,
      trial_id: 0,
      max_steps: 15,
      sequence_index: null,
      sequence_group_id: null,
    },
  ],
  episode_attempts: [
    {
      episode_key: 'fake.Pass|i0|s1|r1|t0',
      lane_key: 'candidate',
      attempt_no: 1,
      state: 'completed',
      outcome: 'PASS',
      error_code: null,
      artifact_root: 'lanes/candidate/trajectory/fake_Pass',
    },
    {
      episode_key: 'fake.Fail|i0|s1|r1|t0',
      lane_key: 'candidate',
      attempt_no: 1,
      state: 'completed',
      outcome: 'FAIL',
      error_code: 'ASSERTION_FAILURE',
      artifact_root: 'lanes/candidate/trajectory/fake_Fail',
    },
  ],
  lane_attempts: [],
  run_attempts: [],
  run_plan: {},
  gate_verdict: null,
  created_at: '2026-07-03T00:00:03.000Z',
  started_at: '2026-07-03T00:00:04.000Z',
  ended_at: null,
};

const liveRun = {
  ...run,
  state: 'running',
  episode_attempts: [],
  run_plan: {
    execution: {
      model: 'dogfood-model',
      api_key: 'sk-live-secret',
      nested: { bearer_token: 'token-secret' },
    },
  },
};

const completedRetryRun = {
  ...run,
  state: 'completed',
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 1,
    completed_episodes: 1,
    completed_lane_episodes: 2,
  },
  episode_identities: [run.episode_identities[1]],
  episode_attempts: [
    {
      episode_key: 'fake.Fail|i0|s1|r1|t0',
      lane_key: 'candidate',
      attempt_no: 1,
      episode_attempt_no: 1,
      state: 'completed',
      outcome: 'FAIL',
      error_code: 'ASSERTION_FAILURE',
      artifact_root: 'lanes/candidate/trajectory/fake_Fail',
    },
    {
      episode_key: 'fake.Fail|i0|s1|r1|t0',
      lane_key: 'candidate',
      attempt_no: 2,
      episode_attempt_no: 1,
      state: 'completed',
      outcome: 'ERROR',
      error_code: 'EXECUTION_ERROR',
      artifact_root: 'lanes/candidate/attempts/0002/trajectory/fake_Fail',
    },
  ],
};

class FakeEventSource {
  static lastInstance: FakeEventSource | null = null;
  listeners: Record<string, Set<(ev: MessageEvent) => void>> = {};
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.lastInstance = this;
  }

  addEventListener(type: string, listener: (ev: MessageEvent) => void) {
    (this.listeners[type] ??= new Set()).add(listener);
  }

  removeEventListener(type: string, listener: (ev: MessageEvent) => void) {
    this.listeners[type]?.delete(listener);
  }

  close() {
    this.listeners = {};
  }

  dispatch(type: string, data: unknown) {
    const ev = { data: JSON.stringify(data) } as MessageEvent;
    this.listeners[type]?.forEach((listener) => listener(ev));
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestUrl(input: RequestInfo | URL): URL {
  if (typeof input === 'string') return new URL(input, window.location.origin);
  if (input instanceof URL) return input;
  return new URL(input.url);
}

function replayBody(episodeKey: string) {
  const isFail = episodeKey.startsWith('fake.Fail');
  return {
    run_id: run.id,
    episode_key: episodeKey,
    lane_key: 'candidate',
    attempt_no: 1,
    episode_attempt_id: isFail ? 'attempt-fail' : 'attempt-pass',
    artifact_root: isFail
      ? 'lanes/candidate/trajectory/fake_Fail'
      : 'lanes/candidate/trajectory/fake_Pass',
    outcome: isFail ? 'FAIL' : 'PASS',
    error_code: isFail ? 'ASSERTION_FAILURE' : null,
    result: {
      is_success: !isFail,
      execution: { stop_reason: isFail ? 'MAX_STEPS' : 'COMPLETE' },
      ...(isFail ? {} : { answer_completion_accepted: true }),
    },
    steps: [
      {
        step: 1,
        route: { app: 'demo', path: '/' },
        action_type: 'CLICK',
        action_data: { point: [0.5, 0.5] },
        thought: 'open the relevant page',
        explain: '',
        summary: '',
        screenshot_artifact_id: isFail ? 'fail-raw-1' : 'pass-raw-1',
        screenshot_annotated_artifact_id: isFail ? 'fail-annot-1' : 'pass-annot-1',
        model_response_artifact_id: isFail ? 'fail-response-1' : 'pass-response-1',
        model_prompt_artifact_id: isFail ? 'fail-prompt-1' : 'pass-prompt-1',
      },
      {
        step: 2,
        route: { app: 'demo', path: '/answer' },
        action_type: isFail ? 'ANSWER' : 'COMPLETE',
        action_data: isFail ? { value: '42' } : { return: 'done' },
        thought: isFail ? 'submit the final answer' : 'finish task',
        explain: '',
        summary: '',
        screenshot_artifact_id: isFail ? 'fail-raw-2' : 'pass-raw-2',
        screenshot_annotated_artifact_id: isFail ? 'fail-annot-2' : 'pass-annot-2',
        model_response_artifact_id: isFail ? 'fail-response-2' : 'pass-response-2',
        model_prompt_artifact_id: isFail ? 'fail-prompt-2' : 'pass-prompt-2',
      },
    ],
  };
}

describe('Run Observatory', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/runs/run-observatory');
    window.localStorage.clear();
    FakeEventSource.lastInstance = null;
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('selects the failed episode by default and renders the replay screenshot', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      const decodedPath = decodeURIComponent(url.pathname);

      if (url.pathname === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'SQLite database is ready.' },
            migrations: { ready: true, message: 'All migrations applied.' },
            runs_dir: { ready: true, message: 'Runs directory is writable.' },
          },
        });
      }
      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs/run-observatory') {
        return jsonResponse(run);
      }
      if (decodedPath.includes('/episodes/fake.Fail|i0|s1|r1|t0/replay')) {
        expect(url.searchParams.get('lane_key')).toBe('candidate');
        expect(url.searchParams.get('attempt_no')).toBe('1');
        return jsonResponse(replayBody('fake.Fail|i0|s1|r1|t0'));
      }
      if (decodedPath.includes('/episodes/fake.Pass|i0|s1|r1|t0/replay')) {
        return jsonResponse(replayBody('fake.Pass|i0|s1|r1|t0'));
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByTestId('tp-run-observatory')).toBeTruthy();
    const picker = await screen.findByLabelText('Replay episode') as HTMLSelectElement;
    expect(picker.selectedOptions[0]?.textContent).toContain('fake.Fail');

    const screenshot = await screen.findByTestId('tp-replay-screenshot') as HTMLImageElement;
    expect(screenshot.getAttribute('src')).toContain('/artifacts/fail-annot-2/content');
    expect(screen.getByText('Step 2: ANSWER')).toBeTruthy();
    expect(within(screen.getByTestId('tp-phone-stage')).getByText('submit the final answer'))
      .toBeTruthy();
    expect(screen.getByRole('button', { name: /Step 2 ANSWER/ }).getAttribute('aria-current'))
      .toBe('step');
    expect(screen.getByTestId('tp-judge-result-json').textContent).toContain('MAX_STEPS');

    fireEvent.click(screen.getByRole('button', { name: /Step 1 CLICK/ }));
    expect(screen.getByRole('button', { name: /Step 1 CLICK/ }).getAttribute('aria-current'))
      .toBe('step');
    expect(await screen.findByText('Step 1: CLICK')).toBeTruthy();
    expect(screenshot.getAttribute('src')).toContain('/artifacts/fail-annot-1/content');

    fireEvent.click(screen.getByRole('tab', { name: 'Prompt' }));
    expect(screen.getByRole('link', { name: 'Open prompt artifact' }).getAttribute('href'))
      .toContain('/artifacts/fail-prompt-1/content');

    fireEvent.click(screen.getByRole('tab', { name: 'Response' }));
    expect(screen.getByRole('link', { name: 'Open response artifact' }).getAttribute('href'))
      .toContain('/artifacts/fail-response-1/content');

    fireEvent.change(screen.getByLabelText('Replay screenshot mode'), {
      target: { value: 'raw' },
    });
    expect(screenshot.getAttribute('src')).toContain('/artifacts/fail-raw-1/content');

    const passOption = Array.from(picker.options).find((option) =>
      option.textContent?.includes('fake.Pass'),
    );
    expect(passOption).toBeTruthy();
    fireEvent.change(picker, { target: { value: passOption?.value } });

    await waitFor(() => {
      expect(screen.getByText('Step 2: COMPLETE')).toBeTruthy();
    });
    expect((screen.getByTestId('tp-replay-screenshot') as HTMLImageElement).getAttribute('src'))
      .toContain('/artifacts/pass-raw-2/content');
    fireEvent.click(screen.getByRole('tab', { name: 'Judge' }));
    expect(screen.getByTestId('tp-answer-completion-badge').textContent)
      .toBe('answer_completion_accepted');
  });

  it('follows the active live episode and shows waiting state before replay artifacts exist', async () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      const decodedPath = decodeURIComponent(url.pathname);

      if (url.pathname === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'SQLite database is ready.' },
            migrations: { ready: true, message: 'All migrations applied.' },
            runs_dir: { ready: true, message: 'Runs directory is writable.' },
          },
        });
      }
      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs/run-observatory') {
        return jsonResponse(liveRun);
      }
      if (decodedPath.includes('/episodes/fake.Pass|i0|s1|r1|t0/replay')) {
        return jsonResponse({
          error: {
            code: 'REPLAY_ARTIFACT_MISSING',
            message: 'Replay artifacts are not available yet.',
            details: [],
          },
        }, 404);
      }
      if (decodedPath.includes('/episodes/fake.Fail|i0|s1|r1|t0/replay')) {
        return jsonResponse({
          error: {
            code: 'REPLAY_ARTIFACT_MISSING',
            message: 'Replay artifacts are not available yet.',
            details: [],
          },
        }, 404);
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByTestId('tp-run-observatory')).toBeTruthy();
    await waitFor(() => {
      expect(FakeEventSource.lastInstance).not.toBeNull();
    });

    FakeEventSource.lastInstance!.dispatch('episode.started', {
      id: 'evt-1',
      run_id: liveRun.id,
      sequence: 1,
      type: 'episode.started',
      occurred_at: '2026-07-03T00:00:05.000Z',
      payload: {
        episode_key: 'fake.Pass|i0|s1|r1|t0',
        task_id: 'fake.Pass',
        max_steps: 15,
      },
      payload_version: 1,
      run_attempt_id: null,
      lane_id: null,
      lane_attempt_id: null,
      episode_id: null,
      episode_attempt_id: null,
      worker_id: 'W0',
    });
    FakeEventSource.lastInstance!.dispatch('episode.step_recorded', {
      id: 'evt-2',
      run_id: liveRun.id,
      sequence: 2,
      type: 'episode.step_recorded',
      occurred_at: '2026-07-03T00:00:06.000Z',
      payload: {
        episode_key: 'fake.Pass|i0|s1|r1|t0',
        step: 2,
        action_type: 'CLICK',
      },
      payload_version: 1,
      run_attempt_id: null,
      lane_id: null,
      lane_attempt_id: null,
      episode_id: null,
      episode_attempt_id: null,
      worker_id: 'W0',
    });

    await waitFor(() => {
      const picker = screen.getByLabelText('Replay episode') as HTMLSelectElement;
      expect(picker.selectedOptions[0]?.textContent).toContain('fake.Pass');
    });
    expect(screen.getByText('Live step 2')).toBeTruthy();
    expect(screen.getByText('Live run is recording step 2; screenshots will appear after replay artifacts are available.'))
      .toBeTruthy();
    expect(screen.getByText('live 2 / 15')).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Diagnostics' }));
    expect(screen.getByText('Diagnostics are available after the run reaches a reportable state.'))
      .toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const settingsJson = screen.getByTestId('tp-run-settings-json').textContent ?? '';
    expect(settingsJson).toContain('[redacted]');
    expect(settingsJson).not.toContain('sk-live-secret');
    expect(settingsJson).not.toContain('token-secret');
  });

  it('defaults to the newest attempt when debug outcomes have the same priority', () => {
    const selected = chooseDefaultReplayOption([
      {
        id: 'old-fail',
        episodeKey: 'fake.Task|old',
        laneKey: 'candidate',
        attemptNo: 1,
        taskId: 'fake.Task',
        outcome: 'FAIL',
        errorCode: 'ASSERTION_FAILURE',
        artifactRoot: 'old',
      },
      {
        id: 'new-error',
        episodeKey: 'fake.Task|new',
        laneKey: 'candidate',
        attemptNo: 2,
        taskId: 'fake.Task',
        outcome: 'ERROR',
        errorCode: 'EXECUTION_ERROR',
        artifactRoot: 'new',
      },
    ]);

    expect(selected?.id).toBe('new-error');
    expect(chooseDefaultReplayOption([
      {
        id: 'old-fail',
        episodeKey: 'fake.Task|old',
        laneKey: 'candidate',
        attemptNo: 1,
        taskId: 'fake.Task',
        outcome: 'FAIL',
        errorCode: 'ASSERTION_FAILURE',
        artifactRoot: 'old',
      },
      {
        id: 'new-pass',
        episodeKey: 'fake.Task|new',
        laneKey: 'candidate',
        attemptNo: 2,
        taskId: 'fake.Task',
        outcome: 'PASS',
        errorCode: null,
        artifactRoot: 'new',
      },
    ])?.id).toBe('old-fail');
  });

  it('does not let completed-run event backlog override the default replay attempt', async () => {
    vi.stubGlobal('EventSource', FakeEventSource);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      const decodedPath = decodeURIComponent(url.pathname);

      if (url.pathname === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'SQLite database is ready.' },
            migrations: { ready: true, message: 'All migrations applied.' },
            runs_dir: { ready: true, message: 'Runs directory is writable.' },
          },
        });
      }
      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs/run-observatory') {
        return jsonResponse(completedRetryRun);
      }
      if (url.pathname.endsWith('/report')) {
        return jsonResponse({ error: { code: 'NO_REPORT', message: 'No report.', details: [] } }, 404);
      }
      if (url.pathname.endsWith('/diagnostics')) {
        return jsonResponse({
          summary: { total: 0, by_severity: {}, by_category: {} },
          items: [],
        });
      }
      if (url.pathname.endsWith('/artifacts')) {
        return jsonResponse({ items: [] });
      }
      if (decodedPath.includes('/episodes/fake.Fail|i0|s1|r1|t0/replay')) {
        expect(url.searchParams.get('attempt_no')).toBe('2');
        return jsonResponse({
          ...replayBody('fake.Fail|i0|s1|r1|t0'),
          attempt_no: 2,
          outcome: 'ERROR',
          error_code: 'EXECUTION_ERROR',
          steps: [],
        });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    const picker = await screen.findByLabelText('Replay episode') as HTMLSelectElement;
    await waitFor(() => {
      expect(picker.selectedOptions[0]?.textContent).toContain('attempt 2');
    });

    FakeEventSource.lastInstance!.dispatch('episode.started', {
      id: 'evt-completed-1',
      run_id: completedRetryRun.id,
      sequence: 1,
      type: 'episode.started',
      occurred_at: '2026-07-03T00:00:05.000Z',
      payload: {
        episode_key: 'fake.Fail|i0|s1|r1|t0',
        task_id: 'fake.Fail',
        max_steps: 15,
      },
      payload_version: 1,
      run_attempt_id: null,
      lane_id: null,
      lane_attempt_id: null,
      episode_id: null,
      episode_attempt_id: null,
      worker_id: 'W0',
    });

    await waitFor(() => {
      expect(picker.selectedOptions[0]?.textContent).toContain('attempt 2');
    });
  });
});
