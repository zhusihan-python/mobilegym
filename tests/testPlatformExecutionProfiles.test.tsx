import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';
import type {
  ExecutionProfile,
  ExecutionProfileRevision,
  ExecutionProfileSpec,
} from '../web/test-platform/api/types';

const project = {
  id: '11111111111111111111111111111111',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-15T00:00:00.000Z',
  updated_at: '2026-07-15T00:00:00.000Z',
};

const publicSpec: ExecutionProfileSpec = {
  schema_version: 1,
  agent: { id: 'generic_v2' },
  model: {
    protocol: 'openai_chat_completions',
    base_url: 'http://127.0.0.1:1234/v1',
    name: 'local-model',
  },
  image_input: { format: 'data_url' },
  generation: {
    temperature: 0,
    top_p: 1,
    max_tokens: 4096,
    stream: true,
  },
  inference: { timeout_seconds: 300 },
  credentials: { required_slots: ['model_api_key'] },
};

const credentialReadiness = {
  required_slots: ['model_api_key'],
  bound_slots: ['model_api_key'],
  missing_slots: [],
  ready: true,
  binding_digest: 'sha256:credential-reference-binding',
};

const revision: ExecutionProfileRevision = {
  id: 'epr-0000000000000000000000000001',
  execution_profile_id: 'ep-00000000000000000000000000001',
  revision_no: 1,
  public_spec: publicSpec,
  public_spec_hash: 'sha256:3d71ef81a34bc6d78054f83d6c094360be1893f79b2d1ffac1bc79a90d29c37d',
  credential_binding_digest: 'sha256:empty-bindings',
  credential_readiness: credentialReadiness,
  published_at: '2026-07-15T00:00:02.000Z',
};

