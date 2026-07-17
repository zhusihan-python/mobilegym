import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

const target = {
  id: 'target-1',
  project_id: project.id,
  name: 'Local simulator',
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
  latest_revision: {
    id: 'revision-1',
    metadata_hash: 'metadata-vs04',
    health_status: 'healthy',
    resolved_at: '2026-07-03T00:00:01.000Z',
    warnings: [],
    metadata: {},
  },
  created_at: '2026-07-03T00:00:00.000Z',
  updated_at: '2026-07-03T00:00:00.000Z',
};

const definition = {
  schema_version: 2 as const,
  name: 'WeChat smoke',
  nodes: [
    {
      id: 'tasks',
      type: 'task_selection' as const,
      depends_on: [],
      config: { task_ids: ['wechat.OpenBlacklist'], sample_n: 1 },
    },
    {
      id: 'matrix',
      type: 'matrix' as const,
      depends_on: ['tasks'],
      config: { lane_slots: { candidate: { role: 'candidate' } }, repeat_n: 2 },
    },
    {
      id: 'execute',
      type: 'execute' as const,
      depends_on: ['matrix'],
      config: { parallel: 1 },
    },
  ],
};

const version = {
  id: 'version-1',
  workflow_id: 'workflow-1',
  version_no: 1,
  status: 'published' as const,
  definition,
  definition_hash: 'sha256:workflow',
  created_at: '2026-07-03T00:00:02.000Z',
  published_at: '2026-07-03T00:00:02.000Z',
};

const workflow = {
  id: 'workflow-1',
  project_id: project.id,
  name: 'WeChat smoke',
  draft_definition: definition,
  latest_version: version,
  created_at: '2026-07-03T00:00:02.000Z',
  updated_at: '2026-07-03T00:00:02.000Z',
};

const profileSpec = {
  schema_version: 1 as const,
  agent: { id: 'generic_v2' as const },
  model: {
    protocol: 'openai_chat_completions' as const,
    base_url: 'http://127.0.0.1:1234/v1',
    name: 'reviewed-model',
  },
  image_input: { format: 'data_url' as const },
  generation: { temperature: 0, top_p: 1, max_tokens: 4096, stream: true },
  inference: { timeout_seconds: 300 },
  credentials: { required_slots: [] },
};

const profileRevision = {
  id: 'profile-revision-1',
  execution_profile_id: 'profile-1',
  revision_no: 1,
  public_spec: profileSpec,
  public_spec_hash: 'sha256:profile-public-1',
  credential_binding_digest: 'sha256:empty',
  credential_readiness: {
    required_slots: [],
    bound_slots: [],
    missing_slots: [],
    ready: true,
    binding_digest: 'sha256:empty',
  },
  published_at: '2026-07-03T00:00:02.000Z',
};

const profile = {
  id: profileRevision.execution_profile_id,
  project_id: project.id,
  name: 'Reviewed generic v2',
  draft_spec: profileSpec,
  credential_readiness: profileRevision.credential_readiness,
  draft_version: 1,
  head_revision: profileRevision,
  archived_at: null,
  created_at: '2026-07-03T00:00:02.000Z',
  updated_at: '2026-07-03T00:00:02.000Z',
};

const run = {
  id: 'run-1',
  project_id: project.id,
  workflow_version_id: version.id,
  name: 'WeChat smoke',
  state: 'queued',
  fingerprint: 'sha256:run-plan-vs04',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 2,
    completed_episodes: 0,
  },
  lanes: [
    {
      id: 'lane-1',
      lane_key: 'candidate',
      role: 'candidate',
      target_id: target.id,
      target_revision_id: target.latest_revision.id,
    },
  ],
  target_revisions: [
    {
      target_id: target.id,
      target_revision_id: target.latest_revision.id,
      metadata_hash: target.latest_revision.metadata_hash,
    },
  ],
  episode_identities: [
    {
      episode_key: 'wechat.OpenBlacklist|i0|s123|r1|t0',
      pair_key: 'wechat.OpenBlacklist|i0|s123|r1|t0',
      task_base_id: 'wechat.OpenBlacklist',
      task_id: 'wechat.OpenBlacklist',
      instance_id: 0,
      instance_seed: 123,
      template_index: null,
      trial_id: 0,
      max_steps: 15,
      sequence_index: null,
      sequence_group_id: null,
    },
    {
      episode_key: 'wechat.OpenBlacklist|i0|s123|r1|t1',
      pair_key: 'wechat.OpenBlacklist|i0|s123|r1|t1',
      task_base_id: 'wechat.OpenBlacklist',
      task_id: 'wechat.OpenBlacklist',
      instance_id: 0,
      instance_seed: 123,
      template_index: null,
      trial_id: 1,
      max_steps: 15,
      sequence_index: null,
      sequence_group_id: null,
    },
  ],
  run_plan: {},
  execution_identity: legacyExecutionIdentity,
  gate_verdict: null,
  created_at: '2026-07-03T00:00:03.000Z',
  started_at: null,
  ended_at: null,
};

