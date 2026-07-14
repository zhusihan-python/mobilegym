import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import type { Project, RunDetail } from '../web/test-platform/api/types';
import { legacyExecutionIdentity } from './testPlatformFixtures';

const project: Project = {
  id: 'project-1',
  name: 'Imported',
  slug: 'imported',
  archived_at: null,
  created_at: '2026-07-06T00:00:00.000Z',
  updated_at: '2026-07-06T00:00:00.000Z',
};

const importedRun: RunDetail = {
  id: 'run-imported',
  project_id: project.id,
  workflow_version_id: 'wv-imported',
  name: 'Imported CLI run',
  state: 'completed',
  fingerprint: 'sha256:imported',
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 1,
    completed_episodes: 1,
    completed_lane_episodes: 1,
  },
  outcome_counts: { pass: 1, fail: 0, error: 0, cancelled: 0, incomplete: 0 },
  lanes: [
    {
      id: 'lane-imported',
      lane_key: 'legacy',
      role: 'candidate',
      target_id: 'target-imported',
      target_revision_id: 'revision-imported',
    },
  ],
  gate_verdict: null,
  created_at: '2026-07-06T00:00:00.000Z',
  started_at: '2026-07-06T00:00:00.000Z',
  ended_at: '2026-07-06T00:00:10.000Z',
  run_plan: {},
  execution_identity: legacyExecutionIdentity,
  imported: {
    source_path: '/tmp/legacy-run',
    source_name: 'legacy-run',
    provenance_missing: ['workflow', 'target_revision'],
  },
  run_attempts: [],
  lane_attempts: [
    {
      id: 'lane-attempt-imported',
      lane_id: 'lane-imported',
      lane_key: 'legacy',
      run_attempt_id: 'attempt-imported',
      attempt_no: 1,
      reason: 'imported',
      state: 'completed',
      artifact_root: '.',
      started_at: '2026-07-06T00:00:00.000Z',
      ended_at: '2026-07-06T00:00:10.000Z',
    },
  ],
  target_revisions: [
    {
      target_id: 'target-imported',
      target_revision_id: 'revision-imported',
      metadata_hash: 'sha256:unknown',
    },
  ],
  episode_identities: [],
  episode_attempts: [],
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

describe('Test Platform imported runs UI', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('imports a legacy run from the Runs page', async () => {
    window.history.pushState({}, '', '/test-platform/runs');
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const method = init?.method ?? 'GET';
      if (url.pathname === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'ok' },
            migrations: { ready: true, message: 'ok' },
            runs_dir: { ready: true, message: 'ok' },
          },
        });
      }
      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs' && method === 'GET') {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs/import' && method === 'POST') {
        return jsonResponse(importedRun, 201);
      }
      if (url.pathname === `/api/platform/v1/runs/${importedRun.id}`) {
        return jsonResponse(importedRun);
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    fireEvent.change(await screen.findByLabelText('Legacy run path'), {
      target: { value: '/tmp/legacy-run' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Import legacy run' }));

    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([input]) => requestUrl(input as RequestInfo | URL).pathname === '/api/platform/v1/runs/import')).toBe(true);
    });
  });

  it('shows imported provenance warnings on Run Detail', async () => {
    window.history.pushState({}, '', `/test-platform/runs/${importedRun.id}`);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.pathname === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'ok' },
            migrations: { ready: true, message: 'ok' },
            runs_dir: { ready: true, message: 'ok' },
          },
        });
      }
      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/runs/${importedRun.id}`) {
        return jsonResponse(importedRun);
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Imported legacy run' })).toBeTruthy();
    expect(
      within(screen.getByTestId('tp-run-completion-facts')).getByText(
        'Legacy Execution Identity',
      ),
    ).toBeTruthy();
    expect(screen.getByText('/tmp/legacy-run')).toBeTruthy();
    expect(screen.getByText(/workflow, target_revision/)).toBeTruthy();
  });
});
