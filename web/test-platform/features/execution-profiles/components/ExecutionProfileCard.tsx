import { useState } from 'react';

import {
  archiveExecutionProfile,
  diffExecutionProfileRevisions,
  listExecutionProfileRevisions,
  publishExecutionProfile,
} from '../../../api/client';
import type {
  ExecutionProfile,
  ExecutionProfileRevision,
  ExecutionProfileRevisionDiff,
} from '../../../api/types';
import { CloneExecutionProfileDialog } from './CloneExecutionProfileDialog';
import { ExecutionProfileDraftDialog } from './ExecutionProfileDraftDialog';

export function ExecutionProfileCard({
  projectId,
  profile,
  onPublished,
  onChanged,
}: {
  projectId: string;
  profile: ExecutionProfile;
  onPublished: (profileId: string, revision: ExecutionProfileRevision) => void;
  onChanged: (profile: ExecutionProfile) => void;
}) {
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editOpen, setEditOpen] = useState(false);
  const [revisions, setRevisions] = useState<ExecutionProfileRevision[] | null>(null);
  const [revisionDiff, setRevisionDiff] = useState<ExecutionProfileRevisionDiff | null>(null);
  const [cloneRevision, setCloneRevision] = useState<ExecutionProfileRevision | null>(null);
  const spec = profile.draft_spec;

  const publish = () => {
    setPublishing(true);
    setError(null);
    publishExecutionProfile({
      projectId,
      executionProfileId: profile.id,
      expectedDraftVersion: profile.draft_version,
      expectedHeadRevisionId: profile.head_revision?.id ?? null,
    })
      .then((revision) => onPublished(profile.id, revision))
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Profile could not be published.');
      })
      .finally(() => setPublishing(false));
  };

  const loadHistory = () => {
    setError(null);
    listExecutionProfileRevisions({ projectId, executionProfileId: profile.id })
      .then((response) => setRevisions(response.items))
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Revision history could not be loaded.');
      });
  };

  const compareLatest = () => {
    if (!revisions || revisions.length < 2) return;
    const [fromRevision, toRevision] = revisions.slice(-2);
    diffExecutionProfileRevisions({
      projectId,
      fromRevisionId: fromRevision.id,
      toRevisionId: toRevision.id,
    })
      .then(setRevisionDiff)
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Revision diff could not be loaded.');
      });
  };

  const archive = () => {
    setError(null);
    archiveExecutionProfile({
      projectId,
      executionProfileId: profile.id,
      expectedDraftVersion: profile.draft_version,
      expectedHeadRevisionId: profile.head_revision?.id ?? null,
    })
      .then(onChanged)
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Profile could not be archived.');
      });
  };

  return (
    <article className="tp-panel tp-target-card">
      <div className="tp-target-card-header">
        <div>
          <h2>{profile.name}</h2>
          <p>
            {profile.archived_at
              ? 'Archived'
              : profile.head_revision
                ? `Revision ${profile.head_revision.revision_no}`
                : 'Draft'}
          </p>
        </div>
        <div className="tp-modal-actions">
          {profile.archived_at === null ? (
            <>
              <button type="button" onClick={() => setEditOpen(true)}>Edit draft</button>
              <button type="button" onClick={archive}>Archive profile</button>
            </>
          ) : null}
          <button
            type="button"
            onClick={publish}
            disabled={publishing || profile.archived_at !== null}
          >
            {publishing ? 'Publishing...' : 'Publish revision'}
          </button>
        </div>
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
      <div className="tp-modal-actions">
        <button type="button" onClick={loadHistory}>View revision history</button>
        {revisions && revisions.length >= 2 ? (
          <button type="button" onClick={compareLatest}>Compare latest revisions</button>
        ) : null}
      </div>
      {revisions ? (
        <section aria-label={`Revision history for ${profile.name}`}>
          <h3>Revision history</h3>
          {revisions.map((revision) => (
            <div className="tp-profile-revision" key={revision.id}>
              <div>
                <strong>Revision {revision.revision_no}</strong>
                <div className="tp-mono">{revision.id}</div>
                <div>{revision.public_spec.agent.id} / {revision.public_spec.model.name}</div>
                <div className="tp-mono">{revision.public_spec.model.base_url}</div>
                <div>Image Input Format: {revision.public_spec.image_input.format}</div>
                <div className="tp-mono">Public hash: {revision.public_spec_hash}</div>
                <div className="tp-mono">Binding digest: {revision.credential_binding_digest}</div>
                <div>Published: {revision.published_at}</div>
              </div>
              <button type="button" onClick={() => setCloneRevision(revision)}>
                Clone revision {revision.revision_no}
              </button>
            </div>
          ))}
        </section>
      ) : null}
      {revisionDiff ? (
        <section aria-label="Public revision diff">
          <h3>Public revision diff</h3>
          {revisionDiff.changes.map((change) => (
            <div className="tp-profile-revision" key={change.path}>
              <strong>{change.path}</strong>
              <span>{String(change.before)}</span>
              <span>{String(change.after)}</span>
            </div>
          ))}
        </section>
      ) : null}
      {error ? <div className="tp-inline-alert" role="alert">{error}</div> : null}
      {editOpen ? (
        <ExecutionProfileDraftDialog
          projectId={projectId}
          profile={profile}
          onSaved={(saved) => {
            onChanged(saved);
            setEditOpen(false);
          }}
          onCancel={() => setEditOpen(false)}
        />
      ) : null}
      {cloneRevision ? (
        <CloneExecutionProfileDialog
          projectId={projectId}
          revision={cloneRevision}
          onCloned={(clone) => {
            onChanged(clone);
            setCloneRevision(null);
          }}
          onCancel={() => setCloneRevision(null)}
        />
      ) : null}
    </article>
  );
}
