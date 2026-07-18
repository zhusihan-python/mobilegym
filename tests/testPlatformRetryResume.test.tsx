import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import type { RunDetail } from '../web/test-platform/api/types';
import { legacyExecutionIdentity } from './testPlatformFixtures';

const project = {
  id: 'project-1',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-06T00:00:00.000Z',
  updated_at: '2026-07-06T00:00:00.000Z',
};

const run: RunDetail = {
  id: 'run-vs13',
  project_id: project.id,
  workflow_version_id: 'version-1',
  name: 'VS-13 retry resume',
  state: 'failed',
  fingerprint: 'sha256:vs13',
  progress: {
    planned_episodes: 2,
    planned_lane_episodes: 2,
    completed_episodes: 1,
    completed_lane_episodes: 1,
  },
  outcome_counts: { pass: 0, fail: 1, error: 0, cancelled: 0, incomplete: 1 },
  lanes: [
    {
      id: 'lane-1',
      lane_key: 'candidate',
      role: 'candidate',
      target_id: 'target-1',
      target_revision_id: 'revision-1',
    },
  ],
  run_attempts: [
    {
      id: 'attempt-1',
      attempt_no: 1,
      reason: 'initial',
      state: 'failed',
      started_at: '2026-07-06T00:00:01.000Z',
      ended_at: '2026-07-06T00:00:02.000Z',
      created_at: '2026-07-06T00:00:00.000Z',
    },
    {
      id: 'attempt-2',
      attempt_no: 2,
      reason: 'retry',
      state: 'failed',
      started_at: '2026-07-06T00:01:01.000Z',
      ended_at: '2026-07-06T00:01:02.000Z',
      created_at: '2026-07-06T00:01:00.000Z',
    },
  ],
  lane_attempts: [
    {
      id: 'lane-attempt-1',
      lane_id: 'lane-1',
      lane_key: 'candidate',
      run_attempt_id: 'attempt-1',
      attempt_no: 1,
      reason: 'initial',
      state: 'failed',
      artifact_root: 'lanes/candidate/attempts/0001',
      started_at: '2026-07-06T00:00:01.000Z',
      ended_at: '2026-07-06T00:00:02.000Z',
    },
    {
      id: 'lane-attempt-2',
      lane_id: 'lane-1',
      lane_key: 'candidate',
      run_attempt_id: 'attempt-2',
      attempt_no: 2,
      reason: 'retry',
      state: 'failed',
      artifact_root: 'lanes/candidate/attempts/0002',
      started_at: '2026-07-06T00:01:01.000Z',
      ended_at: '2026-07-06T00:01:02.000Z',
    },
  ],
  target_revisions: [
    { target_id: 'target-1', target_revision_id: 'revision-1', metadata_hash: 'metadata-1' },
  ],
  episode_identities: [
    {
      episode_key: 'fake.Task::1',
      pair_key: 'fake.Task::1',
      task_base_id: 'fake.Task',
      task_id: 'fake.Task',
      instance_id: 0,
      instance_seed: 1,
      template_index: null,
      trial_id: 0,
      max_steps: 10,
      sequence_index: null,
      sequence_group_id: null,
    },
  ],
  episode_attempts: [
    {
      episode_key: 'fake.Task::1',
      lane_key: 'candidate',
      run_attempt_id: 'attempt-1',
      lane_attempt_id: 'lane-attempt-1',
      episode_attempt_id: 'episode-attempt-1',
      attempt_no: 1,
      episode_attempt_no: 1,
      state: 'failed',
      outcome: 'FAIL',
      error_code: 'ASSERTION_FAILURE',
      artifact_root: 'lanes/candidate/attempts/0001/episode-1',
    },
    {
      episode_key: 'fake.Task::1',
      lane_key: 'candidate',
      run_attempt_id: 'attempt-2',
      lane_attempt_id: 'lane-attempt-2',
      episode_attempt_id: 'episode-attempt-2',
      attempt_no: 2,
      episode_attempt_no: 1,
      state: 'failed',
      outcome: 'ERROR',
      error_code: 'EXECUTION_ERROR',
      artifact_root: 'lanes/candidate/attempts/0002/episode-1',
    },
  ],
  run_plan: {},
  execution_identity: legacyExecutionIdentity,
  gate_verdict: null,
  created_at: '2026-07-06T00:00:00.000Z',
  started_at: '2026-07-06T00:00:01.000Z',
  ended_at: '2026-07-06T00:01:02.000Z',
};

