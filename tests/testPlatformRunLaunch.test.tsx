import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

const project = {
  id: 'project-ep02',
  name: 'Profile-aware launch',
  slug: 'profile-aware-launch',
  archived_at: null,
  created_at: '2026-07-15T00:00:00.000Z',
  updated_at: '2026-07-15T00:00:00.000Z',
};

const workflowVersion = {
  id: 'workflow-version-ep02',
  workflow_id: 'workflow-ep02',
  version_no: 1,
  status: 'published',
  definition: {
    schema_version: 2,
    name: 'Profile-aware Single',
    nodes: [
      { id: 'tasks', type: 'task_selection', depends_on: [], config: {} },
      {
        id: 'slots',
        type: 'matrix',
        depends_on: ['tasks'],
        config: { lane_slots: { candidate: { role: 'candidate' } } },
      },
      { id: 'execute', type: 'execute', depends_on: ['slots'], config: {} },
    ],
  },
  definition_hash: 'sha256:workflow-ep02',
  created_at: '2026-07-15T00:00:01.000Z',
  published_at: '2026-07-15T00:00:01.000Z',
};

const workflow = {
  id: workflowVersion.workflow_id,
  project_id: project.id,
  name: 'Profile-aware Single',
  draft_definition: workflowVersion.definition,
  latest_version: workflowVersion,
  created_at: workflowVersion.created_at,
  updated_at: workflowVersion.created_at,
};

const targetRevision = {
  id: 'target-revision-ep02',
  metadata_hash: 'sha256:target-ep02',
  health_status: 'healthy',
  resolved_at: '2026-07-15T00:00:02.000Z',
  warnings: [],
  metadata: {},
};

const target = {
  id: 'target-ep02',
  project_id: project.id,
  name: 'Deterministic simulator',
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
  created_at: '2026-07-15T00:00:02.000Z',
  updated_at: '2026-07-15T00:00:02.000Z',
};

const publicSpec = {
  schema_version: 1 as const,
  agent: { id: 'generic_v2' as const },
  model: {
    protocol: 'openai_chat_completions' as const,
    base_url: 'http://127.0.0.1:1234/v1',
    name: 'deterministic-model',
  },
  image_input: { format: 'data_url' as const },
  generation: { temperature: 0, top_p: 1, max_tokens: 4096, stream: true },
  inference: { timeout_seconds: 300 },
  credentials: { required_slots: ['model_api_key'] },
};

const credentialReadiness = {
  required_slots: ['model_api_key'],
  bound_slots: ['model_api_key'],
  missing_slots: [],
  ready: true,
  binding_digest: 'sha256:credential-binding-ep03',
};

const profileRevision = {
  id: 'profile-revision-ep02',
  execution_profile_id: 'profile-ep02',
  revision_no: 1,
  public_spec: publicSpec,
  public_spec_hash: 'sha256:profile-public-ep02',
  credential_binding_digest: 'sha256:empty-binding',
  credential_readiness: credentialReadiness,
  published_at: '2026-07-15T00:00:03.000Z',
};

const profile = {
  id: profileRevision.execution_profile_id,
  project_id: project.id,
  name: 'Deterministic generic v2',
  draft_spec: publicSpec,
  credential_readiness: credentialReadiness,
  head_revision: profileRevision,
  archived_at: null,
  created_at: '2026-07-15T00:00:03.000Z',
  updated_at: '2026-07-15T00:00:03.000Z',
};

const laneIdentity = {
  lane_slot: 'candidate',
  target_revision_id: targetRevision.id,
  target_revision_hash: targetRevision.metadata_hash,
  execution_profile_id: profile.id,
  execution_profile_name: profile.name,
  execution_profile_revision_id: profileRevision.id,
  execution_profile_revision_no: 1,
  execution_profile_public_hash: profileRevision.public_spec_hash,
  execution_profile_revision_hash: 'sha256:profile-revision-ep02',
  lane_fingerprint: 'sha256:lane-ep02',
};

