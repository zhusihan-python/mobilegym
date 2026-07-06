import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';

import { createRun, importLegacyRun, listRuns, listWorkflows } from '../../api/client';
import type { CollectionResponse, Project, RunSummary, WorkflowSummary } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';

const AGENT_OPTIONS = [
  'generic_v2',
  'generic',
  'gui_owl',
  'uitars',
  'mai_ui',
  'autoglm',
  'gelab',
  'venus',
];
const AGENT_STORAGE_KEY = 'test-platform.launch.agent';
const MODEL_BASE_URL_STORAGE_KEY = 'test-platform.launch.model-base-url';
const MODEL_NAME_STORAGE_KEY = 'test-platform.launch.model-name';

type RunsState =
  | { status: 'loading' }
  | { status: 'loaded'; data: CollectionResponse<RunSummary> }
  | { status: 'error'; message: string };

export function RunsPage() {
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunsState>({ status: 'loading' });
  const [sourcePath, setSourcePath] = useState('');
  const [importName, setImportName] = useState('');
  const [importState, setImportState] = useState<
    { status: 'idle' } | { status: 'submitting' } | { status: 'error'; message: string }
  >({ status: 'idle' });

  // Launch run state
  const [workflows, setWorkflows] = useState<WorkflowSummary[]>([]);
  const [selectedVersionId, setSelectedVersionId] = useState('');
  const [runName, setRunName] = useState('');
  const [agent, setAgent] = useState(
    () => window.localStorage.getItem(AGENT_STORAGE_KEY) ?? 'generic_v2',
  );
  const [modelBaseUrl, setModelBaseUrl] = useState(
    () => window.localStorage.getItem(MODEL_BASE_URL_STORAGE_KEY) ?? '',
  );
  const [modelName, setModelName] = useState(
    () => window.localStorage.getItem(MODEL_NAME_STORAGE_KEY) ?? '',
  );
  const [launchState, setLaunchState] = useState<
    { status: 'idle' } | { status: 'submitting' } | { status: 'error'; message: string }
  >({ status: 'idle' });

  useEffect(() => {
    let active = true;
    listWorkflows(selectedProject.id)
      .then((data) => {
        if (active) {
          const published = data.items.filter((workflow) => workflow.latest_version?.status === 'published');
          setWorkflows(published);
          setSelectedVersionId(published[0]?.latest_version?.id ?? '');
          setLaunchState({ status: 'idle' });
        }
      })
      .catch(() => {
        // workflows load failure is non-fatal for runs page
      });
    return () => {
      active = false;
    };
  }, [selectedProject.id]);

  useEffect(() => {
    let active = true;

    listRuns(selectedProject.id)
      .then((data) => {
        if (active) {
          setRuns({ status: 'loaded', data });
        }
      })
      .catch((error) => {
        if (active) {
          setRuns({
            status: 'error',
            message: error instanceof Error ? error.message : 'Unable to load runs.',
          });
        }
      });

    return () => {
      active = false;
    };
  }, [selectedProject.id]);

  const submitImport = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedPath = sourcePath.trim();
    if (!trimmedPath) return;
    setImportState({ status: 'submitting' });
    importLegacyRun({
      projectId: selectedProject.id,
      sourcePath: trimmedPath,
      name: importName.trim() || undefined,
    })
      .then((run) => navigate(`/runs/${run.id}`))
      .catch((error) => {
        setImportState({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to import the legacy run.',
        });
      });
  };

  const runsContent = (() => {
    if (runs.status === 'loading') {
      return <section className="tp-panel">Loading runs...</section>;
    }

    if (runs.status === 'error') {
      return (
        <section className="tp-alert" role="alert">
          <h2>Runs could not be loaded</h2>
          <p>{runs.message}</p>
        </section>
      );
    }

    if (runs.data.items.length === 0) {
      return (
        <EmptyState
          title="No runs yet"
          body={`The API returned zero runs for ${selectedProject.name}.`}
        />
      );
    }

    return (
      <section className="tp-panel">
        <h2>Recent runs</h2>
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>State</th>
              <th>Created</th>
            </tr>
          </thead>
          <tbody>
            {runs.data.items.map((run) => (
              <tr key={run.id}>
                <td>
                  <Link to={`/runs/${run.id}`}>{run.name ?? run.id}</Link>
                  {run.imported ? <span className="tp-kicker"> imported</span> : null}
                </td>
                <td>{run.state}</td>
                <td>{run.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    );
  })();

  const submitLaunch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedAgent = agent.trim();
    const trimmedModelBaseUrl = modelBaseUrl.trim();
    const trimmedModelName = modelName.trim();
    if (!selectedVersionId || !trimmedAgent || !trimmedModelBaseUrl || !trimmedModelName) return;
    window.localStorage.setItem(AGENT_STORAGE_KEY, trimmedAgent);
    window.localStorage.setItem(MODEL_BASE_URL_STORAGE_KEY, trimmedModelBaseUrl);
    window.localStorage.setItem(MODEL_NAME_STORAGE_KEY, trimmedModelName);
    setLaunchState({ status: 'submitting' });
    createRun({
      workflowVersionId: selectedVersionId,
      name: runName.trim() || undefined,
      seed: Math.floor(Math.random() * 1000000),
      idempotencyKey: `launch-${Date.now()}`,
      execution: {
        agent: trimmedAgent,
        modelBaseUrl: trimmedModelBaseUrl,
        modelName: trimmedModelName,
      },
    })
      .then((run) => navigate(`/runs/${run.id}`))
      .catch((error) => {
        setLaunchState({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to create the run.',
        });
      });
  };

  return (
    <>
      {workflows.length > 0 ? (
        <section className="tp-panel">
          <h2>Launch run</h2>
          <form className="tp-form-grid" onSubmit={submitLaunch}>
            <label>
              <span>Workflow version</span>
              <select
                value={selectedVersionId}
                onChange={(event) => setSelectedVersionId(event.target.value)}
              >
                {workflows.map((wf) => (
                  <option key={wf.latest_version!.id} value={wf.latest_version!.id}>
                    {workflowVersionLabel(wf)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Run name</span>
              <input
                value={runName}
                onChange={(event) => setRunName(event.target.value)}
                placeholder="Optional"
              />
            </label>
            <label>
              <span>Agent</span>
              <select value={agent} onChange={(event) => setAgent(event.target.value)}>
                {AGENT_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {option}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Model base URL</span>
              <input
                value={modelBaseUrl}
                onChange={(event) => setModelBaseUrl(event.target.value)}
                placeholder="http://127.0.0.1:1234/v1"
              />
            </label>
            <label>
              <span>Model name</span>
              <input
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                placeholder="local-model"
              />
            </label>
            <button
              type="submit"
              disabled={
                launchState.status === 'submitting'
                || !selectedVersionId
                || !agent.trim()
                || !modelBaseUrl.trim()
                || !modelName.trim()
              }
            >
              {launchState.status === 'submitting' ? 'Launching...' : 'Launch run'}
            </button>
          </form>
          {launchState.status === 'error' ? (
            <p className="tp-error-text" role="alert">{launchState.message}</p>
          ) : null}
        </section>
      ) : null}
      <section className="tp-panel">
        <h2>Import legacy run</h2>
        <form className="tp-form-grid" onSubmit={submitImport}>
          <label>
            <span>Legacy run path</span>
            <input
              value={sourcePath}
              onChange={(event) => setSourcePath(event.target.value)}
              placeholder="/absolute/path/to/run"
            />
          </label>
          <label>
            <span>Display name</span>
            <input
              value={importName}
              onChange={(event) => setImportName(event.target.value)}
              placeholder="Optional"
            />
          </label>
          <button type="submit" disabled={importState.status === 'submitting' || !sourcePath.trim()}>
            {importState.status === 'submitting' ? 'Importing...' : 'Import legacy run'}
          </button>
        </form>
        {importState.status === 'error' ? (
          <p className="tp-error-text" role="alert">{importState.message}</p>
        ) : null}
      </section>
      {runsContent}
    </>
  );
}

function workflowVersionLabel(workflow: WorkflowSummary): string {
  const version = workflow.latest_version;
  if (!version) {
    return workflow.name;
  }
  return `${workflow.name} (v${version.version_no}, ${shortDefinitionHash(version.definition_hash)})`;
}

function shortDefinitionHash(hash: string): string {
  const [algorithm, digest] = hash.split(':', 2);
  if (!algorithm || !digest) {
    return hash.length > 18 ? `${hash.slice(0, 12)}...${hash.slice(-6)}` : hash;
  }
  if (digest.length <= 14) {
    return hash;
  }
  return `${algorithm}:${digest.slice(0, 8)}...${digest.slice(-6)}`;
}
