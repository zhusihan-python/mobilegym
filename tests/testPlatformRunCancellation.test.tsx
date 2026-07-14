import React from 'react';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import { legacyExecutionIdentity } from './testPlatformFixtures';

const project = {
  id: 'project-1',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-03T00:00:00.000Z',
  updated_at: '2026-07-03T00:00:00.000Z',
};

// A run that is actively running so the Cancel button is rendered.
const runningRun = {
  id: 'run-cancel-1',
  project_id: project.id,
  workflow_version_id: 'version-1',
  name: 'VS-06 cancellation run',
  state: 'running',
  fingerprint: 'sha256:cancel',
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 1,
    completed_episodes: 0,
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
      state: 'running',
      artifact_root: 'lanes/candidate',
      started_at: '2026-07-04T00:00:00.000Z',
      ended_at: null,
    },
  ],
  target_revisions: [
    { target_id: 'target-1', target_revision_id: 'revision-1', metadata_hash: 'metadata-1' },
  ],
  episode_identities: [],
  episode_attempts: [],
  run_plan: {},
  execution_identity: legacyExecutionIdentity,
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

// A controllable fake EventSource that lets the test push events.
class FakeEventSource {
  static lastInstance: FakeEventSource | null = null;
  listeners: Record<string, Set<(ev: MessageEvent) => void>> = {};
  onerror: ((this: FakeEventSource, ev: Event) => unknown) | null = null;
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
    this.listeners[type]?.forEach((l) => l(ev));
  }
}

describe('Test Platform run cancellation', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/runs/run-cancel-1');
    window.localStorage.clear();
    FakeEventSource.lastInstance = null;
    vi.stubGlobal('EventSource', FakeEventSource);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('renders a Cancel button for active runs and calls cancelRun on click', async () => {
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
      if (path === '/api/platform/v1/runs/run-cancel-1') {
        return jsonResponse(runningRun);
      }
      if (path === '/api/platform/v1/runs/run-cancel-1/cancel') {
        return jsonResponse({ run_id: 'run-cancel-1', cancel_requested: true, state: 'running' });
      }
      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);
    const cancelButton = await screen.findByRole('button', { name: 'Cancel run' });
    expect(cancelButton).toBeTruthy();

    fireEvent.click(cancelButton);
    await waitFor(() => {
      const cancelCalls = fetchMock.mock.calls.filter(
        ([input]) => requestPath(input as RequestInfo | URL) === '/api/platform/v1/runs/run-cancel-1/cancel',
      );
      expect(cancelCalls.length).toBe(1);
    });
  });

  it('updates the run state to cancelled via a live event without reloading', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
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
      if (path === '/api/platform/v1/runs/run-cancel-1') {
        return jsonResponse(runningRun);
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    render(<App />);
    await screen.findByRole('button', { name: 'Cancel run' });
    // Initially the run-state badge shows running.
    expect(screen.getByText('running', { selector: '.tp-run-state' })).toBeTruthy();

    // Push a live run.cancelled event through the fake EventSource.
    await waitFor(() => {
      expect(FakeEventSource.lastInstance).not.toBeNull();
    });
    FakeEventSource.lastInstance!.dispatch('run.cancelled', {
      id: 'e1',
      run_id: 'run-cancel-1',
      sequence: 1,
      type: 'run.cancelled',
      occurred_at: '2026-07-04T00:00:01.000Z',
      payload: {},
      payload_version: 1,
      run_attempt_id: null,
      lane_id: null,
      lane_attempt_id: null,
      episode_id: null,
      episode_attempt_id: null,
      worker_id: null,
    });

    await waitFor(() => {
      expect(screen.getByText('cancelled', { selector: '.tp-run-state' })).toBeTruthy();
    });
    // The Cancel button disappears because the run is no longer active.
    expect(screen.queryByRole('button', { name: 'Cancel run' })).toBeNull();
  });

  it('does not duplicate state transitions on redelivered events', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
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
      if (path === '/api/platform/v1/runs/run-cancel-1') {
        return jsonResponse(runningRun);
      }
      throw new Error(`Unexpected request: ${path}`);
    }));

    render(<App />);
    await screen.findByRole('button', { name: 'Cancel run' });

    await waitFor(() => {
      expect(FakeEventSource.lastInstance).not.toBeNull();
    });
    const payload = {
      id: 'e2',
      run_id: 'run-cancel-1',
      sequence: 2,
      type: 'run.cancelled',
      occurred_at: '2026-07-04T00:00:02.000Z',
      payload: {},
      payload_version: 1,
      run_attempt_id: null,
      lane_id: null,
      lane_attempt_id: null,
      episode_id: null,
      episode_attempt_id: null,
      worker_id: null,
    };
    FakeEventSource.lastInstance!.dispatch('run.cancelled', payload);
    await waitFor(() => expect(screen.getByText('cancelled', { selector: '.tp-run-state' })).toBeTruthy());
    // Redeliver the same sequence — state stays cancelled, no error.
    FakeEventSource.lastInstance!.dispatch('run.cancelled', payload);
    await waitFor(() => expect(screen.getByText('cancelled', { selector: '.tp-run-state' })).toBeTruthy());
  });
});
