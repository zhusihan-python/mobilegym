import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

const project = {
  id: 'project-1',
  name: 'Test Project',
  slug: 'test-project',
  archived_at: null,
  created_at: '2026-07-12T00:00:00.000Z',
  updated_at: '2026-07-12T00:00:00.000Z',
};

const target = {
  id: 'target-1',
  project_id: project.id,
  name: 'Test Target',
  config: { kind: 'simulator', connection: { env_url: 'http://sim.invalid' } },
  executable: true,
  created_at: '2026-07-12T00:00:00.000Z',
  updated_at: '2026-07-12T00:00:00.000Z',
};

const workflow = {
  id: 'wf-1',
  project_id: project.id,
  name: 'Test Workflow',
  latest_version: {
    id: 'version-1',
    version: 1,
    version_no: 1,
    workflow_id: 'wf-1',
    status: 'published',
    definition_hash: 'sha256:testhash',
    definition: { schema_version: 1, name: 'Test', nodes: [] },
    published_at: '2026-07-12T00:00:00.000Z',
    created_at: '2026-07-12T00:00:00.000Z',
  },
  created_at: '2026-07-12T00:00:00.000Z',
  updated_at: '2026-07-12T00:00:00.000Z',
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

const SENTINEL = 'sk-sentinel-secret';

describe('Model Compatibility Check', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/execution-profiles');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows compatible result without creating a run or navigating', async () => {
    const requests: Array<{ path: string; method: string }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const method = init?.method ?? 'GET';
      requests.push({ path: url.pathname, method });

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
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs' && method === 'GET') {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/model-compatibility/check') {
        return jsonResponse({
          code: 'compatible',
          explanation: 'The model accepted the screenshot request.',
          latency_ms: 42,
          checked_model: 'vision-model',
          checked_image_format: 'data_url',
        });
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    await screen.findByRole('heading', { name: 'Execution Profiles' });
    fireEvent.click(await screen.findByRole('button', { name: 'New execution profile' }));
    fireEvent.change(await screen.findByLabelText('Model endpoint'), {
      target: { value: 'http://provider.invalid/v1' },
    });
    fireEvent.change(await screen.findByLabelText('Model name'), {
      target: { value: 'vision-model' },
    });

    const testButton = await screen.findByTestId('tp-test-connection');
    expect(testButton.getAttribute('type')).toBe('button');
    fireEvent.click(testButton);

    await waitFor(() => {
      expect(screen.getByTestId('tp-compat-result')).toBeTruthy();
    });

    const result = screen.getByTestId('tp-compat-result');
    expect(result.textContent).toContain('compatible');
    expect(result.textContent).toContain('vision-model');
    expect(result.textContent).toContain('data_url');
    expect(result.textContent).toContain('42');

    // Compatibility endpoint was called.
    expect(requests.some((r) => r.path === '/api/platform/v1/model-compatibility/check')).toBe(true);
    // Run creation endpoint was NOT called.
    expect(
      requests.some((r) => r.path === '/api/platform/v1/runs' && r.method === 'POST'),
    ).toBe(false);
  });

  it('shows failure result and does not leak the api key into the DOM', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
      if (url.pathname === '/api/platform/v1/tasks') {
        return jsonResponse({ items: [], next_cursor: null, digest: 'sha256:tasks' });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: [target], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [workflow], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs') {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/model-compatibility/check') {
        return jsonResponse({
          code: 'authentication_failure',
          explanation: 'Authentication was rejected by the provider.',
          latency_ms: 10,
          checked_model: 'test-model',
          checked_image_format: 'data_url',
        });
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    }));

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: 'New execution profile' }));
    fireEvent.change(await screen.findByLabelText('Model endpoint'), {
      target: { value: 'http://provider.invalid/v1' },
    });
    fireEvent.change(await screen.findByLabelText('Model name'), {
      target: { value: 'test-model' },
    });
    fireEvent.change(await screen.findByLabelText('Model API key'), {
      target: { value: SENTINEL },
    });

    fireEvent.click(await screen.findByTestId('tp-test-connection'));

    await waitFor(() => {
      expect(screen.getByTestId('tp-compat-result')).toBeTruthy();
    });

    const result = screen.getByTestId('tp-compat-result');
    expect(result.textContent).toContain('authentication_failure');
    // The sentinel api key must never appear in the DOM.
    expect(result.textContent).not.toContain(SENTINEL);
    expect(document.body.textContent ?? '').not.toContain(SENTINEL);
    // The api key must never be persisted to localStorage.
    expect(window.localStorage.getItem('test-platform.launch.model-api-key')).toBeNull();
  });

  it('keeps compatibility checks scoped to typed generic_v2 drafts', async () => {
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
      if (url.pathname === '/api/platform/v1/tasks') {
        return jsonResponse({ items: [], next_cursor: null, digest: 'sha256:tasks' });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: [target], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [workflow], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs') {
        return jsonResponse({ items: [], next_cursor: null });
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    }));

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: 'New execution profile' }));
    const agent = await screen.findByLabelText('Agent') as HTMLSelectElement;
    expect(agent.value).toBe('generic_v2');
    expect(agent.disabled).toBe(true);
    expect(screen.getByTestId('tp-test-connection')).toBeTruthy();
  });

  it('does not let a late stale response overwrite a newer result', async () => {
    // Deferred promises let us control when each compatibility response resolves.
    const deferreds: Array<{
      resolve: (value: Response) => void;
      promise: Promise<Response>;
    }> = [];

    function makeDeferred() {
      let resolve!: (value: Response) => void;
      const promise = new Promise<Response>((r) => {
        resolve = r;
      });
      deferreds.push({ resolve, promise });
      return promise;
    }

    let callCount = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
      if (url.pathname === '/api/platform/v1/tasks') {
        return jsonResponse({ items: [], next_cursor: null, digest: 'sha256:tasks' });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: [target], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [workflow], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs') {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/model-compatibility/check') {
        callCount++;
        return makeDeferred();
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    }));

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: 'New execution profile' }));
    fireEvent.change(await screen.findByLabelText('Model endpoint'), {
      target: { value: 'http://provider.invalid/v1' },
    });
    fireEvent.change(await screen.findByLabelText('Model name'), {
      target: { value: 'first-model' },
    });

    // Fire the first check (will be deferred).
    fireEvent.click(screen.getByTestId('tp-test-connection'));
    await waitFor(() => expect(callCount).toBe(1));

    // Change the model name (clears stale result + invalidates token).
    fireEvent.change(screen.getByLabelText('Model name'), {
      target: { value: 'second-model' },
    });
    // The stale result should be cleared.
    expect(screen.queryByTestId('tp-compat-result')).toBeNull();

    // Fire the second check.
    fireEvent.click(screen.getByTestId('tp-test-connection'));
    await waitFor(() => expect(callCount).toBe(2));

    // Resolve the LATE (first/stale) response first — it must NOT appear.
    deferreds[0].resolve(
      jsonResponse({
        code: 'compatible',
        explanation: 'Stale result.',
        latency_ms: 1,
        checked_model: 'first-model',
        checked_image_format: 'data_url',
      }),
    );
    await new Promise((r) => setTimeout(r, 50));

    // Stale result must not show.
    expect(screen.queryByTestId('tp-compat-result')).toBeNull();

    // Resolve the NEWER (second) response — it SHOULD appear.
    deferreds[1].resolve(
      jsonResponse({
        code: 'authentication_failure',
        explanation: 'Authentication was rejected.',
        latency_ms: 2,
        checked_model: 'second-model',
        checked_image_format: 'data_url',
      }),
    );
    await waitFor(() => {
      expect(screen.getByTestId('tp-compat-result')).toBeTruthy();
    });

    const result = screen.getByTestId('tp-compat-result');
    expect(result.textContent).toContain('authentication_failure');
    expect(result.textContent).toContain('second-model');
    expect(result.textContent).not.toContain('first-model');
  });
});
