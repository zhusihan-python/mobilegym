import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

const project = {
  id: '11111111111111111111111111111111',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-03T00:00:00.000Z',
  updated_at: '2026-07-03T00:00:00.000Z',
};

const baselineTarget = {
  id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  project_id: project.id,
  name: 'Baseline simulator',
  kind: 'simulator',
  enabled: true,
  config: {
    kind: 'simulator',
    connection: { env_url: 'http://127.0.0.1:5173', proxy_configured: false },
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
  latest_revision: null,
  created_at: '2026-07-03T00:00:01.000Z',
  updated_at: '2026-07-03T00:00:01.000Z',
};

const candidateTarget = {
  id: 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
  project_id: project.id,
  name: 'Candidate simulator',
  kind: 'simulator',
  enabled: true,
  config: {
    kind: 'simulator',
    connection: { env_url: 'http://127.0.0.1:5174', proxy_configured: false },
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
  latest_revision: null,
  created_at: '2026-07-03T00:00:01.000Z',
  updated_at: '2026-07-03T00:00:01.000Z',
};

const targets = [baselineTarget, candidateTarget];

const tasks = [
  {
    task_base_id: 'wechat.OpenBlacklist',
    suite: 'wechat',
    class_name: 'OpenBlacklist',
    apps: ['wechat'],
    templates: ['Open blacklist'],
    parameters: {},
    difficulty: 'L1',
    scope: 'S1',
    objective: 'operate',
    composition: 'atomic',
    capabilities: [],
    max_steps: 15,
    answer_fields: false,
    optimal_path_lengths: [],
  },
];

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

describe('Test Platform paired comparison policy (VS-10)', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/workflows');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('emits a paired definition with lanes + compare node carrying the three axes', async () => {
    const requests: Array<{ path: string; body: any }> = [];
    let workflow = {
      id: 'workflow-1',
      project_id: project.id,
      name: 'Paired comparison',
      draft_definition: null,
      latest_version: null,
      created_at: '2026-07-03T00:00:02.000Z',
      updated_at: '2026-07-03T00:00:02.000Z',
    };

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      requests.push({ path: url.pathname, body });

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
        return jsonResponse({ items: tasks, next_cursor: null, digest: 'sha256:test' });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: targets, next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        if (init?.method === 'POST') {
          workflow = { ...workflow, draft_definition: body.definition };
          return jsonResponse(workflow, 201);
        }
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/workflows/workflow-1/compile-preview') {
        return jsonResponse({
          task_count: 1,
          task_instance_count: 1,
          trial_count: 1,
          lane_count: 2,
          total_episodes: 2,
          lane_keys: ['baseline', 'candidate'],
          violations: [],
        });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Workflows' })).toBeTruthy();
    fireEvent.click(await screen.findByLabelText('Select wechat.OpenBlacklist'));

    // Enable the paired/comparison policy section.
    fireEvent.click(screen.getByLabelText('Paired comparison (baseline vs candidate)'));

    // Select baseline + candidate targets.
    fireEvent.change(screen.getByLabelText('Baseline target'), {
      target: { value: baselineTarget.id },
    });
    fireEvent.change(screen.getByLabelText('Candidate target'), {
      target: { value: candidateTarget.id },
    });

    // Validate preview so the paired definition is sent.
    fireEvent.click(screen.getByRole('button', { name: 'Validate preview' }));

    await waitFor(() => {
      expect(requests.some((r) => r.path.endsWith('/compile-preview'))).toBe(true);
    });

    // The draft definition persisted on the POST /workflows or PATCH /draft
    // must carry the paired lanes + compare node with the three axes.
    const draftRequest = requests.find(
      (r) =>
        r.path.endsWith('/workflows') && r.body && r.body.definition,
    );
    expect(draftRequest).toBeTruthy();
    const definition = draftRequest!.body.definition;
    const matrix = definition.nodes.find((n: any) => n.type === 'matrix');
    expect(matrix.config.lanes).toMatchObject({
      baseline: { target_id: baselineTarget.id, role: 'baseline' },
      candidate: { target_id: candidateTarget.id, role: 'candidate' },
    });
    const compare = definition.nodes.find((n: any) => n.type === 'compare');
    expect(compare).toBeTruthy();
    expect(compare.config.target_constraints).toEqual([
      'same_app',
      'same_device',
      'same_data',
    ]);
    expect(compare.config.initial_state_policy).toBe('task_projection');
    expect(compare.config.execution).toBe('serial');
  });

  it('renders advisory constraint violations from compile-preview', async () => {
    let workflow = {
      id: 'workflow-1',
      project_id: project.id,
      name: 'Paired comparison',
      draft_definition: null,
      latest_version: null,
      created_at: '2026-07-03T00:00:02.000Z',
      updated_at: '2026-07-03T00:00:02.000Z',
    };

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const body = init?.body ? JSON.parse(String(init.body)) : null;

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
      if (url.pathname === '/api/platform/v1/tasks') {
        return jsonResponse({ items: tasks, next_cursor: null, digest: 'sha256:test' });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: targets, next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        if (init?.method === 'POST') {
          workflow = { ...workflow, draft_definition: body.definition };
          return jsonResponse(workflow, 201);
        }
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/workflows/workflow-1/compile-preview') {
        return jsonResponse({
          task_count: 1,
          task_instance_count: 1,
          trial_count: 1,
          lane_count: 2,
          total_episodes: 2,
          lane_keys: ['baseline', 'candidate'],
          violations: [
            {
              constraint: 'same_app',
              code: 'APP_VERSION_CODE_MISMATCH',
              message: "App 'wechat' versionCode differs.",
              details: { app_id: 'wechat', baseline: 80046, candidate: 80047 },
            },
          ],
        });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Workflows' })).toBeTruthy();
    fireEvent.click(await screen.findByLabelText('Select wechat.OpenBlacklist'));
    fireEvent.click(screen.getByLabelText('Paired comparison (baseline vs candidate)'));
    fireEvent.change(screen.getByLabelText('Baseline target'), {
      target: { value: baselineTarget.id },
    });
    fireEvent.change(screen.getByLabelText('Candidate target'), {
      target: { value: candidateTarget.id },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Validate preview' }));

    // The advisory violation surfaces in the preview panel (Contract 3).
    expect(await screen.findByTestId('tp-constraint-violations')).toBeTruthy();
    expect(await screen.findByText(/APP_VERSION_CODE_MISMATCH/)).toBeTruthy();
  });
});
