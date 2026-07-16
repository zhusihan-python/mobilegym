import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

const project = {
  id: 'project-ep06',
  name: 'Execution Comparison',
  slug: 'execution-comparison',
  archived_at: null,
  created_at: '2026-07-17T00:00:00.000Z',
  updated_at: '2026-07-17T00:00:00.000Z',
};

const workflowVersion = {
  id: 'workflow-version-ep06',
  workflow_id: 'workflow-ep06',
  version_no: 1,
  status: 'published',
  definition: {
    schema_version: 2,
    name: 'Execution Comparison',
    nodes: [
      { id: 'tasks', type: 'task_selection', depends_on: [], config: {} },
      {
        id: 'slots',
        type: 'matrix',
        depends_on: ['tasks'],
        config: {
          lane_slots: {
            baseline: { role: 'baseline' },
            candidate: { role: 'candidate' },
          },
        },
      },
      { id: 'execute', type: 'execute', depends_on: ['slots'], config: {} },
      {
        id: 'compare',
        type: 'compare',
        depends_on: ['execute'],
        config: { initial_state_policy: 'task_projection', execution: 'serial' },
      },
    ],
  },
  definition_hash: 'sha256:workflow-ep06',
  created_at: '2026-07-17T00:00:01.000Z',
  published_at: '2026-07-17T00:00:01.000Z',
};

const workflow = {
  id: workflowVersion.workflow_id,
  project_id: project.id,
  name: workflowVersion.definition.name,
  draft_definition: workflowVersion.definition,
  latest_version: workflowVersion,
  created_at: workflowVersion.created_at,
  updated_at: workflowVersion.created_at,
};

const targetRevision = {
  id: 'target-revision-shared-ep06',
  metadata_hash: 'sha256:target-shared-ep06',
  health_status: 'healthy',
  resolved_at: '2026-07-17T00:00:02.000Z',
  warnings: [],
  metadata: {},
};

const target = {
  id: 'target-shared-ep06',
  project_id: project.id,
  name: 'Shared simulator',
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
  latest_revision: targetRevision,
  created_at: targetRevision.resolved_at,
  updated_at: targetRevision.resolved_at,
};

function profile(id: string, name: string, modelName: string) {
  const publicSpec = {
    schema_version: 1 as const,
    agent: { id: 'generic_v2' as const },
    model: {
      protocol: 'openai_chat_completions' as const,
      base_url: 'http://127.0.0.1:1234/v1',
      name: modelName,
    },
    image_input: { format: 'data_url' as const },
    generation: { temperature: 0, top_p: 1, max_tokens: 4096, stream: true },
    inference: { timeout_seconds: 300 },
    credentials: { required_slots: [] },
  };
  const revision = {
    id: `${id}-revision-1`,
    execution_profile_id: id,
    revision_no: 1,
    public_spec: publicSpec,
    public_spec_hash: `sha256:${id}-public`,
    credential_binding_digest: 'sha256:empty-binding',
    credential_readiness: {
      required_slots: [],
      bound_slots: [],
      missing_slots: [],
      ready: true,
      binding_digest: 'sha256:empty-binding',
    },
    published_at: '2026-07-17T00:00:03.000Z',
  };
  return {
    id,
    project_id: project.id,
    name,
    draft_spec: publicSpec,
    credential_readiness: revision.credential_readiness,
    draft_version: 1,
    head_revision: revision,
    archived_at: null,
    created_at: revision.published_at,
    updated_at: revision.published_at,
  };
}

const baselineProfile = profile(
  'profile-baseline-ep06',
  'Baseline subject',
  'deterministic-profile-pass',
);
const candidateProfile = profile(
  'profile-candidate-ep06',
  'Candidate subject',
  'deterministic-profile-fail',
);

