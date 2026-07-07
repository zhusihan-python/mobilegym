import React from 'react';
import { cleanup, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

const project = {
  id: 'project-1',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-03T00:00:00.000Z',
  updated_at: '2026-07-03T00:00:00.000Z',
};

const run = {
  id: 'run-1',
  project_id: project.id,
  workflow_version_id: 'version-1',
  name: 'VS-05 serial run',
  state: 'completed',
  fingerprint: 'sha256:serial-run',
  progress: {
    planned_episodes: 3,
    planned_lane_episodes: 3,
    completed_episodes: 3,
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
  lane_attempts: [
    {
      id: 'lane-attempt-1',
      lane_id: 'lane-1',
      lane_key: 'candidate',
      state: 'completed',
      artifact_root: 'lanes/candidate',
      started_at: '2026-07-03T00:00:04.000Z',
      ended_at: '2026-07-03T00:00:05.000Z',
    },
  ],
  target_revisions: [
    {
      target_id: 'target-1',
      target_revision_id: 'revision-1',
      metadata_hash: 'metadata-vs05',
    },
  ],
  episode_identities: [],
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
    {
      episode_key: 'fake.Error|i0|s1|r1|t0',
      lane_key: 'candidate',
      attempt_no: 1,
      state: 'completed',
      outcome: 'ERROR',
      error_code: 'EXECUTION_ERROR',
      artifact_root: 'lanes/candidate/trajectory/fake_Error',
    },
  ],
  run_plan: {},
  gate_verdict: null,
  created_at: '2026-07-03T00:00:03.000Z',
  started_at: '2026-07-03T00:00:04.000Z',
  ended_at: '2026-07-03T00:00:05.000Z',
};

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

describe('Test Platform serial run detail', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/runs/run-1');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows terminal episode outcomes and a Run Explorer link for the lane attempt', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
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
      if (url.pathname === '/api/platform/v1/runs/run-1') {
        return jsonResponse(run);
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    // The completed counter shows "completed / planned" (VS-07 parallel progress).
    expect(screen.getByTestId('tp-completed-episodes').textContent).toBe('3');
    const episodeAttempts = within(screen.getByTestId('tp-episode-attempts'));
    expect(episodeAttempts.getByText('PASS')).toBeTruthy();
    expect(episodeAttempts.getByText('FAIL')).toBeTruthy();
    expect(episodeAttempts.getByText('ERROR')).toBeTruthy();

    const explorerLink = screen.getByRole('link', { name: 'Open in Run Explorer' });
    expect(explorerLink.getAttribute('href')).toContain('/run_explorer.html');
  });
});