const manualSequenceRun = {
  ...run,
  id: 'run-manual-sequence',
  name: 'Manual sequence run',
  fingerprint: 'sha256:manual-sequence-run',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 2,
    completed_episodes: 0,
    completed_lane_episodes: 0,
  },
  episode_identities: [
    {
      episode_key: 'wechat.OpenBlacklist|i0|s501|r1|t0',
      pair_key: 'wechat.OpenBlacklist|i0|s501|r1|t0',
      task_base_id: 'wechat.OpenBlacklist',
      task_id: 'wechat.OpenBlacklist',
      instance_id: 0,
      instance_seed: 501,
      template_index: null,
      trial_id: 0,
      max_steps: 15,
      sequence_index: 0,
      sequence_group_id: 'manual_sequence',
    },
    {
      episode_key: 'wechat.BlacklistContact|i0|s502|r1|t0',
      pair_key: 'wechat.BlacklistContact|i0|s502|r1|t0',
      task_base_id: 'wechat.BlacklistContact',
      task_id: 'wechat.BlacklistContact',
      instance_id: 0,
      instance_seed: 502,
      template_index: null,
      trial_id: 0,
      max_steps: 20,
      sequence_index: 1,
      sequence_group_id: 'manual_sequence',
    },
  ],
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

describe('Test Platform immutable run planning', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/workflows');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('launches exact published revisions and shows the queued frozen run overview', async () => {
    window.history.pushState({}, '', '/test-platform/run-launch');
    const requests: Array<{
      path: string;
      method: string;
      headers: Headers;
      body?: any;
    }> = [];
    const profileAwareRun = {
      ...run,
      run_plan: { schema_version: 2 },
      lanes: [{
        ...run.lanes[0],
        execution_profile_revision_id: profileRevision.id,
        execution_profile_revision_hash: 'sha256:profile-revision-1',
        lane_fingerprint: 'sha256:lane-1',
      }],
      execution_identity: {
        kind: 'profile_aware',
        label: 'Execution Profile Revision',
        schema_version: 2,
        lane_bindings: [{
          lane_slot: 'candidate',
          target_revision_id: target.latest_revision.id,
          target_revision_hash: target.latest_revision.metadata_hash,
          execution_profile_id: profile.id,
          execution_profile_name: profile.name,
          execution_profile_revision_id: profileRevision.id,
          execution_profile_revision_no: 1,
          execution_profile_public_hash: profileRevision.public_spec_hash,
          execution_profile_revision_hash: 'sha256:profile-revision-1',
          lane_fingerprint: 'sha256:lane-1',
        }],
      },
    };
    const preview = {
      workflow_version_id: version.id,
      workflow_version_hash: version.definition_hash,
      comparison_intent: 'single',
      lane_bindings: [{
        lane_slot: 'candidate',
        role: 'candidate',
        target_id: target.id,
        target_revision_id: target.latest_revision.id,
        target_revision_hash: target.latest_revision.metadata_hash,
        execution_profile_id: profile.id,
        execution_profile_name: profile.name,
        execution_profile_revision_id: profileRevision.id,
        execution_profile_revision_no: 1,
        execution_profile_public_hash: profileRevision.public_spec_hash,
        execution_profile_revision_hash: 'sha256:profile-revision-1',
        lane_fingerprint: 'sha256:lane-1',
      }],
      constraint_violations: [],
      episode_count: 2,
      fingerprint_inputs: {},
      run_plan_fingerprint: run.fingerprint,
      preview_token: 'sha256:preview-1',
      credential_requirements: [],
    };
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      requests.push({
        path: url.pathname,
        method: init?.method ?? 'GET',
        headers: new Headers(init?.headers),
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      });

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
        return jsonResponse({ items: [profile], next_cursor: null });
      }
      if (url.pathname.endsWith('/run-launch/preview')) {
        return jsonResponse(preview);
      }
      if (url.pathname.endsWith('/run-launch') && init?.method === 'POST') {
        return jsonResponse(profileAwareRun, 201);
      }
      if (url.pathname === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(profileAwareRun);
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect((await screen.findByLabelText('Target Revision') as HTMLSelectElement).value)
      .toBe(target.latest_revision.id);
    expect((screen.getByLabelText('Execution Profile Revision') as HTMLSelectElement).value)
      .toBe(profileRevision.id);
    fireEvent.click(screen.getByRole('button', { name: 'Preview launch' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Create run' }));

    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    expect(screen.getByTestId('tp-run-state').textContent).toBe('queued');
    expect(screen.getByText('2 planned episodes')).toBeTruthy();
    expect(screen.getByText('revision-1')).toBeTruthy();
    expect(screen.getByText('sha256:run-plan-vs04')).toBeTruthy();

    const launch = requests.find(
      (request) => request.path.endsWith('/run-launch') && request.method === 'POST',
    );
    expect(launch?.headers.get('Idempotency-Key')).toBeTruthy();
    expect(launch?.body).toMatchObject({
      workflow_version_id: version.id,
      lane_bindings: [{
        lane_slot: 'candidate',
        target_revision_id: target.latest_revision.id,
        execution_profile_revision_id: profileRevision.id,
      }],
    });
  });

  it('resets the launch workflow version when switching projects with same-named workflows', async () => {
    window.history.pushState({}, '', '/test-platform/run-launch');
    const otherProject = {
      ...project,
      id: 'project-2',
      name: 'Manual test',
      slug: 'manual-test',
    };
    const oldVersion = {
      ...version,
      id: 'version-old',
      definition_hash: 'sha256:dda7fe69a308ac0a535f47280937b79e93ea04578f50ceb8c0d3e6e187667699',
    };
    const newVersion = {
      ...version,
      id: 'version-new',
      definition_hash: 'sha256:f3dcc1a8a377a3115f78a9037c0dfd6a0ab13acca7fd888dc330c1e7ee8efe75',
    };
    const oldWorkflow = {
      ...workflow,
      latest_version: oldVersion,
    };
    const newWorkflow = {
      ...workflow,
      id: 'workflow-2',
      project_id: otherProject.id,
      latest_version: newVersion,
    };
    const otherTarget = {
      ...target,
      id: 'target-2',
      project_id: otherProject.id,
      latest_revision: {
        ...target.latest_revision,
        id: 'revision-2',
        metadata_hash: 'metadata-project-2',
      },
    };
    const otherProfileRevision = {
      ...profileRevision,
      id: 'profile-revision-2',
      execution_profile_id: 'profile-2',
    };
    const otherProfile = {
      ...profile,
      id: otherProfileRevision.execution_profile_id,
      project_id: otherProject.id,
      head_revision: otherProfileRevision,
    };

    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
        return jsonResponse({ items: [project, otherProject], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/workflows`) {
        return jsonResponse({ items: [oldWorkflow], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${otherProject.id}/workflows`) {
        return jsonResponse({ items: [newWorkflow], next_cursor: null });
      }
      if (url.pathname === '/api/platform/v1/targets') {
        return jsonResponse({
          items: url.searchParams.get('project_id') === otherProject.id
            ? [otherTarget]
            : [target],
          next_cursor: null,
        });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profiles`) {
        return jsonResponse({ items: [profile], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${otherProject.id}/execution-profiles`) {
        return jsonResponse({ items: [otherProfile], next_cursor: null });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Run Launch' })).toBeTruthy();
    const workflowSelect = await screen.findByLabelText('Workflow Version') as HTMLSelectElement;
    await waitFor(() => {
      expect(workflowSelect.value).toBe(oldVersion.id);
    });

    fireEvent.change(await screen.findByLabelText('Project'), {
      target: { value: otherProject.id },
    });

    await waitFor(() => {
      expect((screen.getByLabelText('Workflow Version') as HTMLSelectElement).value)
        .toBe(newVersion.id);
    });
    expect(screen.getByText(`WeChat smoke / v1 / ${newVersion.id}`)).toBeTruthy();
    expect((screen.getByLabelText('Target Revision') as HTMLSelectElement).value)
      .toBe(otherTarget.latest_revision.id);
    expect((screen.getByLabelText('Execution Profile Revision') as HTMLSelectElement).value)
      .toBe(otherProfileRevision.id);
  });

  it('shows manual sequence ordering metadata on run detail', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
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
      if (url.pathname === `/api/platform/v1/runs/${manualSequenceRun.id}`) {
        return jsonResponse(manualSequenceRun);
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));
    window.history.pushState({}, '', `/test-platform/runs/${manualSequenceRun.id}`);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Run overview' })).toBeTruthy();
    const identities = within(await screen.findByTestId('tp-episode-identities'));
    expect(identities.getByRole('columnheader', { name: 'Seq' })).toBeTruthy();
    expect(identities.getByRole('columnheader', { name: 'Group' })).toBeTruthy();
    expect(identities.getByRole('columnheader', { name: 'Task' })).toBeTruthy();

    const rows = identities.getAllByRole('row').slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      '1',
      'manual_sequence',
      'wechat.OpenBlacklist',
      'wechat.OpenBlacklist|i0|s501|r1|t0',
      '501',
      '0',
      '15',
    ]);
    expect(within(rows[1]).getAllByRole('cell').map((cell) => cell.textContent)).toEqual([
      '2',
      'manual_sequence',
      'wechat.BlacklistContact',
      'wechat.BlacklistContact|i0|s502|r1|t0',
      '502',
      '0',
      '20',
    ]);
  });
});