const preview = {
  workflow_version_id: workflowVersion.id,
  workflow_version_hash: workflowVersion.definition_hash,
  comparison_intent: 'execution_comparison',
  lane_bindings: [baselineProfile, candidateProfile].map((item, index) => ({
    lane_slot: index === 0 ? 'baseline' : 'candidate',
    role: index === 0 ? 'baseline' : 'candidate',
    target_id: target.id,
    target_revision_id: targetRevision.id,
    target_revision_hash: targetRevision.metadata_hash,
    execution_profile_id: item.id,
    execution_profile_name: item.name,
    execution_profile_revision_id: item.head_revision.id,
    execution_profile_revision_no: 1,
    execution_profile_public_hash: item.head_revision.public_spec_hash,
    execution_profile_revision_hash: `sha256:${item.id}-revision`,
    lane_fingerprint: `sha256:${item.id}-lane`,
  })),
  execution_profile_diff: {
    from_revision_id: baselineProfile.head_revision.id,
    to_revision_id: candidateProfile.head_revision.id,
    changes: [{
      path: 'model.name',
      before: 'deterministic-profile-pass',
      after: 'deterministic-profile-fail',
    }],
  },
  constraint_violations: [],
  episode_count: 1,
  fingerprint_inputs: {},
  run_plan_fingerprint: 'sha256:run-plan-ep06',
  preview_token: 'sha256:preview-ep06',
  credential_requirements: [],
};

const run = {
  id: 'run-ep06',
  project_id: project.id,
  workflow_version_id: workflowVersion.id,
  name: 'Execution Comparison',
  state: 'queued',
  fingerprint: preview.run_plan_fingerprint,
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 2,
    completed_episodes: 0,
    completed_lane_episodes: 0,
  },
  outcome_counts: { pass: 0, fail: 0, error: 0, cancelled: 0, incomplete: 2 },
  lanes: preview.lane_bindings.map((binding, index) => ({
    id: `lane-ep06-${index}`,
    lane_key: binding.lane_slot,
    role: binding.role,
    target_id: binding.target_id,
    target_revision_id: binding.target_revision_id,
    execution_profile_revision_id: binding.execution_profile_revision_id,
    execution_profile_revision_hash: binding.execution_profile_revision_hash,
    lane_fingerprint: binding.lane_fingerprint,
  })),
  run_plan: {
    schema_version: 2,
    comparison: { intent: 'execution_comparison' },
  },
  execution_identity: {
    kind: 'profile_aware',
    label: 'Execution Profile Revision',
    schema_version: 2,
    lane_bindings: preview.lane_bindings.map((binding) => ({
      lane_slot: binding.lane_slot,
      target_revision_id: binding.target_revision_id,
      target_revision_hash: binding.target_revision_hash,
      execution_profile_id: binding.execution_profile_id,
      execution_profile_name: binding.execution_profile_name,
      execution_profile_revision_id: binding.execution_profile_revision_id,
      execution_profile_revision_no: binding.execution_profile_revision_no,
      execution_profile_public_hash: binding.execution_profile_public_hash,
      execution_profile_revision_hash: binding.execution_profile_revision_hash,
      lane_fingerprint: binding.lane_fingerprint,
    })),
  },
  run_attempts: [],
  lane_attempts: [],
  target_revisions: [{
    target_id: target.id,
    target_revision_id: targetRevision.id,
    metadata_hash: targetRevision.metadata_hash,
  }],
  episode_identities: [],
  episode_attempts: [],
  gate_verdict: null,
  created_at: '2026-07-17T00:00:04.000Z',
  started_at: null,
  ended_at: null,
};

const episodeKey = 'fake.ExecutionComparison|i0|s1|r1|t0';

const completedRun = {
  ...run,
  state: 'completed',
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 2,
    completed_episodes: 1,
    completed_lane_episodes: 2,
  },
  outcome_counts: { pass: 1, fail: 1, error: 0, cancelled: 0, incomplete: 0 },
  run_attempts: [{
    id: 'run-attempt-ep06',
    attempt_no: 1,
    reason: 'initial',
    state: 'completed',
    started_at: '2026-07-17T00:00:04.000Z',
    ended_at: '2026-07-17T00:00:06.000Z',
    compatibility: [],
    created_at: '2026-07-17T00:00:04.000Z',
  }],
  lane_attempts: [],
  episode_identities: [{
    episode_key: episodeKey,
    pair_key: episodeKey,
    task_base_id: 'fake.ExecutionComparison',
    task_id: 'fake.ExecutionComparison',
    instance_id: 0,
    instance_seed: 20260717,
    template_index: null,
    trial_id: 0,
    max_steps: 15,
    sequence_index: null,
    sequence_group_id: null,
  }],
  episode_attempts: [
    {
      episode_key: episodeKey,
      lane_key: 'baseline',
      run_attempt_id: 'run-attempt-ep06',
      episode_attempt_id: 'episode-attempt-baseline-ep06',
      attempt_no: 1,
      state: 'completed',
      outcome: 'PASS',
      error_code: null,
      artifact_root: 'lanes/baseline/trajectory/fake_ExecutionComparison',
    },
    {
      episode_key: episodeKey,
      lane_key: 'candidate',
      run_attempt_id: 'run-attempt-ep06',
      episode_attempt_id: 'episode-attempt-candidate-ep06',
      attempt_no: 1,
      state: 'completed',
      outcome: 'FAIL',
      error_code: 'ASSERTION_FAILURE',
      artifact_root: 'lanes/candidate/trajectory/fake_ExecutionComparison',
    },
  ],
  gate_verdict: 'failed',
  started_at: '2026-07-17T00:00:04.000Z',
  ended_at: '2026-07-17T00:00:06.000Z',
};

