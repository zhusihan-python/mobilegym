import { useEffect, useState, type FormEvent } from 'react';
import { useOutletContext } from 'react-router-dom';

import {
  createExecutionProfile,
  listExecutionProfiles,
  publishExecutionProfile,
} from '../../api/client';
import type {
  CollectionResponse,
  ExecutionProfile,
  ExecutionProfileRevision,
  ExecutionProfileSpec,
  Project,
} from '../../api/types';

type ProfilesState =
  | { status: 'loading' }
  | { status: 'loaded'; data: CollectionResponse<ExecutionProfile> }
  | { status: 'error'; message: string };

export function ExecutionProfilesPage() {
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const [profiles, setProfiles] = useState<ProfilesState>({ status: 'loading' });
  const [createOpen, setCreateOpen] = useState(false);

  const loadProfiles = () => {
    setProfiles({ status: 'loading' });
    listExecutionProfiles(selectedProject.id)
      .then((data) => setProfiles({ status: 'loaded', data }))
      .catch((error) => {
        setProfiles({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to load Execution Profiles.',
        });
      });
  };

  useEffect(() => {
    loadProfiles();
  }, [selectedProject.id]);

  const handleCreated = (profile: ExecutionProfile) => {
    setProfiles((current) => {
      if (current.status !== 'loaded') return current;
      return {
        status: 'loaded',
        data: { ...current.data, items: [...current.data.items, profile] },
      };
    });
    setCreateOpen(false);
  };

  const handlePublished = (
    executionProfileId: string,
    revision: ExecutionProfileRevision,
  ) => {
    setProfiles((current) => {
      if (current.status !== 'loaded') return current;
      return {
        status: 'loaded',
        data: {
          ...current.data,
          items: current.data.items.map((profile) =>
            profile.id === executionProfileId
              ? { ...profile, head_revision: revision }
              : profile,
          ),
        },
      };
    });
  };

  if (profiles.status === 'loading') {
    return <section className="tp-panel">Loading Execution Profiles...</section>;
  }
  if (profiles.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Execution Profiles could not be loaded</h2>
        <p>{profiles.message}</p>
        <button type="button" onClick={loadProfiles}>Retry profiles</button>
      </section>
    );
  }

  return (
    <>
      <section className="tp-panel tp-page-actions">
        <div>
          <h2>Execution Profiles</h2>
          <p>Immutable Agent and model subject identity for {selectedProject.name}.</p>
        </div>
        <button type="button" onClick={() => setCreateOpen(true)}>
          New execution profile
        </button>
      </section>

      {profiles.data.items.length === 0 ? (
        <section className="tp-empty">
          <h2>No Execution Profiles yet</h2>
          <p>Create a no-secret subject draft, review it, and publish revision 1.</p>
          <button type="button" onClick={() => setCreateOpen(true)}>
            Create first execution profile
          </button>
        </section>
      ) : (
        <section className="tp-target-list" aria-label="Execution Profiles">
          {profiles.data.items.map((profile) => (
            <ExecutionProfileCard
              key={profile.id}
              projectId={selectedProject.id}
              profile={profile}
              onPublished={handlePublished}
            />
          ))}
        </section>
      )}

      {createOpen ? (
        <CreateExecutionProfileDialog
          projectId={selectedProject.id}
          onCreated={handleCreated}
          onCancel={() => setCreateOpen(false)}
        />
      ) : null}
    </>
  );
}

function ExecutionProfileCard({
  projectId,
  profile,
  onPublished,
}: {
  projectId: string;
  profile: ExecutionProfile;
  onPublished: (profileId: string, revision: ExecutionProfileRevision) => void;
}) {
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const spec = profile.head_revision?.public_spec ?? profile.draft_spec;

  const publish = () => {
    setPublishing(true);
    setError(null);
    publishExecutionProfile({ projectId, executionProfileId: profile.id })
      .then((revision) => onPublished(profile.id, revision))
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Profile could not be published.');
      })
      .finally(() => setPublishing(false));
  };

  return (
    <article className="tp-panel tp-target-card">
      <div className="tp-target-card-header">
        <div>
          <h2>{profile.name}</h2>
          <p>{profile.head_revision ? `Revision ${profile.head_revision.revision_no}` : 'Draft'}</p>
        </div>
        <button
          type="button"
          onClick={publish}
          disabled={publishing || profile.head_revision !== null}
        >
          {publishing ? 'Publishing...' : 'Publish revision'}
        </button>
      </div>

      <dl className="tp-target-details">
        <div><dt>Agent</dt><dd>{spec.agent.id}</dd></div>
        <div><dt>Model</dt><dd>{spec.model.name}</dd></div>
        <div><dt>Image Input Format</dt><dd>{spec.image_input.format}</dd></div>
        <div><dt>Streaming</dt><dd>{spec.generation.stream ? 'Enabled' : 'Disabled'}</dd></div>
        <div><dt>Endpoint</dt><dd className="tp-mono">{spec.model.base_url}</dd></div>
        <div><dt>Temperature / top-p</dt><dd>{spec.generation.temperature} / {spec.generation.top_p}</dd></div>
        <div><dt>Max tokens</dt><dd>{spec.generation.max_tokens}</dd></div>
        <div><dt>Inference timeout</dt><dd>{spec.inference.timeout_seconds}s</dd></div>
        <div>
          <dt>Credential readiness</dt>
          <dd>{profile.credential_readiness.ready ? 'Ready' : 'Missing binding'}</dd>
        </div>
        <div>
          <dt>Credential slots</dt>
          <dd>{profile.credential_readiness.required_slots.join(', ') || 'None'}</dd>
        </div>
      </dl>

      {profile.head_revision ? (
        <div className="tp-profile-revision">
          <div><span className="tp-subtitle">Revision ID</span><strong className="tp-mono">{profile.head_revision.id}</strong></div>
          <div><span className="tp-subtitle">Public hash</span><strong className="tp-mono">{profile.head_revision.public_spec_hash}</strong></div>
        </div>
      ) : null}
      {error ? <div className="tp-inline-alert" role="alert">{error}</div> : null}
    </article>
  );
}