const preview = {
  workflow_version_id: workflowVersion.id,
  workflow_version_hash: workflowVersion.definition_hash,
  comparison_intent: 'single',
  lane_bindings: [{
    ...laneIdentity,
    role: 'candidate',
    target_id: target.id,
  }],
  constraint_violations: [],
  episode_count: 1,
  fingerprint_inputs: {},
  run_plan_fingerprint: 'sha256:run-plan-ep02',
  preview_token: 'sha256:preview-ep02',
  credential_requirements: ['model_api_key'],
};

const candidateTargetRevision = {
  ...targetRevision,
  id: 'target-revision-candidate-ep05',
  metadata_hash: 'sha256:target-candidate-ep05',
};

const candidateTarget = {
  ...target,
  id: 'target-candidate-ep05',
  name: 'Candidate deterministic simulator',
  config: {
    ...target.config,
    connection: { env_url: 'http://127.0.0.1:5174' },
  },
  latest_revision: candidateTargetRevision,
};

const targetComparisonWorkflowVersion = {
  ...workflowVersion,
  id: 'workflow-version-ep05',
  workflow_id: 'workflow-ep05',
  definition: {
    ...workflowVersion.definition,
    name: 'Profile-aware Target Comparison',
    nodes: [
      workflowVersion.definition.nodes[0],
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
      workflowVersion.definition.nodes[2],
      {
        id: 'compare',
        type: 'compare',
        depends_on: ['execute'],
        config: {
          target_constraints: ['same_app', 'same_device', 'same_data'],
        },
      },
    ],
  },
  definition_hash: 'sha256:workflow-ep05',
};

const targetComparisonWorkflow = {
  ...workflow,
  id: targetComparisonWorkflowVersion.workflow_id,
  name: 'Profile-aware Target Comparison',
  draft_definition: targetComparisonWorkflowVersion.definition,
  latest_version: targetComparisonWorkflowVersion,
};

const targetComparisonPreview = {
  ...preview,
  workflow_version_id: targetComparisonWorkflowVersion.id,
  workflow_version_hash: targetComparisonWorkflowVersion.definition_hash,
  comparison_intent: 'target_comparison',
  lane_bindings: [
    {
      ...laneIdentity,
      lane_slot: 'baseline',
      role: 'baseline',
      target_id: target.id,
      lane_fingerprint: 'sha256:lane-baseline-ep05',
    },
    {
      ...laneIdentity,
      lane_slot: 'candidate',
      role: 'candidate',
      target_id: candidateTarget.id,
      target_revision_id: candidateTargetRevision.id,
      target_revision_hash: candidateTargetRevision.metadata_hash,
      lane_fingerprint: 'sha256:lane-candidate-ep05',
    },
  ],
  run_plan_fingerprint: 'sha256:run-plan-ep05',
  preview_token: 'sha256:preview-ep05',
  credential_requirements: [],
};

const targetComparisonEpisodeKey = 'fake.TargetComparison|i0|s1|r1|t0';