const comparison = {
  id: 'comparison-ep06',
  run_id: completedRun.id,
  run_attempt_id: 'run-attempt-ep06',
  baseline_lane_id: completedRun.lanes[0].id,
  candidate_lane_id: completedRun.lanes[1].id,
  policy: { intent: 'execution_comparison' },
  summary: { regressions: 1 },
  created_at: completedRun.ended_at,
  pairs: [{
    id: 'comparison-pair-ep06',
    comparison_id: 'comparison-ep06',
    pair_key: episodeKey,
    classification: 'regression',
    baseline_episode_attempt_id: 'episode-attempt-baseline-ep06',
    candidate_episode_attempt_id: 'episode-attempt-candidate-ep06',
    delta: { baseline_outcome: 'PASS', candidate_outcome: 'FAIL' },
    integrity: {
      status: 'ok',
      prepared_projection_hash: 'sha256:prepared-ep06',
      baseline_actual_projection_hash: 'sha256:prepared-ep06',
      candidate_actual_projection_hash: 'sha256:prepared-ep06',
    },
    prepared: {
      params: { sampled: 'shared' },
      instruction: 'Verify one shared Prepared Episode.',
      projection_hash: 'sha256:prepared-ep06',
    },
  }],
};

const report = {
  id: 'report-ep06',
  schema_version: 1,
  run_id: completedRun.id,
  run_attempt_id: 'run-attempt-ep06',
  input_hash: 'sha256:report-input-ep06',
  provenance: {
    project_id: project.id,
    run_id: completedRun.id,
    run_attempt_id: 'run-attempt-ep06',
    workflow_version_id: workflowVersion.id,
    run_plan_hash: preview.run_plan_fingerprint,
    task_source_digest: 'sha256:tasks-ep06',
    target_revision_ids: {
      baseline: targetRevision.id,
      candidate: targetRevision.id,
    },
  },
  functional: {
    summary: { success_rate: 0.5 },
    lanes: {},
    taxonomy: {},
  },
  performance: {
    summary: { unit: 'seconds', runtime_s: {}, phases: {}, excluded: {} },
  },
  comparison: {
    classification_counts: { regressions: 1, candidate_errors: 0 },
    coverage: { total_pairs: 1, paired_pairs: 1, unpaired_pairs: 0, coverage_rate: 1 },
    runtime_s: {
      unit: 'seconds',
      sample_count: 1,
      baseline_p95: 1,
      candidate_p95: 1.2,
      absolute_delta: 0.2,
      percent_delta: 20,
    },
    phases: {},
    pairs: [comparison.pairs[0]],
    pair_deltas: {},
  },
  gate: {
    schema_version: 1,
    verdict: 'failed',
    thresholds: { max_regressions: 0 },
    observed: { max_regressions: 1 },
    reasons: [{
      metric: 'max_regressions',
      reason: 'threshold_exceeded',
      threshold: 0,
      observed: 1,
    }],
  },
  created_at: completedRun.ended_at,
};

