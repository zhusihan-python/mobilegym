import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';

import {
  checkModelCompatibility,
  createRun,
  importLegacyRun,
  listRuns,
  listWorkflows,
} from '../../api/client';
import type {
  CollectionResponse,
  CompatibilityResult,
  Project,
  RunSummary,
  WorkflowSummary,
} from '../../api/types';
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
const IMAGE_URL_FORMAT_STORAGE_KEY = 'test-platform.launch.image-url-format';

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
  const [modelApiKey, setModelApiKey] = useState('');
  const [imageUrlFormat, setImageUrlFormat] = useState(
    () => window.localStorage.getItem(IMAGE_URL_FORMAT_STORAGE_KEY) ?? 'data_url',
  );
  const [launchState, setLaunchState] = useState<
    { status: 'idle' } | { status: 'submitting' } | { status: 'error'; message: string }
  >({ status: 'idle' });

  // Compatibility check state
  const [compatState, setCompatState] = useState<
    | { status: 'idle' }
    | { status: 'checking' }
    | { status: 'loaded'; result: CompatibilityResult }
    | { status: 'error'; message: string }
  >({ status: 'idle' });
  const compatTokenRef = useRef(0);

  // Clear stale compatibility result when any form field changes.
  const clearCompatResult = () => {
    compatTokenRef.current += 1;
    setCompatState({ status: 'idle' });
  };

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
              <th>Execution</th>
              <th>Verdict</th>
              <th>Outcomes</th>
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
                <td>{run.gate_verdict ?? 'pending'}</td>
                <td>
                  <span>{run.outcome_counts.pass} pass</span>{' '}
                  <span>{run.outcome_counts.fail} fail</span>{' '}
                  <span>{run.outcome_counts.error} error</span>{' '}
                  <span>{run.outcome_counts.cancelled} cancelled</span>{' '}
                  <span>{run.outcome_counts.incomplete} incomplete</span>
                </td>
                <td>{run.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    );
  })();

  const testConnection = () => {
    const trimmedModelBaseUrl = modelBaseUrl.trim();
    const trimmedModelName = modelName.trim();
    if (!trimmedModelBaseUrl || !trimmedModelName) return;
    compatTokenRef.current += 1;
    const token = compatTokenRef.current;
    setCompatState({ status: 'checking' });
    checkModelCompatibility({
      modelBaseUrl: trimmedModelBaseUrl,
      modelName: trimmedModelName,
      modelApiKey: modelApiKey.trim() || undefined,
      imageUrlFormat: imageUrlFormat.trim() || 'data_url',
    })
      .then((result) => {
        if (token !== compatTokenRef.current) return;
        setCompatState({ status: 'loaded', result });
      })
      .catch((error) => {
        if (token !== compatTokenRef.current) return;
        setCompatState({
          status: 'error',
          message: error instanceof Error ? error.message : 'Compatibility check failed.',
        });
      });
  };

  const submitLaunch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const trimmedAgent = agent.trim();
    const trimmedModelBaseUrl = modelBaseUrl.trim();
    const trimmedModelName = modelName.trim();
    const trimmedModelApiKey = modelApiKey.trim();
    const trimmedImageUrlFormat = imageUrlFormat.trim() || 'data_url';
    if (!selectedVersionId || !trimmedAgent || !trimmedModelBaseUrl || !trimmedModelName) return;
    window.localStorage.setItem(AGENT_STORAGE_KEY, trimmedAgent);
    window.localStorage.setItem(MODEL_BASE_URL_STORAGE_KEY, trimmedModelBaseUrl);
    window.localStorage.setItem(MODEL_NAME_STORAGE_KEY, trimmedModelName);
    window.localStorage.setItem(IMAGE_URL_FORMAT_STORAGE_KEY, trimmedImageUrlFormat);
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
        modelApiKey: trimmedModelApiKey || undefined,
        imageUrlFormat: trimmedImageUrlFormat,
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
                onChange={(event) => {
                  setSelectedVersionId(event.target.value);
                  clearCompatResult();
                }}
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
              <select value={agent} onChange={(event) => {
                setAgent(event.target.value);
                clearCompatResult();
              }}>
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
                onChange={(event) => {
                  setModelBaseUrl(event.target.value);
                  clearCompatResult();
                }}
                placeholder="http://127.0.0.1:1234/v1"
              />
            </label>
            <label>
              <span>Model name</span>
              <input
                value={modelName}
                onChange={(event) => {
                  setModelName(event.target.value);
                  clearCompatResult();
                }}
                placeholder="local-model"
              />
            </label>
            <label>
              <span>Model API key</span>
              <input
                type="password"
                value={modelApiKey}
                onChange={(event) => {
                  setModelApiKey(event.target.value);
                  clearCompatResult();
                }}
                placeholder="Optional for local models"
                autoComplete="off"
              />
            </label>
            <label>
              <span>Image URL format</span>
              <select value={imageUrlFormat} onChange={(event) => {
                setImageUrlFormat(event.target.value);
                clearCompatResult();
              }}>
                <option value="data_url">OpenAI data URL</option>
                <option value="bare_base64">BigModel bare base64</option>
              </select>
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
            {agent.trim() === 'generic_v2' ? (
              <button
                type="button"
                onClick={testConnection}
                disabled={
                  compatState.status === 'checking'
                  || !modelBaseUrl.trim()
                  || !modelName.trim()
                }
                data-testid="tp-test-connection"
              >
                {compatState.status === 'checking' ? 'Checking...' : 'Test connection'}
              </button>
            ) : (
              <span data-testid="tp-test-connection-disabled" className="tp-hint">
                Compatibility check is available for generic_v2 only.
              </span>
            )}
          </form>
          {launchState.status === 'error' ? (
            <p className="tp-error-text" role="alert">{launchState.message}</p>
          ) : null}
          {compatState.status === 'loaded' ? (
            <div className="tp-compat-result" data-testid="tp-compat-result">
              <strong className={`tp-compat-code tp-compat-code-${compatState.result.code}`}>
                {compatState.result.code}
              </strong>
              <span>{compatState.result.explanation}</span>
              <span className="tp-mono">
                {compatState.result.checked_model} · {compatState.result.checked_image_format} · {compatState.result.latency_ms}ms
              </span>
            </div>
          ) : null}
          {compatState.status === 'error' ? (
            <p className="tp-error-text" role="alert">{compatState.message}</p>
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
