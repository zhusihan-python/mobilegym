import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import type {
  ArtifactItem,
  Project,
  RunDetail,
  RunDiagnostics,
  RunReport,
} from '../web/test-platform/api/types';

const project: Project = {
  id: 'project-1',
  name: 'Diagnostics',
  slug: 'diagnostics',
  archived_at: null,
  created_at: '2026-07-06T00:00:00.000Z',
  updated_at: '2026-07-06T00:00:00.000Z',
};

const run: RunDetail = {
  id: 'run-1',
  project_id: project.id,
  workflow_version_id: 'wv1',
  name: 'Diagnostics run',
  state: 'failed',
  fingerprint: 'sha256:run-plan',
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 1,
    completed_episodes: 1,
    completed_lane_episodes: 1,
  },
  outcome_counts: { pass: 1, fail: 0, error: 0, cancelled: 0, incomplete: 0 },
  lanes: [
    { id: 'lane-c', lane_key: 'candidate', role: 'candidate', target_id: 'target-c', target_revision_id: 'rev-c' },
  ],
  gate_verdict: 'failed',
  created_at: '2026-07-06T00:00:00.000Z',
  started_at: '2026-07-06T00:00:01.000Z',
  ended_at: '2026-07-06T00:00:10.000Z',
  run_plan: {},
  target_revisions: [],
  episode_identities: [{
    episode_key: 'fake.Task::0',
    pair_key: 'fake.Task::0',
    task_base_id: 'fake.Task',
    task_id: 'fake.Task',
    instance_id: 0,
    instance_seed: 1,
    template_index: null,
    trial_id: 0,
    max_steps: 10,
    sequence_index: null,
    sequence_group_id: null,
  }],
  episode_attempts: [{
    episode_key: 'fake.Task::0',
    lane_key: 'candidate',
    attempt_no: 1,
    state: 'failed',
    outcome: 'ERROR',
    error_code: 'BROWSER_REQUEST_FAILED',
    artifact_root: 'artifacts/cand0',
  }],
};

const report: RunReport = {
  id: 'report-1',
  schema_version: 2,
  run_id: run.id,
  run_attempt_id: 'attempt-1',
  input_hash: 'sha256:report',
  provenance: {
    project_id: project.id,
    run_id: run.id,
    run_attempt_id: 'attempt-1',
    workflow_version_id: 'wv1',
    run_plan_hash: 'sha256:run-plan',
    task_source_digest: 'sha256:tasks',
    target_revision_ids: { candidate: 'rev-c' },
  },
  functional: {
    summary: { success_rate: 0, planned_lane_episodes: 1 },
    lanes: {},
    taxonomy: {},
  },
  performance: {
    summary: {
      unit: 'seconds',
      runtime_s: { sample_count: 0, p50: null, p75: null, p90: null, p95: null, p99: null },
      phases: {},
      excluded: {},
    },
  },
  comparison: {
    classification_counts: { regressions: 0, candidate_errors: 0 },
    coverage: {},
    runtime_s: {
      unit: 'seconds',
      sample_count: 0,
      baseline_p95: null,
      candidate_p95: null,
      absolute_delta: null,
      percent_delta: null,
    },
    phases: {},
    pairs: [],
    pair_deltas: {},
  },
  gate: {
    schema_version: 1,
    verdict: 'failed',
    thresholds: {},
    observed: {},
    reasons: [],
  },
  created_at: '2026-07-06T00:00:11.000Z',
};

const diagnostics: RunDiagnostics = {
  schema_version: 2,
  run_id: run.id,
  run_attempt_id: 'attempt-1',
  input_hash: 'sha256:diagnostics',
  provenance: report.provenance,
  summary: {
    total: 2,
    by_category: { gate: 1, network: 1 },
    by_severity: { error: 2 },
  },
  items: [
    {
      id: 'diag-error',
      code: 'BROWSER_REQUEST_FAILED',
      category: 'network',
      phase: 'browser.network',
      severity: 'error',
      retryable: true,
      message: 'connection refused',
      entity_type: 'diagnostic_event',
      source_event_id: 'event-browser',
      scope: 'episode',
      run_id: run.id,
      run_attempt_id: 'attempt-1',
      run_attempt_no: 1,
      lane_id: 'lane-c',
      lane_attempt_id: 'la-c',
      lane_key: 'candidate',
      target_id: 'target-c',
      episode_id: 'episode-c',
      episode_attempt_id: 'ea-c',
      episode_key: 'fake.Task::0',
      episode_attempt_no: 1,
      worker_id: 'W0',
      step: 2,
      task_id: 'fake.Task',
      app_ids: ['fake'],
      artifacts: [{
        id: 'artifact-1',
        kind: 'log',
        media_type: 'text/plain',
        href: `/api/platform/v1/runs/${run.id}/artifacts/artifact-1/content`,
      }],
      recommended_action: 'Inspect the browser log and retry the request.',
    },
    {
      id: 'diag-run-wide',
      code: 'QUALITY_GATE_FAILED',
      category: 'gate',
      phase: 'gate',
      severity: 'error',
      retryable: false,
      message: 'quality gate failed',
      entity_type: 'gate',
      source_event_id: null,
      scope: 'run',
      run_id: run.id,
      run_attempt_id: 'attempt-1',
      run_attempt_no: 1,
      lane_id: null,
      lane_attempt_id: null,
      lane_key: null,
      target_id: null,
      episode_id: 'episode-c',
      episode_attempt_id: null,
      episode_key: 'fake.Task::0',
      episode_attempt_no: null,
      worker_id: null,
      step: null,
      task_id: null,
      app_ids: [],
      artifacts: [],
      recommended_action: 'Inspect gate reasons.',
    },
  ],
  next_cursor: null,
};

