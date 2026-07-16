import { useState, type FormEvent } from 'react';

import { cloneExecutionProfileRevision } from '../../../api/client';
import type {
  ExecutionProfile,
  ExecutionProfileRevision,
} from '../../../api/types';

export function CloneExecutionProfileDialog({
  projectId,
  revision,
  onCloned,
  onCancel,
}: {
  projectId: string;
  revision: ExecutionProfileRevision;
  onCloned: (profile: ExecutionProfile) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(`Clone of revision ${revision.revision_no}`);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    cloneExecutionProfileRevision({
      projectId,
      revisionId: revision.id,
      name: name.trim(),
    })
      .then(onCloned)
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Revision could not be cloned.');
      })
      .finally(() => setSubmitting(false));
  };

  return (
    <div className="tp-modal-backdrop">
      <form
        className="tp-modal"
        role="dialog"
        aria-modal="true"
        aria-label={`Clone revision ${revision.revision_no}`}
        onSubmit={submit}
      >
        <h2>Clone Execution Profile Revision</h2>
        <p>Revision {revision.revision_no} will become a new editable draft.</p>
        <label>
          Clone profile name
          <input value={name} onChange={(event) => setName(event.target.value)} />
        </label>
        {error ? <div className="tp-inline-alert" role="alert">{error}</div> : null}
        <div className="tp-modal-actions">
          <button type="button" onClick={onCancel}>Cancel</button>
          <button type="submit" disabled={submitting || !name.trim()}>
            {submitting ? 'Cloning...' : 'Clone profile'}
          </button>
        </div>
      </form>
    </div>
  );
}
