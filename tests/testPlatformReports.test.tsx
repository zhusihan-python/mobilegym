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
  outcome_counts: { pass: 2, fail: 1, error: 1, cancelled: 0, incomplete: 0 },
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
  reliability: {
    schema_version: 1,
    summary: {
      total_materializations: 2,
      pass_at_1: 0.75,
      flaky_tasks: 1,
      insufficient_trials_tasks: 0,
    },
    tasks: [
      {
        lane_key: 'baseline',
        materialization_key: 'task.alpha|i0|s1|r1',
        task_id: 'task.alpha',
        counts: { planned: 3, attempted: 3, valid: 3, success: 3, failure: 0, error: 0, cancelled: 0, missing: 0 },
        pass_at_k: { '1': 1.0, '2': 1.0, '5': 1.0 },
        flakiness: 'stable' as const,
      },
      {
        lane_key: 'candidate',
        materialization_key: 'task.alpha|i0|s1|r1',
        task_id: 'task.alpha',
        counts: { planned: 3, attempted: 3, valid: 3, success: 2, failure: 1, error: 0, cancelled: 0, missing: 0 },
        pass_at_k: { '1': 0.6667, '2': 1.0, '5': 1.0 },
        flakiness: 'flaky' as const,
      },
      {
        lane_key: 'candidate',
        materialization_key: 'task.beta|i0|s2|r1',
        task_id: 'task.beta',
        counts: { planned: 3, attempted: 3, valid: 3, success: 3, failure: 0, error: 0, cancelled: 0, missing: 0 },
        pass_at_k: { '1': 1.0, '2': 1.0, '5': 1.0 },
        flakiness: 'stable' as const,
      },
    ],
    pass_k_values: [1, 2, 5],
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
  lane_key: 'baseline',
  target_revision_id: 'rev-b',
  created_at: '2026-07-06T00:00:12.000Z',
};

const manualRun: RunDetail = {
  ...run,
  id: 'run-manual-sequence',
  name: 'Manual sequence report run',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 2,
    completed_episodes: 2,
    completed_lane_episodes: 2,
  },
  lanes: [
    { id: 'lane-c', lane_key: 'candidate', role: 'candidate', target_id: 'target-c', target_revision_id: 'rev-c' },
  ],
  gate_verdict: 'failed',
  episode_identities: [
    {
      episode_key: 'task.alpha|i0',
      pair_key: 'task.alpha|i0',
      task_base_id: 'task.alpha',
      task_id: 'task.alpha',
      instance_id: 0,
      instance_seed: 11,
      template_index: null,
      trial_id: 0,
      max_steps: 15,
      sequence_index: 0,
      sequence_group_id: 'manual_sequence',
    },
    {
      episode_key: 'task.beta|i0',
      pair_key: 'task.beta|i0',
      task_base_id: 'task.beta',
      task_id: 'task.beta',
      instance_id: 0,
      instance_seed: 12,
      template_index: null,
      trial_id: 0,
      max_steps: 15,
      sequence_index: 1,
      sequence_group_id: 'manual_sequence',
    },
  ],
  episode_attempts: [
    {
      episode_key: 'task.alpha|i0',
      lane_key: 'candidate',
      attempt_no: 1,
      state: 'completed',
      outcome: 'FAIL',
      error_code: 'ASSERTION_FAILURE',
      artifact_root: 'lanes/candidate/trajectory/task_alpha',
    },
    {
      episode_key: 'task.beta|i0',
      lane_key: 'candidate',
      attempt_no: 1,
      state: 'completed',
      outcome: 'PASS',
      error_code: null,
      artifact_root: 'lanes/candidate/trajectory/task_beta',
    },
  ],
};