const run = {
  id: 'run-ep02',
  project_id: project.id,
  workflow_version_id: workflowVersion.id,
  name: 'Profile-aware Single',
  state: 'queued',
  fingerprint: preview.run_plan_fingerprint,
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 1,
    completed_episodes: 0,
    completed_lane_episodes: 0,
  },
  outcome_counts: { pass: 0, fail: 0, error: 0, cancelled: 0, incomplete: 1 },
  lanes: [{
    id: 'lane-ep02',
    lane_key: 'candidate',
    role: 'candidate',
    target_id: target.id,
    target_revision_id: targetRevision.id,
    execution_profile_revision_id: profileRevision.id,
    execution_profile_revision_hash: laneIdentity.execution_profile_revision_hash,
    lane_fingerprint: laneIdentity.lane_fingerprint,
  }],
  run_plan: { schema_version: 2 },
  execution_identity: {
    kind: 'profile_aware',
    label: 'Execution Profile Revision',
    schema_version: 2,
    lane_bindings: [laneIdentity],
  },
  run_attempts: [{
    id: 'run-attempt-ep03',
    attempt_no: 1,
    reason: 'initial',
    state: 'queued',
    started_at: null,
    ended_at: null,
    compatibility: [{
      outcome: 'passed',
      code: 'compatible',
      explanation: 'The model accepted the screenshot request.',
      latency_ms: 42,
      cached: false,
      checked_model: publicSpec.model.name,
      checked_image_format: publicSpec.image_input.format,
      lane_keys: ['candidate'],
    }],
    created_at: '2026-07-15T00:00:04.000Z',
  }],
  lane_attempts: [],
  target_revisions: [{
    target_id: target.id,
    target_revision_id: targetRevision.id,
    metadata_hash: targetRevision.metadata_hash,
  }],
  episode_identities: [],
  episode_attempts: [],
  gate_verdict: null,
  created_at: '2026-07-15T00:00:04.000Z',
  started_at: null,
  ended_at: null,
};

