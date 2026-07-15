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
  credentials: { required_slots: [] },
};

const profileRevision = {
  id: 'profile-revision-ep02',
  execution_profile_id: 'profile-ep02',
  revision_no: 1,
  public_spec: publicSpec,
  public_spec_hash: 'sha256:profile-public-ep02',
  credential_binding_digest: 'sha256:empty-binding',
  published_at: '2026-07-15T00:00:03.000Z',
};

const profile = {
  id: profileRevision.execution_profile_id,
  project_id: project.id,
  name: 'Deterministic generic v2',
  draft_spec: publicSpec,
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
  episode_count: 1,
  fingerprint_inputs: {},
  run_plan_fingerprint: 'sha256:run-plan-ep02',
  preview_token: 'sha256:preview-ep02',
  credential_requirements: [],
};

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
  created_at: '2026-07-15T00:00:04.000Z',
  started_at: null,
  ended_at: null,
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
    fireEvent.click(screen.getByRole('button', { name: 'Create run' }));

    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    expect(screen.getByText(workflowVersion.id)).toBeTruthy();
    expect(screen.getAllByText('Execution Profile Revision')).not.toHaveLength(0);
    expect(screen.getByText(profile.name)).toBeTruthy();
    expect(screen.getByText(profileRevision.id, { exact: false })).toBeTruthy();
    expect(screen.getByText(targetRevision.id)).toBeTruthy();
    expect(screen.getByText(laneIdentity.lane_fingerprint)).toBeTruthy();

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
    });

    cleanup();
    render(<App />);
    await waitFor(() => {
      expect(screen.getByText(workflowVersion.id)).toBeTruthy();
      expect(screen.getByText(profileRevision.id, { exact: false })).toBeTruthy();
      expect(screen.getByText(laneIdentity.lane_fingerprint)).toBeTruthy();
    });
  });
});
