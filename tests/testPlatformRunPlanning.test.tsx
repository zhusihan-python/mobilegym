import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
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

const target = {
  id: 'target-1',
  project_id: project.id,
  name: 'Local simulator',
  kind: 'simulator',
  enabled: true,
  config: {
    kind: 'simulator',
    connection: { env_url: 'http://127.0.0.1:5173' },
    device_profile: {
      name: 'Pixel 7',
      viewport_width: 393,
      viewport_height: 852,
      physical_width: 1080,
      physical_height: 2400,
      device_scale_factor: 2.75,
    },
    runtime: {},
    labels: {},
  },
  latest_revision: {
    id: 'revision-1',
    metadata_hash: 'metadata-vs04',
    health_status: 'healthy',
    resolved_at: '2026-07-03T00:00:01.000Z',
    warnings: [],
    metadata: {},
  },
  created_at: '2026-07-03T00:00:00.000Z',
  updated_at: '2026-07-03T00:00:00.000Z',
};

const definition = {
  schema_version: 1 as const,
  name: 'WeChat smoke',
  nodes: [
    {
      id: 'tasks',
      type: 'task_selection' as const,
      depends_on: [],
      config: { task_ids: ['wechat.OpenBlacklist'], sample_n: 1 },
    },
    {
      id: 'matrix',
      type: 'matrix' as const,
      depends_on: ['tasks'],
      config: { lanes: { candidate: { target_id: target.id } }, repeat_n: 2 },
    },
    {
      id: 'execute',
      type: 'execute' as const,
      depends_on: ['matrix'],
      config: { parallel: 1 },
    },
  ],
};

const version = {
  id: 'version-1',
  workflow_id: 'workflow-1',
  version_no: 1,
  status: 'published' as const,
  definition,
  definition_hash: 'sha256:workflow',
  created_at: '2026-07-03T00:00:02.000Z',
  published_at: '2026-07-03T00:00:02.000Z',
};

const workflow = {
  id: 'workflow-1',
  project_id: project.id,
  name: 'WeChat smoke',
  draft_definition: definition,
  latest_version: version,
  created_at: '2026-07-03T00:00:02.000Z',
  updated_at: '2026-07-03T00:00:02.000Z',
};

const run = {
  id: 'run-1',
  project_id: project.id,
  workflow_version_id: version.id,
  name: 'WeChat smoke',
  state: 'queued',
  fingerprint: 'sha256:run-plan-vs04',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 2,
    completed_episodes: 0,
  },
  lanes: [
    {
      id: 'lane-1',
      lane_key: 'candidate',
      role: 'candidate',
      target_id: target.id,
      target_revision_id: target.latest_revision.id,
    },
  ],
  target_revisions: [
    {
      target_id: target.id,
      target_revision_id: target.latest_revision.id,
      metadata_hash: target.latest_revision.metadata_hash,
    },
  ],
  episode_identities: [
    {
      episode_key: 'wechat.OpenBlacklist|i0|s123|r1|t0',
      pair_key: 'wechat.OpenBlacklist|i0|s123|r1|t0',
      task_base_id: 'wechat.OpenBlacklist',
      task_id: 'wechat.OpenBlacklist',
      instance_id: 0,
      instance_seed: 123,
      template_index: null,
      trial_id: 0,
      max_steps: 15,
    },
    {
      episode_key: 'wechat.OpenBlacklist|i0|s123|r1|t1',
      pair_key: 'wechat.OpenBlacklist|i0|s123|r1|t1',
      task_base_id: 'wechat.OpenBlacklist',
      task_id: 'wechat.OpenBlacklist',
      instance_id: 0,
      instance_seed: 123,
      template_index: null,
      trial_id: 1,
      max_steps: 15,
    },
  ],
  run_plan: {},
  gate_verdict: null,
  created_at: '2026-07-03T00:00:03.000Z',
  started_at: null,
  ended_at: null,
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

describe('Test Platform immutable run planning', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/workflows');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('launches a published workflow and shows the queued frozen run overview', async () => {
    const requests: Array<{ path: string; method: string; headers: Headers }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      requests.push({
        path: url.pathname,
        method: init?.method ?? 'GET',
        headers: new Headers(init?.headers),
      });

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
      if (url.pathname === '/api/platform/v1/tasks') {
        return jsonResponse({ items: [], next_cursor: null, digest: 'sha256:tasks' });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: [target], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [workflow], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs' && init?.method === 'POST') {
        return jsonResponse(run, 201);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Workflows' })).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'Launch version 1' }));

    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    expect(screen.getByText('queued')).toBeTruthy();
    expect(screen.getByText('2 planned episodes')).toBeTruthy();
    expect(screen.getByText('revision-1')).toBeTruthy();
    expect(screen.getByText('sha256:run-plan-vs04')).toBeTruthy();

    const launch = requests.find(
      (request) => request.path === '/api/platform/v1/runs' && request.method === 'POST',
    );
    expect(launch?.headers.get('Idempotency-Key')).toBeTruthy();
  });
});
