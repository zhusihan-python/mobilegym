import { useEffect, useState, type FormEvent } from 'react';
import { useOutletContext } from 'react-router-dom';

import { checkTargetHealth, createSimulatorTarget, listTargets } from '../../api/client';
import type { CollectionResponse, Project, Target, TargetHealthResponse } from '../../api/types';

type TargetsState =
  | { status: 'loading' }
  | { status: 'loaded'; data: CollectionResponse<Target> }
  | { status: 'error'; message: string };

const DEFAULT_DEVICE_PROFILE = {
  name: 'Pixel 7',
  viewportWidth: 393,
  viewportHeight: 852,
  physicalWidth: 1080,
  physicalHeight: 2400,
  deviceScaleFactor: 2.75,
};

export function TargetsPage() {
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const [targets, setTargets] = useState<TargetsState>({ status: 'loading' });
  const [createOpen, setCreateOpen] = useState(false);

  const loadTargets = () => {
    setTargets({ status: 'loading' });
    listTargets(selectedProject.id)
      .then((data) => {
        setTargets({ status: 'loaded', data });
      })
      .catch((error) => {
        setTargets({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to load targets.',
        });
      });
  };

  useEffect(() => {
    loadTargets();
  }, [selectedProject.id]);

  const updateTargetRevision = (targetId: string, health: TargetHealthResponse) => {
    setTargets((current) => {
      if (current.status !== 'loaded') return current;
      return {
        status: 'loaded',
        data: {
          ...current.data,
          items: current.data.items.map((target) => {
            if (target.id !== targetId) return target;
            return health.revision ? { ...target, latest_revision: health.revision } : target;
          }),
        },
      };
    });
  };

  const handleCreated = (target: Target) => {
    setTargets((current) => {
      if (current.status !== 'loaded') return current;
      return {
        status: 'loaded',
        data: {
          ...current.data,
          items: [...current.data.items, target],
        },
      };
    });
    setCreateOpen(false);
  };

  if (targets.status === 'loading') {
    return <section className="tp-panel">Loading targets...</section>;
  }

  if (targets.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Targets could not be loaded</h2>
        <p>{targets.message}</p>
        <button type="button" onClick={loadTargets}>
          Retry targets
        </button>
      </section>
    );
  }

  return (
    <>
      <section className="tp-panel tp-page-actions">
        <div>
          <h2>Registered targets</h2>
          <p>Simulator endpoints and reserved device configs for {selectedProject.name}.</p>
        </div>
        <button type="button" onClick={() => setCreateOpen(true)}>
          New simulator target
        </button>
      </section>

      {targets.data.items.length === 0 ? (
        <section className="tp-empty">
          <h2>No targets yet</h2>
          <p>Register the local simulator endpoint to run health checks.</p>
          <button type="button" onClick={() => setCreateOpen(true)}>
            New simulator target
          </button>
        </section>
      ) : (
        <section className="tp-target-list" aria-label="Targets">
          {targets.data.items.map((target) => (
            <TargetCard
              key={target.id}
              target={target}
              onHealth={updateTargetRevision}
            />
          ))}
        </section>
      )}

      {createOpen ? (
        <CreateTargetDialog
          projectId={selectedProject.id}
          onCreated={handleCreated}
          onCancel={() => setCreateOpen(false)}
        />
      ) : null}
    </>
  );
}

