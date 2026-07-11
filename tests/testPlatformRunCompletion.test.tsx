import React from 'react';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';


function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}


describe('Test Platform run completion summaries', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/runs');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows lifecycle, verdict, and terminal outcome counts on the run list', async () => {
    const project = {
      id: 'project-1',
      name: 'Completion project',
      slug: 'completion-project',
      archived_at: null,
      created_at: '2026-07-11T00:00:00.000Z',
      updated_at: '2026-07-11T00:00:00.000Z',
    };
    const run = {
      id: 'run-completed-failed-gate',
      project_id: project.id,
      workflow_version_id: 'version-1',
      name: 'Completed with failed verdict',
      state: 'completed',
      fingerprint: 'sha256:completion',
      progress: {
        planned_episodes: 5,
        planned_lane_episodes: 5,
        completed_episodes: 4,
        completed_lane_episodes: 4,
      },
      outcome_counts: {
        pass: 1,
        fail: 1,
        error: 1,
        cancelled: 1,
        incomplete: 1,
      },
      lanes: [],
      gate_verdict: 'failed',
      created_at: '2026-07-11T00:00:00.000Z',
      started_at: '2026-07-11T00:00:01.000Z',
      ended_at: '2026-07-11T00:00:02.000Z',
    };
    const passedRun = {
      ...run,
      id: 'run-completed-passed-gate',
      name: 'Completed with passed verdict',
      gate_verdict: 'passed',
      outcome_counts: {
        pass: 5,
        fail: 0,
        error: 0,
        cancelled: 0,
        incomplete: 0,
      },
    };
    const lifecycleFailedRun = {
      ...run,
      id: 'run-lifecycle-failed',
      name: 'Lifecycle failure',
      state: 'failed',
      gate_verdict: null,
      outcome_counts: {
        pass: 1,
        fail: 0,
        error: 1,
        cancelled: 0,
        incomplete: 3,
      },
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      const url = new URL(raw, window.location.origin);
      if (url.pathname === '/health/ready') {
        return jsonResponse({ ready: true, checks: {} });
      }
      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/workflows') {
        return jsonResponse({ items: [], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/runs') {
        return jsonResponse({
          items: [run, passedRun, lifecycleFailedRun],
          next_cursor: null,
        });
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    const row = await waitFor(() => screen.getByRole('row', {
      name: /Completed with failed verdict/,
    }));
    const values = within(row);
    expect(values.getByText('completed')).toBeTruthy();
    expect(values.getByText('failed')).toBeTruthy();
    for (const value of ['1 pass', '1 fail', '1 error', '1 cancelled', '1 incomplete']) {
      expect(values.getByText(value)).toBeTruthy();
    }

    const passedRow = screen.getByRole('row', { name: /Completed with passed verdict/ });
    expect(within(passedRow).getByText('completed')).toBeTruthy();
    expect(within(passedRow).getByText('passed')).toBeTruthy();

    const failedRow = screen.getByRole('row', { name: /Lifecycle failure/ });
    expect(within(failedRow).getByText('failed')).toBeTruthy();
    expect(within(failedRow).getByText('pending')).toBeTruthy();
    expect(within(failedRow).getByText('3 incomplete')).toBeTruthy();
  });
});