const runWithOnlineModelKey: RunDetail = {
  ...run,
  run_plan: {
    lanes: [
      {
        lane_key: 'candidate',
        runner_config: {
          agent: 'autoglm',
          model_name: 'glm-5v-turbo',
          model_base_url: 'https://open.bigmodel.cn/api/paas/v4',
          image_url_format: 'bare_base64',
          model_api_key_configured: true,
        },
      },
    ],
  },
};

const profileAwareExecutionIdentity: RunDetail['execution_identity'] = {
  kind: 'profile_aware',
  label: 'Execution Profile Revision',
  schema_version: 2,
  lane_bindings: [
    {
      lane_slot: 'baseline',
      target_revision_id: 'target-revision-frozen-ep07',
      target_revision_hash: 'sha256:target-frozen-ep07',
      execution_profile_id: 'profile-baseline-ep07',
      execution_profile_name: 'Baseline frozen subject',
      execution_profile_revision_id: 'profile-revision-baseline-ep07',
      execution_profile_revision_no: 1,
      execution_profile_public_hash: 'sha256:profile-public-baseline-ep07',
      execution_profile_revision_hash: 'sha256:profile-revision-baseline-ep07',
      lane_fingerprint: 'sha256:lane-baseline-ep07',
    },
    {
      lane_slot: 'candidate',
      target_revision_id: 'target-revision-frozen-ep07',
      target_revision_hash: 'sha256:target-frozen-ep07',
      execution_profile_id: 'profile-candidate-ep07',
      execution_profile_name: 'Candidate frozen subject',
      execution_profile_revision_id: 'profile-revision-candidate-ep07',
      execution_profile_revision_no: 1,
      execution_profile_public_hash: 'sha256:profile-public-candidate-ep07',
      execution_profile_revision_hash: 'sha256:profile-revision-candidate-ep07',
      lane_fingerprint: 'sha256:lane-candidate-ep07',
    },
  ],
};

const profileAwareRun: RunDetail = {
  ...run,
  run_plan: {
    schema_version: 2,
    execution_snapshots: {
      'profile-revision-baseline-ep07': {
        public_spec: {
          credentials: { required_slots: [] },
        },
      },
      'profile-revision-candidate-ep07': {
        public_spec: {
          credentials: { required_slots: [] },
        },
      },
    },
    lanes: [
      {
        lane_key: 'baseline',
        execution_snapshot_key: 'profile-revision-baseline-ep07',
      },
      {
        lane_key: 'candidate',
        execution_snapshot_key: 'profile-revision-candidate-ep07',
      },
    ],
  },
  execution_identity: profileAwareExecutionIdentity,
  lanes: profileAwareExecutionIdentity.lane_bindings.map((binding, index) => ({
    id: `lane-profile-aware-${index}`,
    lane_key: binding.lane_slot,
    role: binding.lane_slot,
    target_id: 'target-frozen-ep07',
    target_revision_id: binding.target_revision_id,
    execution_profile_revision_id: binding.execution_profile_revision_id,
    execution_profile_revision_hash: binding.execution_profile_revision_hash,
    lane_fingerprint: binding.lane_fingerprint,
  })),
};