function TargetCard({
  target,
  onHealth,
}: {
  target: Target;
  onHealth: (targetId: string, health: TargetHealthResponse) => void;
}) {
  const [health, setHealth] = useState<
    | { status: 'idle' }
    | { status: 'checking' }
    | { status: 'checked'; data: TargetHealthResponse }
    | { status: 'error'; message: string }
  >({ status: 'idle' });

  const revision = health.status === 'checked' && health.data.revision
    ? health.data.revision
    : target.latest_revision;
  const warnings = health.status === 'checked'
    ? health.data.warnings
    : revision?.warnings ?? [];
  const apps = revision?.metadata.apps ?? [];

  const runHealth = () => {
    setHealth({ status: 'checking' });
    checkTargetHealth(target.id)
      .then((data) => {
        setHealth({ status: 'checked', data });
        onHealth(target.id, data);
      })
      .catch((error) => {
        setHealth({
          status: 'error',
          message: error instanceof Error ? error.message : 'Health check failed.',
        });
      });
  };

  return (
    <article className="tp-panel tp-target-card">
      <div className="tp-target-card-header">
        <div>
          <h2>{target.name}</h2>
          <p>{target.kind === 'simulator' ? 'Simulator' : 'Real device'} - {target.config.device_profile.name}</p>
        </div>
        <button
          type="button"
          onClick={runHealth}
          disabled={health.status === 'checking'}
          aria-label={`Check health for ${target.name}`}
        >
          {health.status === 'checking' ? 'Checking...' : 'Check health'}
        </button>
      </div>

      <dl className="tp-target-details">
        <div>
          <dt>Device</dt>
          <dd>{target.config.device_profile.name}</dd>
        </div>
        <div>
          <dt>Viewport</dt>
          <dd>
            {target.config.device_profile.viewport_width} x {target.config.device_profile.viewport_height}
          </dd>
        </div>
        <div>
          <dt>Physical</dt>
          <dd>
            {target.config.device_profile.physical_width} x {target.config.device_profile.physical_height}
          </dd>
        </div>
        <div>
          <dt>Scale</dt>
          <dd>{target.config.device_profile.device_scale_factor}</dd>
        </div>
        <div>
          <dt>Status</dt>
          <dd>{revision?.health_status ?? 'Not checked'}</dd>
        </div>
      </dl>

      {health.status === 'checked' && health.data.error ? (
        <div className="tp-inline-alert" role="alert">
          {health.data.error.code}: {health.data.error.message}
        </div>
      ) : null}
      {health.status === 'error' ? (
        <div className="tp-inline-alert" role="alert">
          {health.message}
        </div>
      ) : null}
      {warnings.length > 0 ? (
        <ul className="tp-warning-list">
          {warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      ) : null}

      {revision ? (
        <div className="tp-target-metadata">
          <div>
            <span className="tp-subtitle">Build</span>
            <strong>{revision.metadata.simulator?.buildId ?? 'unknown'}</strong>
          </div>
          <div>
            <span className="tp-subtitle">Data</span>
            <strong>{revision.metadata.data?.revision ?? 'unpinned'}</strong>
          </div>
        </div>
      ) : null}

      {apps.length > 0 ? (
        <div className="tp-app-version-list">
          {apps.map((app) => (
            <div key={app.id} className="tp-app-version-row">
              <span>{app.displayNameEn ?? app.displayName} {app.version} ({app.versionCode})</span>
              <span>{app.packageName}</span>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function CreateTargetDialog({
  projectId,
  onCreated,
  onCancel,
}: {
  projectId: string;
  onCreated: (target: Target) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState('Local simulator');
  const [envUrl, setEnvUrl] = useState('http://127.0.0.1:5173');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanedName = name.trim().replace(/\s+/g, ' ');
    const cleanedUrl = envUrl.trim();
    if (!cleanedName) {
      setError('Target name is required.');
      return;
    }
    if (!cleanedUrl) {
      setError('Simulator endpoint URL is required.');
      return;
    }

    setError(null);
    setSubmitting(true);
    createSimulatorTarget({
      projectId,
      name: cleanedName,
      envUrl: cleanedUrl,
      deviceProfile: DEFAULT_DEVICE_PROFILE,
    })
      .then(onCreated)
      .catch((apiError) => {
        setError(apiError instanceof Error ? apiError.message : 'Target could not be created.');
      })
      .finally(() => {
        setSubmitting(false);
      });
  };

  return (
    <div className="tp-modal-backdrop">
      <form className="tp-modal" role="dialog" aria-modal="true" aria-label="Create target" onSubmit={submit}>
        <h2>New simulator target</h2>
        <label htmlFor="tp-target-name">Target name</label>
        <input
          id="tp-target-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoFocus
        />
        <label htmlFor="tp-target-url">Simulator endpoint</label>
        <input
          id="tp-target-url"
          value={envUrl}
          onChange={(event) => setEnvUrl(event.target.value)}
        />
        {error ? (
          <div className="tp-inline-alert" role="alert">
            {error}
          </div>
        ) : null}
        <div className="tp-modal-actions">
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" disabled={submitting}>
            Create target
          </button>
        </div>
      </form>
    </div>
  );
}
