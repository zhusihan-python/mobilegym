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
  RunLaunchPreview as RunLaunchPreviewData,
  Target,
  WorkflowSummary,
} from '../../api/types';
import { RunLaunchForm } from './components/RunLaunchForm';
import { RunLaunchPreview } from './components/RunLaunchPreview';
import {
  buildRunLaunchCommand,
  initialRunLaunchSelection,
  isRunLaunchReady,
  selectionForWorkflow,
  type RunLaunchSelection,
} from './model';

type CatalogState =
  | { status: 'loading' }
  | {
    status: 'loaded';
    workflows: WorkflowSummary[];
    targets: Target[];
    profiles: ExecutionProfile[];
  }
  | { status: 'error'; message: string };

const EMPTY_SELECTION: RunLaunchSelection = {
  workflowVersionId: '',
  comparisonIntent: 'single',
  baselineTargetRevisionId: '',
  candidateTargetRevisionId: '',
  profileRevisionId: '',
  name: '',
  seed: '20260715',
};

export function RunLaunchPage() {
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const navigate = useNavigate();
  const [catalog, setCatalog] = useState<CatalogState>({ status: 'loading' });
  const [selection, setSelection] = useState<RunLaunchSelection>(EMPTY_SELECTION);
  const [preview, setPreview] = useState<RunLaunchPreviewData | null>(null);
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
        setSelection(initialRunLaunchSelection({ workflows, targets, profiles }));
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

  const clearPreview = () => {
    setPreview(null);
    setSecretBindings({});
    setError(null);
  };

  const changeSelection = (patch: Partial<RunLaunchSelection>) => {
    setSelection((current) => ({ ...current, ...patch }));
    clearPreview();
  };

  const changeWorkflow = (workflowVersionId: string) => {
    if (catalog.status !== 'loaded') return;
    setSelection((current) => ({
      ...current,
      ...selectionForWorkflow(workflowVersionId, catalog.workflows, catalog.targets),
    }));
    clearPreview();
  };

  const command = buildRunLaunchCommand(selectedProject.id, selection);
  const runPreview = () => {
    setBusy('preview');
    setError(null);
    previewRunLaunch(command)
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
      command,
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
    return (
      <section className="tp-alert" role="alert">
        <h2>Run Launch could not be loaded</h2>
        <p>{catalog.message}</p>
      </section>
    );
  }

  const ready = isRunLaunchReady(selection);
  const secretsReady = preview?.credential_requirements.every(
    (slot) => Boolean(secretBindings[slot]?.trim()),
  ) ?? false;

  return (
    <>
      <RunLaunchForm
        workflows={catalog.workflows}
        targets={catalog.targets}
        profiles={catalog.profiles}
        selection={selection}
        disabled={busy !== null}
        previewing={busy === 'preview'}
        ready={ready}
        onChange={changeSelection}
        onWorkflowChange={changeWorkflow}
        onPreview={runPreview}
      />
      {error ? <div className="tp-inline-alert" role="alert">{error}</div> : null}
      {preview ? (
        <RunLaunchPreview
          preview={preview}
          secretBindings={secretBindings}
          creating={busy === 'create'}
          createDisabled={
            busy !== null
            || !secretsReady
            || preview.constraint_violations.length > 0
          }
          onSecretBindingChange={(slot, value) => {
            setSecretBindings((current) => ({ ...current, [slot]: value }));
          }}
          onCreate={create}
        />
      ) : null}
    </>
  );
}
