import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

const target = {
  id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  project_id: project.id,
  name: 'Local simulator',
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

const tasks = [
  { task_base_id: 'wechat.BlacklistContact', suite: 'wechat', class_name: 'BlacklistContact', apps: ['wechat'], templates: ['Blacklist'], parameters: {}, difficulty: 'L3', scope: 'S1', objective: 'operate', composition: 'sequential', capabilities: [], max_steps: null, answer_fields: false, optimal_path_lengths: [] },
  { task_base_id: 'wechat.OpenBlacklist', suite: 'wechat', class_name: 'OpenBlacklist', apps: ['wechat'], templates: ['Open blacklist'], parameters: {}, difficulty: 'L1', scope: 'S1', objective: 'operate', composition: 'atomic', capabilities: [], max_steps: 15, answer_fields: false, optimal_path_lengths: [] },
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

describe('Test Platform workflow editor', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/workflows');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('validates preview counts and publishes an immutable version', async () => {
    const requests: Array<{ path: string; body: any }> = [];
    let workflow = {
      id: 'workflow-1',
      project_id: project.id,
      name: 'WeChat smoke',
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
        return jsonResponse({ items: [target], next_cursor: null });
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
          task_count: 2,
          task_instance_count: 2,
          trial_count: 2,
          lane_count: 1,
          total_episodes: 4,
          lane_keys: ['candidate'],
          ordered_task_ids: ['wechat.BlacklistContact', 'wechat.OpenBlacklist'],
          execution_strategy: 'batch',
        });
      }
      if (url.pathname === '/api/platform/v1/workflows/workflow-1/publish') {
        return jsonResponse({
          workflow_id: 'workflow-1',
          workflow_version_id: 'version-1',
          version: {
            id: 'version-1',
            workflow_id: 'workflow-1',
            version_no: 1,
            status: 'published',
            definition: workflow.draft_definition,
            definition_hash: 'sha256:version',
            created_at: '2026-07-03T00:00:03.000Z',
            published_at: '2026-07-03T00:00:03.000Z',
          },
        });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Workflows' })).toBeTruthy();
    await screen.findByLabelText('Select wechat.BlacklistContact');
    expect(screen.queryByLabelText('Target')).toBeNull();
    expect(screen.queryByLabelText('Agent')).toBeNull();
    expect(screen.queryByLabelText('Model base URL')).toBeNull();
    expect(screen.queryByLabelText('Model name')).toBeNull();
    expect(screen.queryByLabelText('Model API key')).toBeNull();
    expect(screen.queryByLabelText('Image URL format')).toBeNull();
    fireEvent.click(await screen.findByLabelText('Select wechat.BlacklistContact'));
    fireEvent.click(screen.getByLabelText('Select wechat.OpenBlacklist'));
    fireEvent.change(screen.getByLabelText('Repeat count'), { target: { value: '2' } });
    fireEvent.click(screen.getByRole('button', { name: 'Validate preview' }));

    expect(await screen.findByText('4 total episodes')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Publish workflow' }));

    expect(await screen.findByText('Published version 1')).toBeTruthy();
    expect(requests.some((request) => request.path.endsWith('/publish'))).toBe(true);
    expect(screen.getByRole('button', { name: 'Open Run Launch' })).toBeTruthy();

    const draftRequest = requests.find(
      (request) => request.path.endsWith('/workflows') && request.body?.definition,
    );
    expect(draftRequest?.body.definition).toMatchObject({
      schema_version: 2,
      nodes: [
        { id: 'tasks' },
        {
          id: 'matrix',
          config: {
            lane_slots: { candidate: { role: 'candidate' } },
            repeat_n: 2,
          },
        },
        { id: 'execute' },
      ],
    });
  });

  it('builds a manual sequence definition from reordered selected tasks', async () => {
    const requests: Array<{ path: string; body: any }> = [];
    let workflow = {
      id: 'workflow-1',
      project_id: project.id,
      name: 'Manual sequence',
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
        return jsonResponse({ items: [target], next_cursor: null });
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
          task_count: 2,
          task_instance_count: 2,
          trial_count: 1,
          lane_count: 1,
          total_episodes: 2,
          lane_keys: ['candidate'],
          ordered_task_ids: ['wechat.OpenBlacklist', 'wechat.BlacklistContact'],
          execution_strategy: 'linear_sequence',
        });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Workflows' })).toBeTruthy();
    const repeatInput = await screen.findByLabelText('Repeat count');
    fireEvent.change(repeatInput, { target: { value: '3' } });
    fireEvent.change(screen.getByLabelText('Parallel workers'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('Processes'), { target: { value: '2' } });
    fireEvent.click(screen.getByLabelText('Paired comparison (baseline vs candidate)'));
    fireEvent.change(screen.getByLabelText('Workflow mode'), {
      target: { value: 'manual_sequence' },
    });

    const policyFacts = screen.getByLabelText('Manual sequence policies');
    expect(policyFacts.textContent).toContain('State policy');
    expect(policyFacts.textContent).toContain('isolated');
    expect(policyFacts.textContent).toContain('Failure policy');
    expect(policyFacts.textContent).toContain('continue');
    expect((screen.getByLabelText('Repeat count') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('Parallel workers') as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText('Processes') as HTMLInputElement).disabled).toBe(true);
    const pairedToggle = screen.getByLabelText(
      'Paired comparison (baseline vs candidate)',
    ) as HTMLInputElement;
    expect(pairedToggle.disabled).toBe(true);
    expect(pairedToggle.checked).toBe(false);

    fireEvent.click(await screen.findByLabelText('Select wechat.BlacklistContact'));
    fireEvent.click(screen.getByLabelText('Select wechat.OpenBlacklist'));
    fireEvent.click(screen.getByRole('button', { name: 'Move wechat.OpenBlacklist up' }));
    fireEvent.click(screen.getByRole('button', { name: 'Validate preview' }));

    await waitFor(() => {
      expect(requests.some((request) => request.path.endsWith('/compile-preview'))).toBe(true);
    });
    expect(await screen.findByText('linear_sequence')).toBeTruthy();
    expect(await screen.findByText('wechat.OpenBlacklist -> wechat.BlacklistContact')).toBeTruthy();

    const draftRequest = requests.find(
      (request) => request.path.endsWith('/workflows') && request.body?.definition,
    );
    expect(draftRequest).toBeTruthy();
    const definition = draftRequest!.body.definition;
    const taskSelection = definition.nodes.find((node: any) => node.type === 'task_selection');
    expect(taskSelection.config.task_ids).toEqual([
      'wechat.OpenBlacklist',
      'wechat.BlacklistContact',
    ]);
    expect(taskSelection.config.order_policy).toBe('manual');
    expect(taskSelection.config.sample_n).toBe(1);

    const matrix = definition.nodes.find((node: any) => node.type === 'matrix');
    expect(matrix.config.repeat_n).toBe(1);
    expect(definition.schema_version).toBe(2);
    expect(matrix.config.lane_slots).toEqual({ candidate: { role: 'candidate' } });

    const execute = definition.nodes.find((node: any) => node.type === 'execute');
    expect(execute.config).toMatchObject({
      execution_strategy: 'linear_sequence',
      state_policy: 'isolated',
      failure_policy: 'continue',
      parallel: 1,
      processes: 1,
    });
    expect(definition.nodes.some((node: any) => node.type === 'compare')).toBe(false);
  });

  it('copies deprecated non-secret launch preferences only into a reviewed draft', async () => {
    const savedEndpoint = 'http://127.0.0.1:9000/v1';
    const savedModel = 'saved-vision-model';
    const deprecatedSecret = 'sk-deprecated-must-be-scrubbed';
    window.localStorage.setItem('test-platform.launch.agent', 'generic_v2');
    window.localStorage.setItem('test-platform.launch.model-base-url', savedEndpoint);
    window.localStorage.setItem('test-platform.launch.model-name', savedModel);
    window.localStorage.setItem('test-platform.launch.image-url-format', 'bare_base64');
    window.localStorage.setItem('test-platform.launch.model-api-key', deprecatedSecret);
    const requests: Array<{ path: string; method: string; body?: any }> = [];

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const method = init?.method ?? 'GET';
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      requests.push({ path: url.pathname, method, body });

      if (url.pathname === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'ready' },
            migrations: { ready: true, message: 'ready' },
            runs_dir: { ready: true, message: 'ready' },
          },
        });
      }
      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/tasks') {
        return jsonResponse({ items: tasks, next_cursor: null, digest: 'sha256:test' });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        if (method === 'POST') {
          return jsonResponse({
            id: 'profile-draft-ep09',
            project_id: project.id,
            name: body.name,
            draft_spec: body.draft_spec,
            credential_readiness: {
              required_slots: [],
              bound_slots: [],
              missing_slots: [],
              ready: true,
              binding_digest: 'sha256:empty',
            },
            draft_version: 1,
            head_revision: null,
            archived_at: null,
            created_at: '2026-07-17T00:00:00.000Z',
            updated_at: '2026-07-17T00:00:00.000Z',
          }, 201);
        }
        return jsonResponse({ items: [], next_cursor: null });
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    await screen.findByLabelText('Select wechat.BlacklistContact');
    fireEvent.click(screen.getByRole('link', { name: 'Execution Profiles' }));
    expect(await screen.findByRole('heading', { name: 'Execution Profiles' })).toBeTruthy();
    expect(screen.queryByRole('dialog', { name: 'Create Execution Profile' })).toBeNull();
    expect(window.localStorage.getItem('test-platform.launch.model-base-url')).toBeNull();
    expect(window.localStorage.getItem('test-platform.launch.model-name')).toBeNull();
    expect(window.localStorage.getItem('test-platform.launch.model-api-key')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Review saved launch preferences' }));
    const dialog = await screen.findByRole('dialog', { name: 'Create Execution Profile' });
    expect((within(dialog).getByLabelText('Model endpoint') as HTMLInputElement).value)
      .toBe(savedEndpoint);
    expect((within(dialog).getByLabelText('Model name') as HTMLInputElement).value)
      .toBe(savedModel);
    expect((within(dialog).getByLabelText('Image Input Format') as HTMLSelectElement).value)
      .toBe('bare_base64');

    fireEvent.click(within(dialog).getByRole('button', { name: 'Create profile' }));
    await waitFor(() => {
      const createRequest = requests.find(
        (request) => request.path.endsWith('/execution-profiles') && request.method === 'POST',
      );
      expect(createRequest?.body.draft_spec).toMatchObject({
        agent: { id: 'generic_v2' },
        model: { base_url: savedEndpoint, name: savedModel },
        image_input: { format: 'bare_base64' },
        credentials: { required_slots: [] },
      });
    });
    expect(requests.some((request) => request.path.endsWith('/publish'))).toBe(false);
    expect(JSON.stringify(requests)).not.toContain(deprecatedSecret);
  });
});
