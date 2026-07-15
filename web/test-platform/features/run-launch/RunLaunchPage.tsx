import { useEffect, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';

import {
  createRunLaunch,
  listExecutionProfiles,
  listTargets,
  listWorkflows,
  previewRunLaunch,
} from '../../api/client';
import type {
  ExecutionProfile,
  Project,
  RunLaunchCommand,
  RunLaunchPreview,
  Target,
  WorkflowSummary,
} from '../../api/types';

type CatalogState =
  | { status: 'loading' }
  | {
    status: 'loaded';
    workflows: WorkflowSummary[];
    targets: Target[];
    profiles: ExecutionProfile[];
  }
  | { status: 'error'; message: string };

export function RunLaunchPage() {
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<CatalogState>({ status: 'loading' });
  const [workflowVersionId, setWorkflowVersionId] = useState('');
  const [targetRevisionId, setTargetRevisionId] = useState('');
  const [profileRevisionId, setProfileRevisionId] = useState('');
  const [name, setName] = useState('');
  const [seed, setSeed] = useState('20260715');
  const [preview, setPreview] = useState<RunLaunchPreview | null>(null);
  const [secretBindings, setSecretBindings] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<'preview' | 'create' | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setCatalog({ status: 'loading' });
    setPreview(null);
    setSecretBindings({});
    Promise.all([
      listWorkflows(selectedProject.id),
      listTargets(selectedProject.id),
      listExecutionProfiles(selectedProject.id),
    ])
      .then(([workflowResponse, targetResponse, profileResponse]) => {
        if (!active) return;
        const workflows = workflowResponse.items.filter(
          (workflow) => workflow.latest_version?.definition.schema_version === 2,
        );
        const targets = targetResponse.items.filter(
          (target) => target.latest_revision?.health_status === 'healthy',
        );
        const profiles = profileResponse.items.filter(
          (profile) => profile.head_revision !== null,
        );
        setCatalog({ status: 'loaded', workflows, targets, profiles });
        const firstWorkflow = workflows[0]?.latest_version;
        setWorkflowVersionId(firstWorkflow?.id ?? '');
        setTargetRevisionId(targets[0]?.latest_revision?.id ?? '');
        setProfileRevisionId(profiles[0]?.head_revision?.id ?? '');
        setName(firstWorkflow?.definition.name ?? '');
      })
      .catch((loadError) => {
        if (!active) return;
        setCatalog({
          status: 'error',
          message: loadError instanceof Error ? loadError.message : 'Run Launch could not be loaded.',
        });
      });
    return () => {
      active = false;
    };
  }, [selectedProject.id]);

  const command = (): RunLaunchCommand => ({
    project_id: selectedProject.id,
    workflow_version_id: workflowVersionId,
    name: name.trim() || undefined,
    seed: Number(seed),
    comparison_intent: 'single',
    lane_bindings: [{
      lane_slot: 'candidate',
      target_revision_id: targetRevisionId,
      execution_profile_revision_id: profileRevisionId,
    }],
  });

  const clearPreview = () => {
    setPreview(null);
    setSecretBindings({});
    setError(null);
  };

  const runPreview = () => {
    setBusy('preview');
    setError(null);
    previewRunLaunch(command())
      .then((value) => {
        setSecretBindings({});
        setPreview(value);
      })
      .catch((previewError) => {
        setPreview(null);
        setError(previewError instanceof Error ? previewError.message : 'Launch preview failed.');
      })
      .finally(() => setBusy(null));
  };

  const create = () => {
    if (!preview) return;
    setBusy('create');
    setError(null);
    createRunLaunch({
      command: command(),
      previewToken: preview.preview_token,
      idempotencyKey: crypto.randomUUID(),
      secretBindings,
    })
      .then((run) => {
        setSecretBindings({});
        navigate(`/runs/${run.id}`);
      })
      .catch((createError) => {
        setError(createError instanceof Error ? createError.message : 'Run creation failed.');
      })
      .finally(() => setBusy(null));
  };

  if (catalog.status === 'loading') {
    return <section className="tp-panel">Loading Run Launch...</section>;
  }
  if (catalog.status === 'error') {
    return <section className="tp-alert" role="alert"><h2>Run Launch could not be loaded</h2><p>{catalog.message}</p></section>;
  }

  const ready = Boolean(
    workflowVersionId
    && targetRevisionId
    && profileRevisionId
    && Number.isInteger(Number(seed)),
  );
  const secretsReady = preview?.credential_requirements.every(
    (slot) => Boolean(secretBindings[slot]?.trim()),
  ) ?? false;

  return (
    <>
      <section className="tp-panel">
        <h2>Run Launch</h2>
        <p>Bind one candidate Lane Slot to exact reviewed revisions.</p>
        <div className="tp-form-grid">
          <label>
            <span>Workflow Version</span>
            <select
              value={workflowVersionId}
              onChange={(event) => {
                setWorkflowVersionId(event.target.value);
                const selected = catalog.workflows.find(
                  (workflow) => workflow.latest_version?.id === event.target.value,
                );
                setName(selected?.latest_version?.definition.name ?? '');
                clearPreview();
              }}
            >
              {catalog.workflows.map((workflow) => (
                <option key={workflow.latest_version!.id} value={workflow.latest_version!.id}>
                  {workflow.name} / v{workflow.latest_version!.version_no} / {workflow.latest_version!.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Target Revision</span>
            <select value={targetRevisionId} onChange={(event) => { setTargetRevisionId(event.target.value); clearPreview(); }}>
              {catalog.targets.map((target) => (
                <option key={target.latest_revision!.id} value={target.latest_revision!.id}>
                  {target.name} / {target.latest_revision!.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Execution Profile Revision</span>
            <select value={profileRevisionId} onChange={(event) => { setProfileRevisionId(event.target.value); clearPreview(); }}>
              {catalog.profiles.map((profile) => (
                <option key={profile.head_revision!.id} value={profile.head_revision!.id}>
                  {profile.name} / revision {profile.head_revision!.revision_no} / {profile.head_revision!.id}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Run name</span>
            <input value={name} onChange={(event) => { setName(event.target.value); clearPreview(); }} />
          </label>
          <label>
            <span>Seed</span>
            <input type="number" value={seed} onChange={(event) => { setSeed(event.target.value); clearPreview(); }} />
          </label>
          <button type="button" onClick={runPreview} disabled={!ready || busy !== null}>
            {busy === 'preview' ? 'Previewing...' : 'Preview launch'}
          </button>
        </div>
        {!ready ? <p className="tp-kicker">A published Workflow v2, healthy Target Revision, and published Execution Profile Revision are required.</p> : null}
        {error ? <div className="tp-inline-alert" role="alert">{error}</div> : null}
      </section>

      {preview ? (
        <section className="tp-panel" data-testid="tp-run-launch-preview">
          <div className="tp-run-heading">
            <div>
              <h2>Exact launch preview</h2>
              <p>{preview.episode_count} Prepared Episode / {preview.comparison_intent}</p>
            </div>
            <button
              type="button"
              onClick={create}
              disabled={busy !== null || !secretsReady}
            >
              {busy === 'create' ? 'Creating...' : 'Create run'}
            </button>
          </div>
          <dl className="tp-run-facts">
            <div><dt>Workflow Version</dt><dd className="tp-mono">{preview.workflow_version_id}</dd></div>
            <div><dt>Workflow hash</dt><dd className="tp-mono">{preview.workflow_version_hash}</dd></div>
            <div><dt>Run Plan fingerprint</dt><dd className="tp-mono">{preview.run_plan_fingerprint}</dd></div>
            <div><dt>Preview token</dt><dd className="tp-mono">{preview.preview_token}</dd></div>
          </dl>
          {preview.credential_requirements.length > 0 ? (
            <div className="tp-form-grid">
              <h3>Transient credentials</h3>
              {preview.credential_requirements.map((slot) => (
                <label key={slot}>
                  <span>{credentialSlotLabel(slot)}</span>
                  <input
                    type="password"
                    autoComplete="off"
                    value={secretBindings[slot] ?? ''}
                    onChange={(event) => {
                      const value = event.target.value;
                      setSecretBindings((current) => ({
                        ...current,
                        [slot]: value,
                      }));
                    }}
                  />
                </label>
              ))}
              <p className="tp-kicker">
                Values are used only for this launch and are not stored by the browser.
              </p>
            </div>
          ) : null}
          {preview.lane_bindings.map((binding) => (
            <dl className="tp-run-facts" key={binding.lane_slot}>
              <div><dt>Lane Slot</dt><dd>{binding.lane_slot}</dd></div>
              <div><dt>Target Revision</dt><dd className="tp-mono">{binding.target_revision_id}</dd></div>
              <div><dt>Target hash</dt><dd className="tp-mono">{binding.target_revision_hash}</dd></div>
              <div><dt>Execution Profile Revision</dt><dd className="tp-mono">{binding.execution_profile_revision_id}</dd></div>
              <div><dt>Public spec hash</dt><dd className="tp-mono">{binding.execution_profile_public_hash}</dd></div>
              <div><dt>Lane fingerprint</dt><dd className="tp-mono">{binding.lane_fingerprint}</dd></div>
            </dl>
          ))}
        </section>
      ) : null}
    </>
  );
}

function credentialSlotLabel(slot: string): string {
  return slot === 'model_api_key' ? 'Model API key' : slot;
}