const artifact: ArtifactItem = {
  id: 'artifact-1',
  run_id: run.id,
  run_attempt_id: 'attempt-1',
  lane_attempt_id: 'la-c',
  episode_attempt_id: 'ea-c',
  kind: 'log',
  relative_path: 'artifacts/cand0/browser_W0.log',
  media_type: 'text/plain',
  size_bytes: 12,
  sha256: 'sha256:artifact',
  created_at: '2026-07-06T00:00:12.000Z',
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

describe('Test Platform diagnostics UI', () => {
  beforeEach(() => {
    window.history.pushState({}, '', `/test-platform/runs/${run.id}`);
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows diagnostics, filters errors, and links artifacts', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
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
      if (url.pathname === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/report`) {
        return jsonResponse(report);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/diagnostics`) {
        if (url.searchParams.get('cursor') === 'diagnostics-page-2') {
          return jsonResponse({
            ...diagnostics,
            items: [diagnostics.items[0]],
            next_cursor: null,
          });
        }
        return jsonResponse({
          ...diagnostics,
          items: [diagnostics.items[1]],
          next_cursor: 'diagnostics-page-2',
        });
      }
      if (url.pathname.includes(`/api/platform/v1/runs/${run.id}/episodes/`)
        && url.pathname.endsWith('/replay')) {
        return jsonResponse({
          run_id: run.id,
          episode_key: 'fake.Task::0',
          lane_key: 'candidate',
          attempt_no: 1,
          episode_attempt_id: 'ea-c',
          artifact_root: 'artifacts/cand0',
          outcome: 'ERROR',
          error_code: 'BROWSER_REQUEST_FAILED',
          result: {},
          steps: [],
        });
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/artifacts`) {
        return jsonResponse({ items: [artifact] });
      }

      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect((await screen.findByTestId('tp-diagnostics-total')).textContent).toContain('2');
    expect(screen.getByTestId('tp-diagnostics-errors').textContent).toContain('2');
    expect(screen.getByTestId('tp-diagnostic-BROWSER_REQUEST_FAILED')).toBeTruthy();
    expect(screen.getByTestId('tp-diagnostic-QUALITY_GATE_FAILED')).toBeTruthy();
    expect(screen.getByTestId('tp-artifact-artifact-1').querySelector('a')?.href).toContain(
      `/api/platform/v1/runs/${run.id}/artifacts/${artifact.id}/content`,
    );

    const browserRow = screen.getByTestId('tp-diagnostic-BROWSER_REQUEST_FAILED');
    expect(browserRow.textContent).toContain('network');
    expect(browserRow.textContent).toContain('retryable');
    expect(browserRow.textContent).toContain('target-c');
    expect(browserRow.textContent).toContain('fake.Task');
    expect(browserRow.textContent).toContain('candidate');
    expect(browserRow.textContent).toContain('fake.Task::0');
    expect(browserRow.textContent).toContain('run 1 / episode 1');
    expect(browserRow.textContent).toContain('Inspect the browser log and retry the request.');
    expect(screen.getByRole('link', { name: 'Open log artifact' }).getAttribute('href')).toBe(
      `/api/platform/v1/runs/${run.id}/artifacts/${artifact.id}/content`,
    );

    fireEvent.change(screen.getByLabelText('Diagnostic category'), {
      target: { value: 'network' },
    });
    fireEvent.change(screen.getByLabelText('Diagnostic task'), {
      target: { value: 'fake.Task' },
    });
    fireEvent.change(screen.getByLabelText('Diagnostic retryability'), {
      target: { value: 'true' },
    });

    expect(screen.getByTestId('tp-diagnostic-BROWSER_REQUEST_FAILED')).toBeTruthy();
    expect(screen.queryByTestId('tp-diagnostic-QUALITY_GATE_FAILED')).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: 'Diagnostics' }));
    const evidence = screen.getByRole('tabpanel');
    expect(evidence.textContent).toContain('Selected episode attempt ea-c');
    expect(evidence.textContent).toContain('BROWSER_REQUEST_FAILED');
    expect(evidence.textContent).toContain('Run-wide diagnostics');
    expect(evidence.textContent).toContain('QUALITY_GATE_FAILED');
  });
});