const draftProfile: ExecutionProfile = {
  id: revision.execution_profile_id,
  project_id: project.id,
  name: 'Generic v2 / local model',
  draft_spec: publicSpec,
  credential_readiness: credentialReadiness,
  draft_version: 1,
  head_revision: null,
  archived_at: null,
  created_at: '2026-07-15T00:00:01.000Z',
  updated_at: '2026-07-15T00:00:01.000Z',
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

describe('Test Platform Execution Profiles workspace', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/execution-profiles');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('creates, publishes, reloads, and displays an exact public revision', async () => {
    let storedProfile: ExecutionProfile | null = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
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
      const collectionPath = `/api/platform/v1/projects/${project.id}/execution-profiles`;
      if (url.pathname === collectionPath && init?.method === 'POST') {
        const body = JSON.parse(String(init.body));
        expect(body).toEqual({
          name: 'Generic v2 / local model',
          draft_spec: publicSpec,
          credential_bindings: [{
            slot: 'model_api_key',
            project_id: project.id,
            backend: 'request',
            reference_id: 'primary-model-key',
            private_locator: 'request://transient/model-api-key',
          }],
        });
        expect(String(init.body)).not.toContain('sk-');
        expect(String(init.body)).not.toContain('secret_value');
        storedProfile = draftProfile;
        return jsonResponse(draftProfile, 201);
      }
      if (url.pathname === collectionPath) {
        return jsonResponse({ items: storedProfile ? [storedProfile] : [], next_cursor: null });
      }
      if (
        url.pathname === `${collectionPath}/${draftProfile.id}/publish`
        && init?.method === 'POST'
      ) {
        expect(JSON.parse(String(init.body))).toEqual({
          expected_draft_version: 1,
          expected_head_revision_id: storedProfile?.head_revision?.id ?? null,
        });
        storedProfile = { ...draftProfile, head_revision: revision };
        return jsonResponse(revision);
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Execution Profiles' })).toBeTruthy();
    fireEvent.click(await screen.findByRole('button', { name: 'New execution profile' }));
    fireEvent.change(screen.getByLabelText('Profile name'), {
      target: { value: 'Generic v2 / local model' },
    });
    fireEvent.change(screen.getByLabelText('Model endpoint'), {
      target: { value: 'http://127.0.0.1:1234/v1' },
    });
    fireEvent.change(screen.getByLabelText('Model name'), {
      target: { value: 'local-model' },
    });
    fireEvent.click(screen.getByLabelText('Require model credential'));
    fireEvent.change(screen.getByLabelText('Credential reference ID'), {
      target: { value: 'primary-model-key' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create profile' }));

    expect(await screen.findByRole('heading', { name: 'Generic v2 / local model' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Publish revision' }));

    expect(await screen.findByText(revision.id)).toBeTruthy();
    expect(screen.getByText(revision.public_spec_hash)).toBeTruthy();
    expect(screen.getByText('generic_v2')).toBeTruthy();
    expect(screen.getByText('local-model')).toBeTruthy();
    expect(screen.getByText('Credential readiness')).toBeTruthy();
    expect(screen.getByText('Ready')).toBeTruthy();
    expect(
      (screen.getByRole('button', { name: 'Publish revision' }) as HTMLButtonElement)
        .disabled,
    ).toBe(false);

    cleanup();
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText(revision.id)).toBeTruthy();
      expect(screen.getByText(revision.public_spec_hash)).toBeTruthy();
    });
  });

  it('edits, republishes, diffs, clones, archives, and discovers history', async () => {
    const revisionTwo: ExecutionProfileRevision = {
      ...revision,
      id: 'epr-0000000000000000000000000002',
      revision_no: 2,
      public_spec: {
        ...publicSpec,
        model: { ...publicSpec.model, name: 'next-model' },
      },
      public_spec_hash: 'sha256:revision-two-public-hash',
      published_at: '2026-07-15T00:00:03.000Z',
    };
    let storedProfiles: ExecutionProfile[] = [{
      ...draftProfile,
      head_revision: revision,
    }];
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      const collectionPath = `/api/platform/v1/projects/${project.id}/execution-profiles`;
      const profilePath = `${collectionPath}/${draftProfile.id}`;

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
      if (url.pathname === collectionPath) {
        const includeArchived = url.searchParams.get('include_archived') === 'true';
        return jsonResponse({
          items: storedProfiles.filter((profile) => includeArchived || !profile.archived_at),
          next_cursor: null,
        });
      }
      if (url.pathname === `${profilePath}/draft` && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body));
        expect(body.expected_draft_version).toBe(1);
        const changed: ExecutionProfile = {
          ...storedProfiles[0],
          draft_spec: body.draft_spec,
          draft_version: 2,
        };
        storedProfiles[0] = changed;
        return jsonResponse(changed);
      }
      if (url.pathname === `${profilePath}/publish` && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({
          expected_draft_version: 2,
          expected_head_revision_id: revision.id,
        });
        storedProfiles[0] = { ...storedProfiles[0], head_revision: revisionTwo };
        return jsonResponse(revisionTwo);
      }
      if (url.pathname === `${profilePath}/revisions`) {
        return jsonResponse({ items: [revision, revisionTwo], next_cursor: null });
      }
      if (url.pathname === `/api/platform/v1/projects/${project.id}/execution-profile-revision-diff`) {
        return jsonResponse({
          from_revision_id: revision.id,
          to_revision_id: revisionTwo.id,
          changes: [{ path: 'model.name', before: 'local-model', after: 'next-model' }],
        });
      }
      if (
        url.pathname === `/api/platform/v1/projects/${project.id}`
          + `/execution-profile-revisions/${revision.id}/clone`
        && init?.method === 'POST'
      ) {
        const body = JSON.parse(String(init.body));
        expect(body).toEqual({ name: 'Cloned revision one' });
        const clone: ExecutionProfile = {
          ...draftProfile,
          id: 'ep-00000000000000000000000000002',
          name: body.name,
          head_revision: null,
        };
        storedProfiles.push(clone);
        return jsonResponse(clone, 201);
      }
      if (url.pathname === `${profilePath}/archive` && init?.method === 'POST') {
        expect(JSON.parse(String(init.body))).toEqual({
          expected_draft_version: 2,
          expected_head_revision_id: revisionTwo.id,
        });
        const archived = {
          ...storedProfiles[0],
          archived_at: '2026-07-15T00:00:04.000Z',
        };
        storedProfiles[0] = archived;
        return jsonResponse(archived);
      }
      throw new Error(`Unexpected request: ${url.pathname}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByRole('heading', { name: draftProfile.name })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Edit draft' }));
    fireEvent.change(screen.getByLabelText('Model name'), {
      target: { value: 'next-model' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    expect(await screen.findByText('next-model')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Publish revision' }));
    expect(await screen.findByText(revisionTwo.id)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'View revision history' }));
    expect(await screen.findByText('Revision 1')).toBeTruthy();
    expect(screen.getAllByText('Revision 2').length).toBeGreaterThanOrEqual(2);
    fireEvent.click(screen.getByRole('button', { name: 'Compare latest revisions' }));
    expect(await screen.findByText('model.name')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Clone revision 1' }));
    fireEvent.change(screen.getByLabelText('Clone profile name'), {
      target: { value: 'Cloned revision one' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Clone profile' }));
    expect(await screen.findByRole('heading', { name: 'Cloned revision one' })).toBeTruthy();

    fireEvent.click(screen.getAllByRole('button', { name: 'Archive profile' })[0]);
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: draftProfile.name })).toBeNull();
    });
    fireEvent.click(screen.getByLabelText('Show archived profiles'));
    expect(await screen.findByRole('heading', { name: draftProfile.name })).toBeTruthy();
    expect(screen.getByText('Archived')).toBeTruthy();
  });
});