const manualReport: RunReport = {
  ...report,
  id: 'report-manual',
  run_id: manualRun.id,
  provenance: {
    ...report.provenance,
    run_id: manualRun.id,
    target_revision_ids: { candidate: 'rev-c' },
  },
  functional: {
    ...report.functional,
    summary: {
      planned_lane_episodes: 2,
      attempted_lane_episodes: 2,
      successes: 1,
      failures: 1,
      errors: 0,
      incomplete: 0,
      false_completions: 0,
      success_rate: 0.5,
      progress_rate: 1,
      error_rate: 0,
      false_completion_rate: 0,
    },
  },
  comparison: {
    classification_counts: {
      total_pairs: 0,
      regressions: 0,
      fixed: 0,
      stable_pass: 0,
      stable_fail: 0,
      baseline_errors: 0,
      candidate_errors: 0,
      pairing_violations: 0,
      unpaired: 0,
    },
    coverage: { total_pairs: 0, paired_pairs: 0, unpaired_pairs: 0, coverage_rate: 0 },
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
  sequence: {
    schema_version: 1,
    groups: [
      {
        sequence_group_id: 'manual_sequence',
        summary: {
          planned_lane_episodes: 2,
          attempted_lane_episodes: 2,
          successes: 1,
          failures: 1,
          errors: 0,
          incomplete: 0,
          success_rate: 0.5,
          progress_rate: 1,
        },
        items: [
          {
            sequence_index: 0,
            step: 1,
            sequence_group_id: 'manual_sequence',
            episode_key: 'task.alpha|i0',
            task_id: 'task.alpha',
            task_base_id: 'task.alpha',
            lane_key: 'candidate',
            status: 'completed',
            outcome: 'FAIL',
            error_code: 'ASSERTION_FAILURE',
            episode_attempt_id: 'ea-alpha',
          },
          {
            sequence_index: 1,
            step: 2,
            sequence_group_id: 'manual_sequence',
            episode_key: 'task.beta|i0',
            task_id: 'task.beta',
            task_base_id: 'task.beta',
            lane_key: 'candidate',
            status: 'completed',
            outcome: 'PASS',
            error_code: null,
            episode_attempt_id: 'ea-beta',
          },
        ],
      },
    ],
  },
  gate: {
    ...report.gate,
    observed: { min_success_rate: 0.5 },
  },
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
      if (url.pathname === `/api/platform/v1/runs/${run.id}/baseline/eligibility`) {
        const laneKey = url.searchParams.get('lane_key');
        return jsonResponse({
          run_id: run.id,
          run_attempt_id: report.run_attempt_id,
          lane_key: laneKey,
          eligible: laneKey === 'baseline',
          counts: laneKey === 'baseline'
            ? { planned: 2, pass: 2, fail: 0, error: 0, cancelled: 0, incomplete: 0 }
            : { planned: 2, pass: 1, fail: 0, error: 1, cancelled: 0, incomplete: 0 },
          reasons: laneKey === 'baseline'
            ? []
            : [{
                code: 'SELECTED_LANE_OUTCOME_NOT_PASS',
                message: 'Every selected-lane episode must have outcome PASS.',
                details: { error: 1 },
              }],
        });
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
    // Reliability section renders Pass@1 and flaky count.
    const reliability = screen.getByTestId('tp-report-reliability');
    expect(reliability.textContent).toContain('0.75');
    expect(reliability.textContent).toContain('task.alpha');
    expect(reliability.textContent).toContain('flaky');
    // Paired lanes sharing materialization_key are shown as separate rows.
    expect(reliability.textContent).toContain('baseline');
    expect(reliability.textContent).toContain('candidate');
    expect(screen.getByTestId('tp-report-pair-pair-regression')).toBeTruthy();
    expect(screen.getByTestId('tp-report-pair-pair-stable')).toBeTruthy();

    fireEvent.click(screen.getByLabelText('Regression pairs only'));

    expect(screen.getByTestId('tp-report-pair-pair-regression')).toBeTruthy();
    expect(screen.queryByTestId('tp-report-pair-pair-stable')).toBeNull();

    expect(await screen.findByText('Eligible for strict baseline promotion.')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Baseline lane'), {
      target: { value: 'candidate' },
    });
    expect(await screen.findByText('Every selected-lane episode must have outcome PASS.')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Promote baseline' }) as HTMLButtonElement).disabled,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText('Baseline lane'), {
      target: { value: 'baseline' },
    });
    await waitFor(() => {
      expect(
        (screen.getByRole('button', { name: 'Promote baseline' }) as HTMLButtonElement).disabled,
      ).toBe(false);
    });

    fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }));
    fireEvent.click(screen.getByRole('button', { name: 'Promote baseline' }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/runs/${run.id}/report/export?format=json`),
        expect.anything(),
      );
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining(`/runs/${run.id}/baseline`),
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ lane_key: 'baseline' }),
        }),
      );
    });
    expect(await screen.findByText('Promoted baseline for baseline.')).toBeTruthy();
  });

  it('groups manual sequence report items under the ordered sequence', async () => {
    window.history.pushState({}, '', `/test-platform/runs/${manualRun.id}`);
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      const decodedPath = decodeURIComponent(url.pathname);

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
      if (url.pathname === `/api/platform/v1/runs/${manualRun.id}`) {
        return jsonResponse(manualRun);
      }
      if (url.pathname === `/api/platform/v1/runs/${manualRun.id}/report`) {
        return jsonResponse(manualReport);
      }
      if (url.pathname === `/api/platform/v1/runs/${manualRun.id}/diagnostics`) {
        return jsonResponse({
          schema_version: 1,
          run_id: manualRun.id,
          run_attempt_id: 'attempt-1',
          input_hash: 'sha256:diagnostics',
          provenance: manualReport.provenance,
          summary: { total: 0, by_category: {}, by_severity: {} },
          items: [],
        });
      }
      if (url.pathname === `/api/platform/v1/runs/${manualRun.id}/artifacts`) {
        return jsonResponse({ items: [] });
      }
      if (decodedPath.includes(`/runs/${manualRun.id}/episodes/`)) {
        return jsonResponse({
          error: {
            code: 'REPLAY_ARTIFACT_MISSING',
            message: 'Replay artifacts are not available yet.',
            details: [],
          },
        }, 404);
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    const sequence = await screen.findByTestId('tp-report-sequence-manual_sequence');
    expect(sequence.textContent).toContain('Manual sequence');
    expect(sequence.textContent).toContain('manual_sequence');
    expect(sequence.textContent).toContain('Step 1');
    expect(sequence.textContent).toContain('task.alpha');
    expect(sequence.textContent).toContain('FAIL');
    expect(sequence.textContent).toContain('ASSERTION_FAILURE');
    expect(sequence.textContent).toContain('Step 2');
    expect(sequence.textContent).toContain('task.beta');
    expect(sequence.textContent).toContain('PASS');
    expect(screen.queryByLabelText('Regression pairs only')).toBeNull();
    expect(screen.queryByTestId('tp-report-pair-pair-regression')).toBeNull();
  });
});
