import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import type { RunDetail } from '../web/test-platform/api/types';

const project = {
  id: 'project-1',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-06T00:00:00.000Z',
  updated_at: '2026-07-06T00:00:00.000Z',
};

const run: RunDetail = {
  id: 'run-vs13',
  project_id: project.id,
  workflow_version_id: 'version-1',
  name: 'VS-13 retry resume',
  state: 'failed',
  fingerprint: 'sha256:vs13',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 2,
    completed_episodes: 1,
    completed_lane_episodes: 1,
  },
  outcome_counts: { pass: 0, fail: 1, error: 0, cancelled: 0, incomplete: 1 },
  lanes: [
    {
      id: 'lane-1',
      lane_key: 'candidate',
      role: 'candidate',
      target_id: 'target-1',
      target_revision_id: 'revision-1',
    },
  ],
  run_attempts: [
    {
      id: 'attempt-1',
      attempt_no: 1,
      reason: 'initial',
      state: 'failed',
      started_at: '2026-07-06T00:00:01.000Z',
      ended_at: '2026-07-06T00:00:02.000Z',
      created_at: '2026-07-06T00:00:00.000Z',
    },
    {
      id: 'attempt-2',
      attempt_no: 2,
      reason: 'retry',
      state: 'failed',
      started_at: '2026-07-06T00:01:01.000Z',
      ended_at: '2026-07-06T00:01:02.000Z',
      created_at: '2026-07-06T00:01:00.000Z',
    },
  ],
  lane_attempts: [
    {
      id: 'lane-attempt-1',
      lane_id: 'lane-1',
      lane_key: 'candidate',
      run_attempt_id: 'attempt-1',
      attempt_no: 1,
      reason: 'initial',
      state: 'failed',
      artifact_root: 'lanes/candidate/attempts/0001',
      started_at: '2026-07-06T00:00:01.000Z',
      ended_at: '2026-07-06T00:00:02.000Z',
    },
    {
      id: 'lane-attempt-2',
      lane_id: 'lane-1',
      lane_key: 'candidate',
      run_attempt_id: 'attempt-2',
      attempt_no: 2,
      reason: 'retry',
      state: 'failed',
      artifact_root: 'lanes/candidate/attempts/0002',
      started_at: '2026-07-06T00:01:01.000Z',
      ended_at: '2026-07-06T00:01:02.000Z',
    },
  ],
  target_revisions: [
    { target_id: 'target-1', target_revision_id: 'revision-1', metadata_hash: 'metadata-1' },
  ],
  episode_identities: [],
  episode_attempts: [],
  run_plan: {},
  gate_verdict: null,
  created_at: '2026-07-06T00:00:00.000Z',
  started_at: '2026-07-06T00:00:01.000Z',
  ended_at: '2026-07-06T00:01:02.000Z',
};

const runWithOnlineModelKey: RunDetail = {
  ...run,
  run_plan: {
    lanes: [
      {
        lane_key: 'candidate',
        runner_config: {
          agent: 'autoglm',
          model_name: 'glm-5v-turbo',
          model_base_url: 'https://open.bigmodel.cn/api/paas/v4',
          image_url_format: 'bare_base64',
          model_api_key_configured: true,
        },
      },
    ],
  },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') return new URL(input, window.location.origin).pathname;
  if (input instanceof URL) return input.pathname;
  return new URL(input.url).pathname;
}

describe('Test Platform retry/resume controls', () => {
  beforeEach(() => {
    window.history.pushState({}, '', `/test-platform/runs/${run.id}`);
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows attempt history, retries, and surfaces resume incompatibility details', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'ok' },
            migrations: { ready: true, message: 'ok' },
            runs_dir: { ready: true, message: 'ok' },
          },
        });
      }
      if (path === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }
      if (path === `/api/platform/v1/runs/${run.id}/retry`) {
        return jsonResponse({
          run_id: run.id,
          run_attempt_id: 'attempt-3',
          attempt_no: 3,
          reason: 'retry',
          selected_lane_episodes: [
            { episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' },
          ],
        }, 202);
      }
      if (path === `/api/platform/v1/runs/${run.id}/resume`) {
        return jsonResponse({
          error: {
            code: 'RUN_RESUME_INCOMPATIBLE_REVISION',
            message: 'The run cannot be resumed because its frozen target revisions are no longer current.',
            details: [
              {
                kind: 'target_revision',
                lane_key: 'candidate',
                expected_revision_id: 'revision-1',
                current_revision_id: 'revision-2',
              },
            ],
          },
        }, 409);
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Attempt history' })).toBeTruthy();
    expect(screen.getAllByText('initial').length).toBeGreaterThan(0);
    expect(screen.getAllByText('retry').length).toBeGreaterThan(0);
    expect(screen.getByText('lanes/candidate/attempts/0002')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => requestPath(input as RequestInfo | URL) === `/api/platform/v1/runs/${run.id}/retry`)).toBe(true);
    });

    fireEvent.click(screen.getByRole('button', { name: 'Resume run' }));
    const message = await screen.findByTestId('tp-followup-message');
    expect(message.textContent).toContain('RUN_RESUME_INCOMPATIBLE_REVISION');
    expect(message.textContent).toContain('revision-1');
    expect(message.textContent).toContain('revision-2');
  });

  it('requires a follow-up model API key and sends it only in the retry body', async () => {
    let retryBody: unknown = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'ok' },
            migrations: { ready: true, message: 'ok' },
            runs_dir: { ready: true, message: 'ok' },
          },
        });
      }
      if (path === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(runWithOnlineModelKey);
      }
      if (path === `/api/platform/v1/runs/${run.id}/retry`) {
        retryBody = JSON.parse(String(init?.body ?? '{}'));
        return jsonResponse({
          run_id: run.id,
          run_attempt_id: 'attempt-3',
          attempt_no: 3,
          reason: 'retry',
          selected_lane_episodes: [
            { episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' },
          ],
        }, 202);
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByLabelText('Model API key')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }));
    expect((await screen.findByTestId('tp-followup-message')).textContent).toContain(
      'Model API key is required',
    );
    expect(
      fetchMock.mock.calls.some(
        ([input]) => requestPath(input as RequestInfo | URL) === `/api/platform/v1/runs/${run.id}/retry`,
      ),
    ).toBe(false);

    fireEvent.change(screen.getByLabelText('Model API key'), {
      target: { value: 'sk-retry-secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }));

    await waitFor(() => {
      expect(retryBody).toEqual({ execution: { model_api_key: 'sk-retry-secret' } });
    });
    expect(JSON.stringify(retryBody)).not.toContain('model_base_url');
  });
});