const profileAwareRunWithOnlineModelKey: RunDetail = {
  ...profileAwareRun,
  run_plan: {
    schema_version: 2,
    execution_snapshots: {
      'profile-revision-baseline-ep07': {
        public_spec: {
          credentials: { required_slots: [] },
        },
      },
      'profile-revision-candidate-ep07': {
        public_spec: {
          credentials: { required_slots: ['model_api_key'] },
        },
      },
    },
    lanes: [
      {
        lane_key: 'baseline',
        execution_snapshot_key: 'profile-revision-baseline-ep07',
        effective_runner_config: {
          model_name: 'deterministic-profile-pass',
        },
      },
      {
        lane_key: 'candidate',
        execution_snapshot_key: 'profile-revision-candidate-ep07',
        effective_runner_config: {
          model_name: 'deterministic-profile-fail',
        },
      },
    ],
  },
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

describe('Test Platform retry/resume controls', () => {
  beforeEach(() => {
    window.history.pushState({}, '', `/test-platform/runs/${run.id}`);
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows attempt history, retries, and surfaces resume incompatibility details', async () => {
    let retryBody: unknown = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }
      if (path === `/api/platform/v1/runs/${run.id}/retry/preview`) {
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind: 'retry',
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          preview_token: 'sha256:retry-preview',
          can_execute: true,
          empty_reason: null,
          selected_lane_episodes: [
            { episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' },
          ],
        });
      }
      if (path === `/api/platform/v1/runs/${run.id}/resume/preview`) {
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind: 'resume',
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          preview_token: 'sha256:resume-preview',
          can_execute: true,
          empty_reason: null,
          selected_lane_episodes: [
            { episode_key: 'fake.Task::2', lane_key: 'candidate', reason: 'resume_missing' },
          ],
        });
      }
      if (path === `/api/platform/v1/runs/${run.id}/retry`) {
        retryBody = JSON.parse(String(init?.body ?? '{}'));
        return jsonResponse({
          run_id: run.id,
          run_attempt_id: 'attempt-3',
          attempt_no: 3,
          reason: 'retry',
          selected_lane_episodes: [
            { episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' },
          ],
        }, 202);
      }
      if (path === `/api/platform/v1/runs/${run.id}/resume`) {
        return jsonResponse({
          error: {
            code: 'RUN_RESUME_INCOMPATIBLE_REVISION',
            message: 'The run cannot be resumed because its frozen target revisions are no longer current.',
            details: [
              {
                kind: 'target_revision',
                lane_key: 'candidate',
                expected_revision_id: 'revision-1',
                current_revision_id: 'revision-2',
              },
            ],
          },
        }, 409);
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Attempt history' })).toBeTruthy();
    expect(screen.getAllByText('initial').length).toBeGreaterThan(0);
    expect(screen.getAllByText('retry').length).toBeGreaterThan(0);
    expect(screen.getByText('lanes/candidate/attempts/0002')).toBeTruthy();
    expect((await screen.findByTestId('tp-retry-preview')).textContent).toContain(
      'candidate · fake.Task::1 · retry_error',
    );
    expect(screen.getByTestId('tp-resume-preview').textContent).toContain(
      'candidate · fake.Task::2 · resume_missing',
    );
    const historicalAttemptLink = screen.getByRole('link', {
      name: 'Open attempt 1 evidence',
    });
    const historicalHref = historicalAttemptLink.getAttribute('href');
    const historicalUrl = new URL(historicalHref ?? '', window.location.origin);
    expect(Object.fromEntries(historicalUrl.searchParams)).toEqual({
      lane: 'candidate',
      episode: 'fake.Task::1',
      attempt: '1',
      screenshot: 'annotated',
      evidence: 'judge',
    });

    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }));
    await waitFor(() => {
      expect(retryBody).toEqual({ preview_token: 'sha256:retry-preview' });
    });
    expect(screen.getByRole('link', { name: 'Open attempt 1 evidence' }).getAttribute('href'))
      .toBe(historicalHref);

    fireEvent.click(screen.getByRole('button', { name: 'Resume run' }));
    const message = await screen.findByTestId('tp-followup-message');
    expect(message.textContent).toContain('RUN_RESUME_INCOMPATIBLE_REVISION');
    expect(message.textContent).toContain('revision-1');
    expect(message.textContent).toContain('revision-2');
  });

  it('requires a follow-up model API key and sends it only in the retry body', async () => {
    let retryBody: unknown = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(runWithOnlineModelKey);
      }
      if (path === `/api/platform/v1/runs/${run.id}/retry/preview`) {
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind: 'retry',
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          preview_token: 'sha256:retry-preview',
          can_execute: true,
          empty_reason: null,
          selected_lane_episodes: [
            { episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' },
          ],
        });
      }
      if (path === `/api/platform/v1/runs/${run.id}/resume/preview`) {
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind: 'resume',
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          preview_token: 'sha256:resume-preview',
          can_execute: false,
          empty_reason: 'No missing or service-restarted lane episodes are available to resume.',
          selected_lane_episodes: [],
        });
      }
      if (path === `/api/platform/v1/runs/${run.id}/retry`) {
        retryBody = JSON.parse(String(init?.body ?? '{}'));
        return jsonResponse({
          run_id: run.id,
          run_attempt_id: 'attempt-3',
          attempt_no: 3,
          reason: 'retry',
          selected_lane_episodes: [
            { episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' },
          ],
        }, 202);
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByLabelText('Model API key')).toBeTruthy();
    expect(await screen.findByTestId('tp-retry-preview')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }));
    expect((await screen.findByTestId('tp-followup-message')).textContent).toContain(
      'Model API key is required',
    );
    expect(
      fetchMock.mock.calls.some(
        ([input]) => requestPath(input as RequestInfo | URL) === `/api/platform/v1/runs/${run.id}/retry`,
      ),
    ).toBe(false);

    fireEvent.change(screen.getByLabelText('Model API key'), {
      target: { value: 'sk-retry-secret' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Retry run' }));

    await waitFor(() => {
      expect(retryBody).toEqual({
        execution: { model_api_key: 'sk-retry-secret' },
        preview_token: 'sha256:retry-preview',
      });
    });
    expect(JSON.stringify(retryBody)).not.toContain('model_base_url');
  });

  it('shows the transient model API key input required by a frozen Run Plan v2 snapshot', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/health/ready') {
        return jsonResponse({ ready: true, checks: {} });
      }
      if (path === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(profileAwareRunWithOnlineModelKey);
      }
      if (path.endsWith('/retry/preview') || path.endsWith('/resume/preview')) {
        const kind = path.endsWith('/retry/preview') ? 'retry' : 'resume';
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind,
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          execution_identity: profileAwareExecutionIdentity,
          preview_token: `sha256:${kind}-profile-aware-credential`,
          can_execute: kind === 'retry',
          empty_reason: kind === 'retry'
            ? null
            : 'No missing or service-restarted lane episodes are available to resume.',
          selected_lane_episodes: kind === 'retry'
            ? [{ episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' }]
            : [],
        });
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByLabelText('Model API key')).toBeTruthy();
  });

  it('submits and clears a frozen-snapshot model API key only through Resume', async () => {
    let resumeBody: unknown = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === '/health/ready') {
        return jsonResponse({ ready: true, checks: {} });
      }
      if (path === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(profileAwareRunWithOnlineModelKey);
      }
      if (path === `/api/platform/v1/runs/${run.id}/retry/preview`) {
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind: 'retry',
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          execution_identity: profileAwareExecutionIdentity,
          preview_token: 'sha256:retry-profile-aware-credential',
          can_execute: false,
          empty_reason: 'No failed or errored lane episodes are available to retry.',
          selected_lane_episodes: [],
        });
      }
      if (path === `/api/platform/v1/runs/${run.id}/resume/preview`) {
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind: 'resume',
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          execution_identity: profileAwareExecutionIdentity,
          preview_token: 'sha256:resume-profile-aware-credential',
          can_execute: true,
          empty_reason: null,
          selected_lane_episodes: [
            { episode_key: 'fake.Task::2', lane_key: 'candidate', reason: 'resume_missing' },
          ],
        });
      }
      if (path === `/api/platform/v1/runs/${run.id}/resume`) {
        resumeBody = JSON.parse(String(init?.body ?? '{}'));
        return jsonResponse({
          run_id: run.id,
          run_attempt_id: 'attempt-3',
          attempt_no: 3,
          reason: 'resume',
          selected_lane_episodes: [
            { episode_key: 'fake.Task::2', lane_key: 'candidate', reason: 'resume_missing' },
          ],
        }, 202);
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const modelApiKeyInput = await screen.findByLabelText('Model API key') as HTMLInputElement;
    expect(await screen.findByTestId('tp-resume-preview')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Resume run' }));
    expect((await screen.findByTestId('tp-followup-message')).textContent).toContain(
      'Model API key is required',
    );
    expect(resumeBody).toBeNull();

    fireEvent.change(modelApiKeyInput, { target: { value: 'sk-resume-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Resume run' }));

    await waitFor(() => {
      expect(resumeBody).toEqual({
        execution: { model_api_key: 'sk-resume-secret' },
        preview_token: 'sha256:resume-profile-aware-credential',
      });
      expect(modelApiKeyInput.value).toBe('');
    });
    expect(Object.values(window.localStorage).join('\n')).not.toContain('sk-resume-secret');
    expect(JSON.stringify(resumeBody)).not.toContain('model_base_url');
  });

  it('disables empty follow-up selections and explains why', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/health/ready') {
        return jsonResponse({ ready: true, checks: {} });
      }
      if (path === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(run);
      }
      if (path.endsWith('/retry/preview') || path.endsWith('/resume/preview')) {
        const kind = path.endsWith('/retry/preview') ? 'retry' : 'resume';
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind,
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          preview_token: `sha256:${kind}-empty`,
          can_execute: false,
          empty_reason: kind === 'retry'
            ? 'No failed or errored lane episodes are available to retry.'
            : 'No missing or service-restarted lane episodes are available to resume.',
          selected_lane_episodes: [],
        });
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByTestId('tp-retry-preview')).toBeTruthy();
    expect((screen.getByRole('button', { name: 'Retry run' }) as HTMLButtonElement).disabled)
      .toBe(true);
    expect((screen.getByRole('button', { name: 'Resume run' }) as HTMLButtonElement).disabled)
      .toBe(true);
    expect(screen.getByTestId('tp-retry-preview').textContent).toContain(
      'No failed or errored lane episodes are available to retry.',
    );
    expect(screen.getByTestId('tp-resume-preview').textContent).toContain(
      'No missing or service-restarted lane episodes are available to resume.',
    );
  });

  it('shows immutable profile-aware Lane Bindings without revision selectors after reload', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === '/health/ready') {
        return jsonResponse({ ready: true, checks: {} });
      }
      if (path === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }
      if (path === `/api/platform/v1/runs/${run.id}`) {
        return jsonResponse(profileAwareRun);
      }
      if (path.endsWith('/retry/preview') || path.endsWith('/resume/preview')) {
        const kind = path.endsWith('/retry/preview') ? 'retry' : 'resume';
        return jsonResponse({
          schema_version: 1,
          run_id: run.id,
          kind,
          source_run_attempt_id: 'attempt-2',
          source_attempt_no: 2,
          execution_identity: profileAwareExecutionIdentity,
          preview_token: `sha256:${kind}-profile-aware-ep07`,
          can_execute: kind === 'retry',
          empty_reason: kind === 'retry'
            ? null
            : 'No missing or service-restarted lane episodes are available to resume.',
          selected_lane_episodes: kind === 'retry'
            ? [{ episode_key: 'fake.Task::1', lane_key: 'candidate', reason: 'retry_error' }]
            : [],
        });
      }
      return jsonResponse({ error: { code: 'NOT_FOUND', message: 'not found', details: [] } }, 404);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const identity = await screen.findByTestId('tp-followup-frozen-identity');
    expect(identity.textContent).toContain('Frozen follow-up Lane Bindings');
    expect(identity.textContent).toContain('target-revision-frozen-ep07');
    expect(identity.textContent).toContain('profile-revision-baseline-ep07');
    expect(identity.textContent).toContain('profile-revision-candidate-ep07');
    expect(identity.textContent).toContain('sha256:lane-candidate-ep07');
    expect(screen.queryByLabelText('Target Revision')).toBeNull();
    expect(screen.queryByLabelText('Execution Profile Revision')).toBeNull();
    expect(screen.queryByLabelText('Model API key')).toBeNull();
    const historicalHref = screen.getByRole('link', {
      name: 'Open attempt 1 evidence',
    }).getAttribute('href');
    expect(Object.fromEntries(new URL(
      historicalHref ?? '',
      window.location.origin,
    ).searchParams)).toEqual({
      lane: 'candidate',
      episode: 'fake.Task::1',
      attempt: '1',
      screenshot: 'annotated',
      evidence: 'judge',
    });

    cleanup();
    render(<App />);
    const reloadedIdentity = await screen.findByTestId('tp-followup-frozen-identity');
    expect(reloadedIdentity.textContent).toContain('profile-revision-baseline-ep07');
    expect(reloadedIdentity.textContent).toContain('profile-revision-candidate-ep07');
    expect(screen.getByRole('link', {
      name: 'Open attempt 1 evidence',
    }).getAttribute('href')).toBe(historicalHref);
  });
});