function replay(laneKey: 'baseline' | 'candidate') {
  const baseline = laneKey === 'baseline';
  return {
    run_id: completedRun.id,
    episode_key: episodeKey,
    lane_key: laneKey,
    attempt_no: 1,
    episode_attempt_id: baseline
      ? 'episode-attempt-baseline-ep06'
      : 'episode-attempt-candidate-ep06',
    artifact_root: `lanes/${laneKey}/trajectory/fake_ExecutionComparison`,
    outcome: baseline ? 'PASS' : 'FAIL',
    error_code: baseline ? null : 'ASSERTION_FAILURE',
    result: { is_success: baseline },
    steps: [{
      step: 1,
      route: { app: 'fake', path: '/' },
      action_type: 'COMPLETE',
      action_data: { return: baseline ? 'pass' : 'fail' },
      thought: `execute frozen ${laneKey} subject`,
      explain: '',
      summary: '',
      screenshot_artifact_id: `${laneKey}-raw-ep06`,
      screenshot_annotated_artifact_id: `${laneKey}-annotated-ep06`,
      model_response_artifact_id: `${laneKey}-response-ep06`,
      model_prompt_artifact_id: `${laneKey}-prompt-ep06`,
    }],
  };
}

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

describe('Test Platform Execution Comparison', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/run-launch');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('locks one Target Revision, binds two Profile Revisions, and previews their diff', async () => {
    const requests: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
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
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [workflow], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: [target], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({
          items: [baselineProfile, candidateProfile],
          next_cursor: null,
        });
      }
      if (url.pathname.endsWith('/run-launch/preview')) {
        requests.push(JSON.parse(String(init?.body)));
        return jsonResponse(preview);
      }
      if (url.pathname.endsWith('/run-launch') && init?.method === 'POST') {
        return jsonResponse(run, 201);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}/comparison`) {
        return jsonResponse({
          error: { code: 'COMPARISON_NOT_FOUND', message: 'Not recorded yet.' },
        }, 404);
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    const comparisonIntent = await screen.findByLabelText(
      'Comparison intent',
    ) as HTMLSelectElement;
    fireEvent.change(comparisonIntent, { target: { value: 'execution_comparison' } });

    expect(screen.getByLabelText('Shared Target Revision')).toBeTruthy();
    expect(screen.getByLabelText('Baseline Execution Profile Revision')).toBeTruthy();
    expect(screen.getByLabelText('Candidate Execution Profile Revision')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Preview launch' }));

    await waitFor(() => expect(requests).toHaveLength(1));
    expect(requests[0]).toEqual({
      workflow_version_id: workflowVersion.id,
      name: workflowVersion.definition.name,
      seed: 20260715,
      comparison_intent: 'execution_comparison',
      lane_bindings: [
        {
          lane_slot: 'baseline',
          target_revision_id: targetRevision.id,
          execution_profile_revision_id: baselineProfile.head_revision.id,
        },
        {
          lane_slot: 'candidate',
          target_revision_id: targetRevision.id,
          execution_profile_revision_id: candidateProfile.head_revision.id,
        },
      ],
    });
    expect(await screen.findByText('Execution Profile Revision diff')).toBeTruthy();
    expect(screen.getByText('model.name')).toBeTruthy();
    expect(screen.getByText('deterministic-profile-pass')).toBeTruthy();
    expect(screen.getByText('deterministic-profile-fail')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Create run' }));
    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    expect(screen.getByText(baselineProfile.name)).toBeTruthy();
    expect(screen.getByText(candidateProfile.name)).toBeTruthy();
    expect(screen.getByText(baselineProfile.head_revision.id, { exact: false })).toBeTruthy();
    expect(screen.getByText(candidateProfile.head_revision.id, { exact: false })).toBeTruthy();
    expect(screen.getAllByText(targetRevision.id).length).toBeGreaterThan(0);

    cleanup();
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(baselineProfile.head_revision.id, { exact: false })).toBeTruthy();
      expect(screen.getByText(candidateProfile.head_revision.id, { exact: false })).toBeTruthy();
    });
  });

  it('keeps both exact Profile identities while inspecting gate, replay, and incident state', async () => {
    window.history.replaceState({}, '', `/test-platform/runs/${completedRun.id}`);
    const writeText = vi.fn(async (_value: string) => undefined);
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
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
      if (url.pathname === `/api/platform/v1/runs/${completedRun.id}`) {
        return jsonResponse(completedRun);
      }
      if (url.pathname === `/api/platform/v1/runs/${completedRun.id}/comparison`) {
        return jsonResponse(comparison);
      }
      if (url.pathname === `/api/platform/v1/runs/${completedRun.id}/report`) {
        return jsonResponse(report);
      }
      if (url.pathname === `/api/platform/v1/runs/${completedRun.id}/diagnostics`) {
        return jsonResponse({
          schema_version: 1,
          run_id: completedRun.id,
          run_attempt_id: 'run-attempt-ep06',
          input_hash: 'sha256:diagnostics-ep06',
          provenance: report.provenance,
          summary: { total: 0, by_category: {}, by_severity: {} },
          items: [],
          next_cursor: null,
        });
      }
      if (url.pathname === `/api/platform/v1/runs/${completedRun.id}/artifacts`) {
        return jsonResponse({ items: [] });
      }
      if (
        url.pathname === `/api/platform/v1/runs/${completedRun.id}/retry/preview`
        || url.pathname === `/api/platform/v1/runs/${completedRun.id}/resume/preview`
      ) {
        const kind = url.pathname.includes('/retry/') ? 'retry' : 'resume';
        return jsonResponse({
          schema_version: 1,
          run_id: completedRun.id,
          kind,
          source_run_attempt_id: 'run-attempt-ep06',
          source_attempt_no: 1,
          preview_token: `preview-${kind}-ep06`,
          can_execute: false,
          empty_reason: 'No lane episodes are selected.',
          selected_lane_episodes: [],
        });
      }
      if (url.pathname === `/api/platform/v1/runs/${completedRun.id}/baseline/eligibility`) {
        return jsonResponse({
          run_id: completedRun.id,
          run_attempt_id: 'run-attempt-ep06',
          lane_key: url.searchParams.get('lane_key'),
          eligible: false,
          counts: { planned: 1, pass: 1, fail: 0, error: 0, cancelled: 0, incomplete: 0 },
          reasons: [{
            code: 'PAIRED_RUN_NOT_ELIGIBLE',
            message: 'Comparison runs are not strict baseline sources.',
            details: {},
          }],
        });
      }
      if (
        decodeURIComponent(url.pathname).includes(
          `/api/platform/v1/runs/${completedRun.id}/episodes/${episodeKey}/replay`,
        )
      ) {
        const laneKey = url.searchParams.get('lane_key');
        expect(laneKey === 'baseline' || laneKey === 'candidate').toBe(true);
        return jsonResponse(replay(laneKey as 'baseline' | 'candidate'));
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByTestId('tp-comparison')).toBeTruthy();
    expect(screen.getByTestId('tp-gate-verdict').textContent).toBe('failed');
    expect(screen.getByText(baselineProfile.name)).toBeTruthy();
    expect(screen.getByText(candidateProfile.name)).toBeTruthy();
    expect(screen.getByText(baselineProfile.head_revision.id, { exact: false })).toBeTruthy();
    expect(screen.getByText(candidateProfile.head_revision.id, { exact: false })).toBeTruthy();

    const picker = await screen.findByLabelText('Replay episode') as HTMLSelectElement;
    expect(picker.selectedOptions[0]?.textContent).toContain('candidate');
    const baselineOption = Array.from(picker.options).find((option) =>
      option.textContent?.includes('baseline')
    );
    expect(baselineOption).toBeTruthy();
    fireEvent.change(picker, { target: { value: baselineOption!.value } });
    await waitFor(() => {
      expect(new URL(window.location.href).searchParams.get('lane')).toBe('baseline');
    });
    expect(screen.getByText(baselineProfile.head_revision.id, { exact: false })).toBeTruthy();
    expect(screen.getByText(candidateProfile.head_revision.id, { exact: false })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Copy incident link' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const incident = new URL(String(writeText.mock.calls[0]?.[0]));
    expect(incident.searchParams.get('lane')).toBe('baseline');
    expect(incident.searchParams.get('episode')).toBe(episodeKey);
    expect(incident.searchParams.get('attempt')).toBe('1');
    expect(incident.toString()).not.toContain(baselineProfile.head_revision.id);
    expect(incident.toString()).not.toContain(candidateProfile.head_revision.id);
    expect(incident.toString()).not.toContain('secret');
  });
});
