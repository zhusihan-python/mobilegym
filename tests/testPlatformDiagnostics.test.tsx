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
  lanes: [
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
  schema_version: 1,
  run_id: run.id,
  run_attempt_id: 'attempt-1',
  input_hash: 'sha256:diagnostics',
  provenance: report.provenance,
  summary: {
    total: 2,
    by_category: { assertion: 1, execution: 1 },
    by_severity: { error: 1, warning: 1 },
  },
  items: [
    {
      id: 'diag-error',
      code: 'EXECUTION_ERROR',
      category: 'execution',
      phase: 'execute',
      severity: 'error',
      retryable: true,
      message: 'candidate crashed',
      entity_type: 'episode_attempt',
      run_id: run.id,
      run_attempt_id: 'attempt-1',
      lane_attempt_id: 'la-c',
      episode_attempt_id: 'ea-c',
      artifact_refs: ['artifacts/cand0'],
      recommended_action: 'Inspect execution logs.',
      raw: {},
    },
    {
      id: 'diag-warning',
      code: 'ASSERTION_FAILURE',
      category: 'assertion',
      phase: 'judge',
      severity: 'warning',
      retryable: false,
      message: 'judge mismatch',
      entity_type: 'episode_attempt',
      run_id: run.id,
      run_attempt_id: 'attempt-1',
      lane_attempt_id: 'la-c',
      episode_attempt_id: 'ea-c2',
      artifact_refs: [],
      recommended_action: 'Inspect judge evidence.',
      raw: {},
    },
  ],
};

const artifact: ArtifactItem = {
  id: 'artifact-1',
  run_id: run.id,
  run_attempt_id: 'attempt-1',
  lane_attempt_id: 'la-c',
  episode_attempt_id: 'ea-c',
  kind: 'json',
  relative_path: 'artifacts/cand0/trace.json',
  media_type: 'application/json',
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
        return jsonResponse(diagnostics);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/artifacts`) {
        return jsonResponse({ items: [artifact] });
      }

      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect((await screen.findByTestId('tp-diagnostics-total')).textContent).toContain('2');
    expect(screen.getByTestId('tp-diagnostics-errors').textContent).toContain('1');
    expect(screen.getByTestId('tp-diagnostic-EXECUTION_ERROR')).toBeTruthy();
    expect(screen.getByTestId('tp-diagnostic-ASSERTION_FAILURE')).toBeTruthy();
    expect(screen.getByTestId('tp-artifact-artifact-1').querySelector('a')?.href).toContain(
      `/api/platform/v1/runs/${run.id}/artifacts/${artifact.id}/content`,
    );

    fireEvent.click(screen.getByLabelText('Errors only'));

    expect(screen.getByTestId('tp-diagnostic-EXECUTION_ERROR')).toBeTruthy();
    expect(screen.queryByTestId('tp-diagnostic-ASSERTION_FAILURE')).toBeNull();
  });
});