function CreateExecutionProfileDialog({
  projectId,
  onCreated,
  onCancel,
}: {
  projectId: string;
  onCreated: (profile: ExecutionProfile) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('Generic v2 / local model');
  const [modelBaseUrl, setModelBaseUrl] = useState('http://127.0.0.1:1234/v1');
  const [modelName, setModelName] = useState('local-model');
  const [imageFormat, setImageFormat] = useState<'data_url' | 'bare_base64'>('data_url');
  const [temperature, setTemperature] = useState('0');
  const [topP, setTopP] = useState('1');
  const [maxTokens, setMaxTokens] = useState('4096');
  const [stream, setStream] = useState(true);
  const [timeoutSeconds, setTimeoutSeconds] = useState('300');
  const [requireModelCredential, setRequireModelCredential] = useState(false);
  const [credentialReferenceId, setCredentialReferenceId] = useState(
    'primary-model-key',
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const draftSpec: ExecutionProfileSpec = {
      schema_version: 1,
      agent: { id: 'generic_v2' },
      model: {
        protocol: 'openai_chat_completions',
        base_url: modelBaseUrl.trim(),
        name: modelName.trim(),
      },
      image_input: { format: imageFormat },
      generation: {
        temperature: Number(temperature),
        top_p: Number(topP),
        max_tokens: Number(maxTokens),
        stream,
      },
      inference: { timeout_seconds: Number(timeoutSeconds) },
      credentials: {
        required_slots: requireModelCredential ? ['model_api_key'] : [],
      },
    };
    setSubmitting(true);
    setError(null);
    createExecutionProfile({
      projectId,
      name: name.trim(),
      draftSpec,
      credentialBindings: requireModelCredential
        ? [{
          slot: 'model_api_key',
          project_id: projectId,
          backend: 'request',
          reference_id: credentialReferenceId.trim(),
          private_locator: 'request://transient/model-api-key',
        }]
        : [],
    })
      .then(onCreated)
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Profile could not be created.');
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <div className="tp-modal-backdrop">
      <form className="tp-modal tp-profile-modal" role="dialog" aria-modal="true" aria-label="Create Execution Profile" onSubmit={submit}>
        <h2>New Execution Profile</h2>
        <label>Profile name<input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label>Agent<select value="generic_v2" disabled><option value="generic_v2">generic_v2</option></select></label>
        <label>Model endpoint<input value={modelBaseUrl} onChange={(event) => setModelBaseUrl(event.target.value)} /></label>
        <label>Model name<input value={modelName} onChange={(event) => setModelName(event.target.value)} /></label>
        <label>Image Input Format<select value={imageFormat} onChange={(event) => setImageFormat(event.target.value as 'data_url' | 'bare_base64')}><option value="data_url">data_url</option><option value="bare_base64">bare_base64</option></select></label>
        <label>Temperature<input type="number" step="0.1" value={temperature} onChange={(event) => setTemperature(event.target.value)} /></label>
        <label>Top p<input type="number" step="0.1" value={topP} onChange={(event) => setTopP(event.target.value)} /></label>
        <label>Max tokens<input type="number" value={maxTokens} onChange={(event) => setMaxTokens(event.target.value)} /></label>
        <label className="tp-checkbox-field"><input type="checkbox" checked={stream} onChange={(event) => setStream(event.target.checked)} />Streaming</label>
        <label>Inference timeout<input type="number" value={timeoutSeconds} onChange={(event) => setTimeoutSeconds(event.target.value)} /></label>
        <label className="tp-checkbox-field">
          <input
            type="checkbox"
            checked={requireModelCredential}
            onChange={(event) => setRequireModelCredential(event.target.checked)}
          />
          Require model credential
        </label>
        {requireModelCredential ? (
          <label>
            Credential reference ID
            <input
              value={credentialReferenceId}
              onChange={(event) => setCredentialReferenceId(event.target.value)}
            />
          </label>
        ) : null}
        {error ? <div className="tp-inline-alert" role="alert">{error}</div> : null}
        <div className="tp-modal-actions">
          <button type="button" onClick={onCancel}>Cancel</button>
          <button
            type="submit"
            disabled={
              submitting
              || (requireModelCredential && !credentialReferenceId.trim())
            }
          >
            {submitting ? 'Creating...' : 'Create profile'}
          </button>
        </div>
      </form>
    </div>
  );
}