const targetComparisonRun = {
  ...run,
  id: 'run-ep05',
  workflow_version_id: targetComparisonWorkflowVersion.id,
  name: 'Profile-aware Target Comparison',
  state: 'completed',
  fingerprint: targetComparisonPreview.run_plan_fingerprint,
  progress: {
    planned_episodes: 1,
    planned_lane_episodes: 2,
    completed_episodes: 1,
    completed_lane_episodes: 2,
  },
  outcome_counts: { pass: 1, fail: 1, error: 0, cancelled: 0, incomplete: 0 },
  lanes: targetComparisonPreview.lane_bindings.map((binding, index) => ({
    id: `lane-ep05-${index}`,
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
    comparison: { intent: 'target_comparison' },
  },
  execution_identity: {
    kind: 'profile_aware',
    label: 'Execution Profile Revision',
    schema_version: 2,
    lane_bindings: targetComparisonPreview.lane_bindings.map((binding) => ({
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
  run_attempts: [{
    ...run.run_attempts[0],
    state: 'completed',
    ended_at: '2026-07-15T00:00:06.000Z',
    lane_keys: ['baseline', 'candidate'],
    compatibility: [{
      ...run.run_attempts[0].compatibility[0],
      lane_keys: ['baseline', 'candidate'],
    }],
  }],
  target_revisions: [
    {
      target_id: target.id,
      target_revision_id: targetRevision.id,
      metadata_hash: targetRevision.metadata_hash,
    },
    {
      target_id: candidateTarget.id,
      target_revision_id: candidateTargetRevision.id,
      metadata_hash: candidateTargetRevision.metadata_hash,
    },
  ],
  episode_identities: [{
    episode_key: targetComparisonEpisodeKey,
    pair_key: targetComparisonEpisodeKey,
    task_base_id: 'fake.TargetComparison',
    task_id: 'fake.TargetComparison',
    instance_id: 0,
    instance_seed: 1,
    template_index: null,
    trial_id: 0,
    max_steps: 15,
    sequence_index: null,
    sequence_group_id: null,
  }],
  episode_attempts: [
    {
      episode_key: targetComparisonEpisodeKey,
      lane_key: 'baseline',
      run_attempt_id: 'run-attempt-ep03',
      episode_attempt_id: 'episode-attempt-baseline-ep05',
      attempt_no: 1,
      state: 'completed',
      outcome: 'PASS',
      error_code: null,
      artifact_root: 'lanes/baseline/trajectory/fake_TargetComparison',
    },
    {
      episode_key: targetComparisonEpisodeKey,
      lane_key: 'candidate',
      run_attempt_id: 'run-attempt-ep03',
      episode_attempt_id: 'episode-attempt-candidate-ep05',
      attempt_no: 1,
      state: 'completed',
      outcome: 'FAIL',
      error_code: 'ASSERTION_FAILURE',
      artifact_root: 'lanes/candidate/trajectory/fake_TargetComparison',
    },
  ],
  gate_verdict: 'failed',
  ended_at: '2026-07-15T00:00:06.000Z',
};

const targetComparisonPair = {
  pair_key: targetComparisonEpisodeKey,
  classification: 'regression',
  baseline_episode_attempt_id: 'episode-attempt-baseline-ep05',
  candidate_episode_attempt_id: 'episode-attempt-candidate-ep05',
  delta: { baseline_outcome: 'PASS', candidate_outcome: 'FAIL' },
};

const targetComparison = {
  id: 'comparison-ep05',
  run_id: targetComparisonRun.id,
  run_attempt_id: 'run-attempt-ep03',
  baseline_lane_id: 'lane-ep05-0',
  candidate_lane_id: 'lane-ep05-1',
  policy: { target_constraints: ['same_app', 'same_device', 'same_data'] },
  summary: { regressions: 1 },
  created_at: '2026-07-15T00:00:06.000Z',
  pairs: [{
    id: 'comparison-pair-ep05',
    comparison_id: 'comparison-ep05',
    ...targetComparisonPair,
    integrity: {
      status: 'ok',
      prepared_projection_hash: 'sha256:prepared-ep05',
      baseline_actual_projection_hash: 'sha256:prepared-ep05',
      candidate_actual_projection_hash: 'sha256:prepared-ep05',
    },
    prepared: {
      params: { task_id: 'fake.TargetComparison' },
      instruction: 'Verify the deterministic target comparison.',
      projection_hash: 'sha256:prepared-ep05',
    },
  }],
};

const targetComparisonReport = {
  id: 'report-ep05',
  schema_version: 1,
  run_id: targetComparisonRun.id,
  run_attempt_id: 'run-attempt-ep03',
  input_hash: 'sha256:report-input-ep05',
  provenance: {
    project_id: project.id,
    run_id: targetComparisonRun.id,
    run_attempt_id: 'run-attempt-ep03',
    workflow_version_id: targetComparisonWorkflowVersion.id,
    run_plan_hash: targetComparisonPreview.run_plan_fingerprint,
    task_source_digest: 'sha256:tasks-ep05',
    target_revision_ids: {
      baseline: targetRevision.id,
      candidate: candidateTargetRevision.id,
    },
  },
  functional: {
    summary: { success_rate: 0.5 },
    lanes: {},
    taxonomy: {},
  },
  performance: {
    summary: {
      unit: 'seconds',
      runtime_s: {},
      phases: {},
      excluded: {},
    },
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
    pairs: [targetComparisonPair],
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
  created_at: '2026-07-15T00:00:06.000Z',
};

function targetComparisonReplay(laneKey: 'baseline' | 'candidate') {
  const isBaseline = laneKey === 'baseline';
  return {
    run_id: targetComparisonRun.id,
    episode_key: targetComparisonEpisodeKey,
    lane_key: laneKey,
    attempt_no: 1,
    episode_attempt_id: isBaseline
      ? 'episode-attempt-baseline-ep05'
      : 'episode-attempt-candidate-ep05',
    artifact_root: `lanes/${laneKey}/trajectory/fake_TargetComparison`,
    outcome: isBaseline ? 'PASS' : 'FAIL',
    error_code: isBaseline ? null : 'ASSERTION_FAILURE',
    result: { is_success: isBaseline },
    steps: [{
      step: 1,
      route: { app: 'demo', path: '/' },
      action_type: 'CLICK',
      action_data: { point: [0.5, 0.5] },
      thought: `inspect the ${laneKey} target`,
      explain: '',
      summary: '',
      screenshot_artifact_id: `${laneKey}-raw-ep05`,
      screenshot_annotated_artifact_id: `${laneKey}-annotated-ep05`,
      model_response_artifact_id: `${laneKey}-response-ep05`,
      model_prompt_artifact_id: `${laneKey}-prompt-ep05`,
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

describe('Test Platform profile-aware Run Launch', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/run-launch');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('previews, creates, navigates, and reloads exact revision identity', async () => {
    const requests: Array<{ path: string; method: string; body?: unknown; headers: Headers }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const method = init?.method ?? 'GET';
      requests.push({
        path: url.pathname,
        method,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
        headers: new Headers(init?.headers),
      });

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
        return jsonResponse({ items: [profile], next_cursor: null });
      }
      if (url.pathname.endsWith('/run-launch/preview')) {
        return jsonResponse(preview);
      }
      if (url.pathname.endsWith('/run-launch') && method === 'POST') {
        return jsonResponse(run, 201);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Run Launch' })).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'Preview launch' }));

    expect(await screen.findByText(preview.run_plan_fingerprint)).toBeTruthy();
    expect(screen.getByText(profileRevision.public_spec_hash)).toBeTruthy();
    expect(screen.getByText(laneIdentity.lane_fingerprint)).toBeTruthy();
    const staleSecret = 'sk-cleared-preview-sentinel';
    const initialSecretInput = screen.getByLabelText('Model API key');
    fireEvent.change(initialSecretInput, { target: { value: staleSecret } });
    fireEvent.change(screen.getByLabelText('Seed'), {
      target: { value: '20260716' },
    });
    expect(screen.queryByLabelText('Model API key')).toBeNull();
    fireEvent.change(screen.getByLabelText('Seed'), {
      target: { value: '20260715' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Preview launch' }));
    const secretInput = await screen.findByLabelText('Model API key');
    expect((secretInput as HTMLInputElement).value).toBe('');

    const secret = 'sk-console-transient-sentinel';
    expect(secretInput.getAttribute('type')).toBe('password');
    fireEvent.change(secretInput, { target: { value: secret } });
    fireEvent.click(screen.getByRole('button', { name: 'Create run' }));

    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    expect(screen.getByText(workflowVersion.id)).toBeTruthy();
    expect(screen.getAllByText('Execution Profile Revision')).not.toHaveLength(0);
    expect(screen.getByText(profile.name)).toBeTruthy();
    expect(screen.getByText(profileRevision.id, { exact: false })).toBeTruthy();
    expect(screen.getByText(targetRevision.id)).toBeTruthy();
    expect(screen.getByText(laneIdentity.lane_fingerprint)).toBeTruthy();
    expect(screen.getByText('Compatibility Preflight')).toBeTruthy();
    expect(screen.getByText(publicSpec.model.name)).toBeTruthy();

    const previewRequest = requests.find((item) => item.path.endsWith('/run-launch/preview'));
    expect(previewRequest?.body).toEqual({
      workflow_version_id: workflowVersion.id,
      name: 'Profile-aware Single',
      seed: 20260715,
      comparison_intent: 'single',
      lane_bindings: [{
        lane_slot: 'candidate',
        target_revision_id: targetRevision.id,
        execution_profile_revision_id: profileRevision.id,
      }],
    });
    const createRequest = requests.find(
      (item) => item.path.endsWith('/run-launch') && !item.path.endsWith('/preview'),
    );
    expect(createRequest?.headers.get('Idempotency-Key')).toBeTruthy();
    expect(createRequest?.body).toEqual({
      ...(previewRequest?.body as object),
      preview_token: preview.preview_token,
      secret_bindings: { model_api_key: secret },
    });
    const persistedBrowserState = Array.from(
      { length: window.localStorage.length },
      (_, index) => window.localStorage.getItem(window.localStorage.key(index) ?? ''),
    ).join('\n');
    expect(persistedBrowserState).not.toContain(secret);
    expect(persistedBrowserState).not.toContain(staleSecret);

    cleanup();
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(workflowVersion.id)).toBeTruthy();
      expect(screen.getByText(profileRevision.id, { exact: false })).toBeTruthy();
      expect(screen.getByText(laneIdentity.lane_fingerprint)).toBeTruthy();
    });
  });

  it('remembers only the recent Execution Profile Revision identity', async () => {
    const alternateRevision = {
      ...profileRevision,
      id: 'profile-revision-ep09',
      execution_profile_id: 'profile-ep09',
      revision_no: 2,
      public_spec_hash: 'sha256:profile-public-ep09',
    };
    const alternateProfile = {
      ...profile,
      id: alternateRevision.execution_profile_id,
      name: 'Reviewed candidate profile',
      head_revision: alternateRevision,
    };
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
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [workflow], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: [target], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({ items: [profile, alternateProfile], next_cursor: null });
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    const profileSelect = await screen.findByLabelText(
      'Execution Profile Revision',
    ) as HTMLSelectElement;
    fireEvent.change(profileSelect, { target: { value: alternateRevision.id } });

    expect(
      window.localStorage.getItem('test-platform.run-launch.recent-profile-revision-id'),
    ).toBe(alternateRevision.id);
    const launchPreferenceKeys = Array.from(
      { length: window.localStorage.length },
      (_, index) => window.localStorage.key(index),
    ).filter((key): key is string => Boolean(key?.includes('launch')));
    expect(launchPreferenceKeys).toEqual([
      'test-platform.run-launch.recent-profile-revision-id',
    ]);

    cleanup();
    render(<App />);

    await waitFor(() => {
      expect((screen.getByLabelText('Execution Profile Revision') as HTMLSelectElement).value)
        .toBe(alternateRevision.id);
    });
  });

  it('previews, creates, reloads, and investigates a Target Comparison', async () => {
    const requests: Array<{ path: string; body?: any }> = [];
    const writeText = vi.fn(async (_value: string) => undefined);
    Object.defineProperty(window.navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      requests.push({ path: url.pathname, body });

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
        return jsonResponse({ items: [targetComparisonWorkflow], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({ items: [target, candidateTarget], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({ items: [profile], next_cursor: null });
      }
      if (url.pathname.endsWith('/run-launch/preview')) {
        return jsonResponse(targetComparisonPreview);
      }
      if (url.pathname.endsWith('/run-launch') && init?.method === 'POST') {
        return jsonResponse(targetComparisonRun, 201);
      }
      if (url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}`) {
        return jsonResponse(targetComparisonRun);
      }
      if (url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}/comparison`) {
        return jsonResponse(targetComparison);
      }
      if (url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}/report`) {
        return jsonResponse(targetComparisonReport);
      }
      if (url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}/diagnostics`) {
        return jsonResponse({
          run_id: targetComparisonRun.id,
          provenance: targetComparisonReport.provenance,
          summary: { total: 0, by_category: {}, by_severity: {} },
          items: [],
          next_cursor: null,
        });
      }
      if (url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}/artifacts`) {
        return jsonResponse({ items: [] });
      }
      if (url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}/baseline/eligibility`) {
        return jsonResponse({
          run_id: targetComparisonRun.id,
          run_attempt_id: 'run-attempt-ep03',
          lane_key: url.searchParams.get('lane_key'),
          eligible: false,
          counts: { planned: 1, pass: 1, fail: 0, error: 0, cancelled: 0, incomplete: 0 },
          reasons: [{
            code: 'PAIRED_RUN_NOT_ELIGIBLE',
            message: 'Target Comparison runs are not strict baseline sources.',
            details: {},
          }],
        });
      }
      if (
        url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}/retry/preview`
        || url.pathname === `/api/platform/v1/runs/${targetComparisonRun.id}/resume/preview`
      ) {
        const kind = url.pathname.includes('/retry/') ? 'retry' : 'resume';
        return jsonResponse({
          schema_version: 1,
          run_id: targetComparisonRun.id,
          kind,
          source_run_attempt_id: 'run-attempt-ep03',
          source_attempt_no: 1,
          preview_token: `preview-${kind}-ep05`,
          can_execute: false,
          empty_reason: 'No lane episodes are selected.',
          selected_lane_episodes: [],
        });
      }
      if (
        decodeURIComponent(url.pathname).includes(
          `/api/platform/v1/runs/${targetComparisonRun.id}/episodes/${targetComparisonEpisodeKey}/replay`,
        )
      ) {
        const laneKey = url.searchParams.get('lane_key');
        expect(laneKey === 'baseline' || laneKey === 'candidate').toBe(true);
        expect(url.searchParams.get('attempt_no')).toBe('1');
        return jsonResponse(targetComparisonReplay(laneKey as 'baseline' | 'candidate'));
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Run Launch' })).toBeTruthy();
    expect((await screen.findByLabelText('Comparison intent') as HTMLSelectElement).value).toBe(
      'target_comparison',
    );
    expect((screen.getByLabelText('Baseline Target Revision') as HTMLSelectElement).value).toBe(
      targetRevision.id,
    );
    expect((screen.getByLabelText('Candidate Target Revision') as HTMLSelectElement).value).toBe(
      candidateTargetRevision.id,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Preview launch' }));

    expect(await screen.findByText('sha256:lane-baseline-ep05')).toBeTruthy();
    expect(screen.getByText('sha256:lane-candidate-ep05')).toBeTruthy();
    const previewRequest = requests.find((item) => item.path.endsWith('/preview'));
    expect(previewRequest?.body).toEqual({
      workflow_version_id: targetComparisonWorkflowVersion.id,
      name: targetComparisonWorkflowVersion.definition.name,
      seed: 20260715,
      comparison_intent: 'target_comparison',
      lane_bindings: [
        {
          lane_slot: 'baseline',
          target_revision_id: targetRevision.id,
          execution_profile_revision_id: profileRevision.id,
        },
        {
          lane_slot: 'candidate',
          target_revision_id: candidateTargetRevision.id,
          execution_profile_revision_id: profileRevision.id,
        },
      ],
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create run' }));
    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    expect(screen.getByText(targetRevision.id)).toBeTruthy();
    expect(screen.getByText(candidateTargetRevision.id)).toBeTruthy();
    expect(screen.getAllByText(profileRevision.id, { exact: false }).length).toBeGreaterThan(1);
    expect(screen.getByText('sha256:lane-baseline-ep05')).toBeTruthy();
    expect(screen.getByText('sha256:lane-candidate-ep05')).toBeTruthy();

    cleanup();
    render(<App />);

    expect(await screen.findByTestId('tp-comparison')).toBeTruthy();
    expect(screen.getByTestId('tp-gate-verdict').textContent).toBe('failed');
    expect(screen.getByText(targetRevision.id)).toBeTruthy();
    expect(screen.getByText(candidateTargetRevision.id)).toBeTruthy();
    expect(screen.getAllByText(profileRevision.id, { exact: false }).length).toBeGreaterThan(1);

    const replayPicker = await screen.findByLabelText('Replay episode') as HTMLSelectElement;
    expect(replayPicker.selectedOptions[0]?.textContent).toContain('candidate');
    const baselineOption = Array.from(replayPicker.options).find((option) =>
      option.textContent?.includes('baseline')
    );
    expect(baselineOption).toBeTruthy();
    fireEvent.change(replayPicker, { target: { value: baselineOption!.value } });
    await waitFor(() => {
      expect(new URL(window.location.href).searchParams.get('lane')).toBe('baseline');
    });
    expect(screen.getByText(targetRevision.id)).toBeTruthy();
    expect(screen.getByText(candidateTargetRevision.id)).toBeTruthy();
    expect(screen.getAllByText(profileRevision.id, { exact: false }).length).toBeGreaterThan(1);

    fireEvent.click(screen.getByRole('button', { name: 'Copy incident link' }));
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1));
    const copied = new URL(String(writeText.mock.calls[0]?.[0]));
    expect(Object.fromEntries(copied.searchParams)).toMatchObject({
      lane: 'baseline',
      episode: targetComparisonEpisodeKey,
      attempt: '1',
    });
  });
});
