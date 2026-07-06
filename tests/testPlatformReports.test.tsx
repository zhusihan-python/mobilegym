import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import type { Baseline, Project, RunDetail, RunReport } from '../web/test-platform/api/types';

const originalCreateObjectUrl = URL.createObjectURL;
const originalRevokeObjectUrl = URL.revokeObjectURL;

const project: Project = {
  id: 'project-1',
  name: 'Reports',
  slug: 'reports',
  archived_at: null,
  created_at: '2026-07-06T00:00:00.000Z',
  updated_at: '2026-07-06T00:00:00.000Z',
};

const run: RunDetail = {
  id: 'run-1',
  project_id: project.id,
  workflow_version_id: 'wv1',
  name: 'Paired report run',
  state: 'completed',
  fingerprint: 'sha256:run-plan',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 4,
    completed_episodes: 2,
    completed_lane_episodes: 4,
  },
  lanes: [
    { id: 'lane-b', lane_key: 'baseline', role: 'baseline', target_id: 'target-b', target_revision_id: 'rev-b' },
    { id: 'lane-c', lane_key: 'candidate', role: 'candidate', target_id: 'target-c', target_revision_id: 'rev-c' },
  ],
  gate_verdict: 'failed',
  created_at: '2026-07-06T00:00:00.000Z',
  started_at: '2026-07-06T00:00:01.000Z',
  ended_at: '2026-07-06T00:00:10.000Z',
  run_plan: {},
  target_revisions: [],
  episode_identities: [],
  episode_attempts: [],
};

const report: RunReport = {
  id: 'report-1',
  schema_version: 1,
  run_id: run.id,
  run_attempt_id: 'attempt-1',
  input_hash: 'sha256:input',
  provenance: {
    project_id: project.id,
    run_id: run.id,
    run_attempt_id: 'attempt-1',
    workflow_version_id: 'wv1',
    run_plan_hash: 'sha256:run-plan',
    task_source_digest: 'sha256:tasks',
    target_revision_ids: { baseline: 'rev-b', candidate: 'rev-c' },
  },
  functional: {
    summary: {
      planned_lane_episodes: 4,
      attempted_lane_episodes: 4,
      successes: 3,
      failures: 1,
      errors: 0,
      incomplete: 0,
      false_completions: 0,
      success_rate: 0.75,
      progress_rate: 1,
      error_rate: 0,
      false_completion_rate: 0,
    },
    lanes: {},
    taxonomy: {},
  },
  performance: {
    summary: {
      unit: 'seconds',
      runtime_s: { sample_count: 4, p50: 10, p75: 12, p90: 12, p95: 12, p99: 12 },
      phases: {},
      excluded: { incomplete: 0, cancelled: 0, errors: 0, non_numeric_runtime: 0 },
    },
  },
  comparison: {
    classification_counts: {
      total_pairs: 2,
      regressions: 1,
      fixed: 0,
      stable_pass: 1,
      stable_fail: 0,
      baseline_errors: 0,
      candidate_errors: 0,
      pairing_violations: 0,
      unpaired: 0,
    },
    coverage: { total_pairs: 2, paired_pairs: 2, unpaired_pairs: 0, coverage_rate: 1 },
    runtime_s: {
      unit: 'seconds',
      sample_count: 2,
      baseline_p95: 10,
      candidate_p95: 12,
      absolute_delta: 2,
      percent_delta: 20,
    },
    phases: {},
    pairs: [
      {
        pair_key: 'pair-regression',
        classification: 'regression',
        baseline_episode_attempt_id: 'ea-b1',
        candidate_episode_attempt_id: 'ea-c1',
        delta: { runtime_s: { absolute: 2, percent: 20 } },
      },
      {
        pair_key: 'pair-stable',
        classification: 'stable_pass',
        baseline_episode_attempt_id: 'ea-b2',
        candidate_episode_attempt_id: 'ea-c2',
        delta: { runtime_s: { absolute: 0, percent: 0 } },
      },
    ],
    pair_deltas: {},
  },
  gate: {
    schema_version: 1,
    verdict: 'failed',
    thresholds: { max_regressions: 0 },
    observed: { max_regressions: 1 },
    reasons: [
      {
        metric: 'max_regressions',
        reason: 'threshold_exceeded',
        threshold: 0,
        observed: 1,
      },
    ],
  },
  created_at: '2026-07-06T00:00:11.000Z',
};

const baseline: Baseline = {
  id: 'baseline-1',
  report_id: report.id,
  run_id: run.id,
  project_id: project.id,
  workflow_version_id: 'wv1',
  run_plan_hash: 'sha256:run-plan',
  task_source_digest: 'sha256:tasks',
  target_revision_ids: { baseline: 'rev-b', candidate: 'rev-c' },
  lane_key: 'candidate',
  target_revision_id: 'rev-c',
  created_at: '2026-07-06T00:00:12.000Z',
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function textResponse(body: string, contentType: string): Response {
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': contentType },
  });
}

function requestUrl(input: RequestInfo | URL): URL {
  if (typeof input === 'string') return new URL(input, window.location.origin);
  if (input instanceof URL) return input;
  return new URL(input.url);
}

describe('Test Platform reports UI', () => {
  beforeEach(() => {
    window.history.pushState({}, '', `/test-platform/runs/${run.id}`);
    window.localStorage.clear();
    Object.defineProperty(URL, 'createObjectURL', {
      value: undefined,
      configurable: true,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: vi.fn(),
      configurable: true,
    });
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    Object.defineProperty(URL, 'createObjectURL', {
      value: originalCreateObjectUrl,
      configurable: true,
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      value: originalRevokeObjectUrl,
      configurable: true,
    });
    window.localStorage.clear();
  });

  it('shows gate verdict, filters regressions, drills into pair diffs, exports, and promotes baseline', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const method = init?.method ?? 'GET';

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
      if (url.pathname === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/report`) {
        return jsonResponse(report);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/diagnostics`) {
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          run_attempt_id: 'attempt-1',
          input_hash: 'sha256:diagnostics',
          provenance: report.provenance,
          summary: { total: 0, by_category: {}, by_severity: {} },
          items: [],
        });
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/artifacts`) {
        return jsonResponse({ items: [] });
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/report/export`) {
        return textResponse(
          url.searchParams.get('format') === 'html' ? '<html>report</html>' : JSON.stringify(report),
          url.searchParams.get('format') === 'html' ? 'text/html' : 'application/json',
        );
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/baseline` && method === 'POST') {
        return jsonResponse(baseline, 201);
      }

      throw new Error(`Unexpected request: ${method} ${url.pathname}${url.search}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect((await screen.findByTestId('tp-gate-verdict')).textContent).toContain('failed');
    expect(screen.getByTestId('tp-report-regressions').textContent).toContain('1');
    expect(screen.getByTestId('tp-report-runtime-delta').textContent).toContain('20%');
    expect(screen.getByTestId('tp-report-pair-pair-regression')).toBeTruthy();
    expect(screen.getByTestId('tp-report-pair-pair-stable')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Regression pairs only'));

    expect(screen.getByTestId('tp-report-pair-pair-regression')).toBeTruthy();
    expect(screen.queryByTestId('tp-report-pair-pair-stable')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }));
    fireEvent.click(screen.getByRole('button', { name: 'Promote baseline' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/runs/${run.id}/report/export?format=json`),
        expect.anything(),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/runs/${run.id}/baseline`),
        expect.objectContaining({ method: 'POST' }),
      );
    });
    expect(await screen.findByText('Promoted baseline for candidate.')).toBeTruthy();
  });
});
