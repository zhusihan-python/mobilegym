import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';

import {
  ApiError,
  compileWorkflowPreview,
  createRun,
  createWorkflow,
  listTargets,
  listTasks,
  listWorkflows,
  publishWorkflow,
  updateWorkflowDraft,
} from '../../api/client';
import type {
  ConstraintViolation,
  Project,
  Target,
  TaskCatalogItem,
  WorkflowCompilePreview,
  WorkflowSummary,
  WorkflowVersion,
} from '../../api/types';
import { TargetComparisonPolicyFields } from './components/TargetComparisonPolicyFields';
import {
  buildWorkflowDefinition,
  canSubmitWorkflow,
  MANUAL_SEQUENCE_FAILURE_POLICY,
  MANUAL_SEQUENCE_STATE_POLICY,
  TARGET_COMPARISON_CONSTRAINTS,
  workflowDefinitionsEqual,
  type WorkflowMode,
} from './model';

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

type LoadState =
  | { status: 'loading' }
  | {
      status: 'loaded';
      tasks: TaskCatalogItem[];
      targets: Target[];
      workflows: WorkflowSummary[];
    }
  | { status: 'error'; message: string };

export function WorkflowsPage() {
  const navigate = useNavigate();
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [workflowMode, setWorkflowMode] = useState<WorkflowMode>('batch');
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
  const [targetId, setTargetId] = useState('');
  const [repeatCount, setRepeatCount] = useState(1);
  const [parallelCount, setParallelCount] = useState(1);
  const [processCount, setProcessCount] = useState(1);
  // Paired authoring defines target-free Workflow v2 Lane Slots. Exact Target
  // and Execution Profile revisions are selected later in Run Launch.
  const [pairedEnabled, setPairedEnabled] = useState(false);
  const [targetConstraints, setTargetConstraints] = useState<string[]>(
    TARGET_COMPARISON_CONSTRAINTS,
  );
  const [initialStatePolicy, setInitialStatePolicy] = useState('task_projection');
  const [executionMode, setExecutionMode] = useState('serial');
  const [activeWorkflow, setActiveWorkflow] = useState<WorkflowSummary | null>(null);
  const [preview, setPreview] = useState<WorkflowCompilePreview | null>(null);
  const [publishedVersion, setPublishedVersion] = useState<WorkflowVersion | null>(null);
  const [runSeed, setRunSeed] = useState(0);
  const [runAgent, setRunAgent] = useState(
    () => window.localStorage.getItem(AGENT_STORAGE_KEY) ?? 'generic_v2',
  );
  const [runModelBaseUrl, setRunModelBaseUrl] = useState(
    () => window.localStorage.getItem(MODEL_BASE_URL_STORAGE_KEY) ?? '',
  );
  const [runModelName, setRunModelName] = useState(
    () => window.localStorage.getItem(MODEL_NAME_STORAGE_KEY) ?? '',
  );
  const [runModelApiKey, setRunModelApiKey] = useState('');
  const [runImageUrlFormat, setRunImageUrlFormat] = useState(
    () => window.localStorage.getItem(IMAGE_URL_FORMAT_STORAGE_KEY) ?? 'data_url',
  );
  const [error, setError] = useState<string | null>(null);
  // VS-10 Contract 3: create-run is the authoritative gate (409). When a paired
  // run is rejected for constraint violations, surface the structured list.
  const [runViolations, setRunViolations] = useState<ConstraintViolation[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    setState({ status: 'loading' });
    Promise.all([
      listTasks(),
      listTargets(selectedProject.id),
      listWorkflows(selectedProject.id),
    ])
      .then(([tasksResponse, targetsResponse, workflowsResponse]) => {
        if (!active) return;
        const firstTargetId = targetsResponse.items[0]?.id ?? '';
        setState({
          status: 'loaded',
          tasks: tasksResponse.items,
          targets: targetsResponse.items,
          workflows: workflowsResponse.items,
        });
        setTargetId((current) => current || firstTargetId);
        const firstWorkflow = workflowsResponse.items[0] ?? null;
        setActiveWorkflow(firstWorkflow);
        setPublishedVersion(firstWorkflow?.latest_version ?? null);
      })
      .catch((loadError) => {
        if (active) {
          setState({
            status: 'error',
            message: loadError instanceof Error ? loadError.message : 'Unable to load workflows.',
          });
        }
      });
    return () => {
      active = false;
    };
  }, [selectedProject.id]);

  const visibleTasks = useMemo(() => {
    if (state.status !== 'loaded') return [];
    return state.tasks;
  }, [state]);

  const isManualSequence = workflowMode === 'manual_sequence';
  const effectivePairedEnabled = pairedEnabled && !isManualSequence;

  const definition = useMemo(
    () =>
      buildWorkflowDefinition(
        {
          workflowMode,
          selectedTaskIds,
          targetId,
          repeatCount,
          parallelCount,
          processCount,
          paired: effectivePairedEnabled
            ? {
                targetConstraints,
                initialStatePolicy,
                execution: executionMode,
              }
            : null,
        },
      ),
    [
      workflowMode,
      repeatCount,
      selectedTaskIds,
      targetId,
      parallelCount,
      processCount,
      effectivePairedEnabled,
      targetConstraints,
      initialStatePolicy,
      executionMode,
    ],
  );

  const toggleTask = (taskId: string) => {
    setSelectedTaskIds((current) =>
      current.includes(taskId)
        ? current.filter((item) => item !== taskId)
        : [...current, taskId],
    );
    setPreview(null);
    setPublishedVersion(null);
  };

  const moveSelectedTask = (taskId: string, direction: -1 | 1) => {
    setSelectedTaskIds((current) => {
      const index = current.indexOf(taskId);
      const nextIndex = index + direction;
      if (index < 0 || nextIndex < 0 || nextIndex >= current.length) {
        return current;
      }
      const next = [...current];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
    setPreview(null);
    setPublishedVersion(null);
  };

  const removeSelectedTask = (taskId: string) => {
    setSelectedTaskIds((current) => current.filter((item) => item !== taskId));
    setPreview(null);
    setPublishedVersion(null);
  };

  const saveDraft = async (): Promise<WorkflowSummary> => {
    if (activeWorkflow) {
      if (workflowDefinitionsEqual(activeWorkflow.draft_definition, definition)) {
        return activeWorkflow;
      }
      const updated = await updateWorkflowDraft({
        workflowId: activeWorkflow.id,
        name: definition.name,
        definition,
      });
      setActiveWorkflow(updated);
      return updated;
    }

    const created = await createWorkflow({
      projectId: selectedProject.id,
      name: definition.name,
      definition,
    });
    setActiveWorkflow(created);
    return created;
  };

  const validatePreview = () => {
    setBusy(true);
    setError(null);
    saveDraft()
      .then((workflow) => compileWorkflowPreview(workflow.id))
      .then((compiled) => {
        setPreview(compiled);
        setPublishedVersion(null);
      })
      .catch((workflowError) => {
        setError(workflowError instanceof Error ? workflowError.message : 'Workflow preview failed.');
      })
      .finally(() => setBusy(false));
  };

  const publish = () => {
    setBusy(true);
    setError(null);
    saveDraft()
      .then((workflow) => publishWorkflow(workflow.id))
      .then((response) => {
        setPublishedVersion(response.version);
      })
      .catch((workflowError) => {
        setError(workflowError instanceof Error ? workflowError.message : 'Workflow publication failed.');
      })
      .finally(() => setBusy(false));
  };

  const launch = () => {
    if (!publishedVersion) return;
    const trimmedAgent = runAgent.trim();
    const trimmedModelBaseUrl = runModelBaseUrl.trim();
    const trimmedModelName = runModelName.trim();
    const trimmedModelApiKey = runModelApiKey.trim();
    const trimmedImageUrlFormat = runImageUrlFormat.trim() || 'data_url';
    if (!trimmedAgent || !trimmedModelBaseUrl || !trimmedModelName) return;
    window.localStorage.setItem(AGENT_STORAGE_KEY, trimmedAgent);
    window.localStorage.setItem(MODEL_BASE_URL_STORAGE_KEY, trimmedModelBaseUrl);
    window.localStorage.setItem(MODEL_NAME_STORAGE_KEY, trimmedModelName);
    window.localStorage.setItem(IMAGE_URL_FORMAT_STORAGE_KEY, trimmedImageUrlFormat);
    setBusy(true);
    setError(null);
    setRunViolations([]);
    createRun({
      workflowVersionId: publishedVersion.id,
      name: activeWorkflow?.name ?? publishedVersion.definition.name,
      seed: runSeed,
      idempotencyKey: crypto.randomUUID(),
      execution: {
        agent: trimmedAgent,
        modelBaseUrl: trimmedModelBaseUrl,
        modelName: trimmedModelName,
        modelApiKey: trimmedModelApiKey || undefined,
        imageUrlFormat: trimmedImageUrlFormat,
      },
    })
      .then((run) => navigate(`/runs/${run.id}`))
      .catch((runError) => {
        // VS-10 Contract 3: a 409 COMPARISON_CONSTRAINT_VIOLATED carries the
        // structured violation list on error.details. Surface those in the UI;
        // any other error degrades to the generic message path.
        if (runError instanceof ApiError && runError.code === 'COMPARISON_CONSTRAINT_VIOLATED') {
          const violations = (runError.details as unknown[]).filter(
            (item): item is ConstraintViolation =>
              Boolean(item) && typeof item === 'object' && 'code' in (item as Record<string, unknown>),
          );
          setRunViolations(violations);
          setError(runError.message || 'Comparison constraints are not satisfied.');
        } else {
          setError(runError instanceof Error ? runError.message : 'Run creation failed.');
        }
      })
      .finally(() => setBusy(false));
  };

  if (state.status === 'loading') {
    return <section className="tp-panel">Loading workflows...</section>;
  }

  if (state.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Workflows could not be loaded</h2>
        <p>{state.message}</p>
      </section>
    );
  }

  return (
    <>
      <section className="tp-panel tp-page-actions">
        <div>
          <h2>Workflow draft</h2>
          <p>Select tasks and a simulator target before publishing an immutable version.</p>
        </div>
        <div className="tp-workflow-actions">
          <button
            type="button"
            onClick={validatePreview}
            disabled={
              busy ||
              !canSubmitWorkflow(selectedTaskIds, targetId, effectivePairedEnabled)
            }
          >
            Validate preview
          </button>
          <button
            type="button"
            onClick={publish}
            disabled={
              busy ||
              !canSubmitWorkflow(selectedTaskIds, targetId, effectivePairedEnabled)
            }
          >
            Publish workflow
          </button>
          {publishedVersion ? (
            <button
              type="button"
              onClick={
                publishedVersion.definition.schema_version === 2
                  ? () => navigate('/run-launch')
                  : launch
              }
              disabled={
                busy
                || (
                  publishedVersion.definition.schema_version === 1
                  && (!runAgent.trim() || !runModelBaseUrl.trim() || !runModelName.trim())
                )
              }
            >
              {publishedVersion.definition.schema_version === 2
                ? 'Open Run Launch'
                : `Launch version ${publishedVersion.version_no}`}
            </button>
          ) : null}
        </div>
      </section>

      <section className="tp-panel tp-workflow-editor">
        <div className="tp-workflow-mode">
          <label className="tp-segmented-control">
            <span>Mode</span>
            <select
              aria-label="Workflow mode"
              value={workflowMode}
              onChange={(event) => {
                const nextMode = event.currentTarget.value as WorkflowMode;
                setWorkflowMode(nextMode);
                if (nextMode === 'manual_sequence') {
                  setPairedEnabled(false);
                  setRepeatCount(1);
                  setParallelCount(1);
                  setProcessCount(1);
                }
                setPreview(null);
                setPublishedVersion(null);
              }}
            >
              <option value="batch">Batch</option>
              <option value="manual_sequence">Manual sequence</option>
            </select>
          </label>
        </div>

        <div className="tp-workflow-task-column">
          <div className="tp-workflow-task-list" aria-label="Workflow tasks">
            {visibleTasks.map((task) => (
              <label key={task.task_base_id} className="tp-checkbox-row">
                <input
                  type="checkbox"
                  checked={selectedTaskIds.includes(task.task_base_id)}
                  onChange={() => toggleTask(task.task_base_id)}
                  aria-label={`Select ${task.task_base_id}`}
                />
                <span>{task.task_base_id}</span>
              </label>
            ))}
          </div>

          {isManualSequence ? (
            <div className="tp-workflow-sequence" aria-label="Manual sequence order">
              <h3>Manual order</h3>
              <dl className="tp-workflow-policy-facts" aria-label="Manual sequence policies">
                <div>
                  <dt>State policy</dt>
                  <dd>{MANUAL_SEQUENCE_STATE_POLICY}</dd>
                </div>
                <div>
                  <dt>Failure policy</dt>
                  <dd>{MANUAL_SEQUENCE_FAILURE_POLICY}</dd>
                </div>
              </dl>
              {selectedTaskIds.length > 0 ? (
                <ol>
                  {selectedTaskIds.map((taskId, index) => (
                    <li key={taskId}>
                      <span>{taskId}</span>
                      <div>
                        <button
                          type="button"
                          onClick={() => moveSelectedTask(taskId, -1)}
                          disabled={index === 0}
                          aria-label={`Move ${taskId} up`}
                        >
                          Up
                        </button>
                        <button
                          type="button"
                          onClick={() => moveSelectedTask(taskId, 1)}
                          disabled={index === selectedTaskIds.length - 1}
                          aria-label={`Move ${taskId} down`}
                        >
                          Down
                        </button>
                        <button
                          type="button"
                          onClick={() => removeSelectedTask(taskId)}
                          aria-label={`Remove ${taskId}`}
                        >
                          Remove
                        </button>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <p>No tasks selected</p>
              )}
            </div>
          ) : null}
        </div>

        <div className="tp-workflow-controls">
          <label htmlFor="tp-workflow-target">Target</label>
          <select
            id="tp-workflow-target"
            value={targetId}
            onChange={(event) => {
              setTargetId(event.target.value);
              setPreview(null);
              setPublishedVersion(null);
            }}
          >
            <option value="">Select target</option>
            {state.targets.map((target) => (
              <option key={target.id} value={target.id}>
                {target.name}
              </option>
            ))}
          </select>

          <label htmlFor="tp-workflow-repeat">Repeat count</label>
          <input
            id="tp-workflow-repeat"
            type="number"
            min={1}
            value={repeatCount}
            disabled={isManualSequence}
            onChange={(event) => {
              setRepeatCount(Math.max(1, Number(event.target.value) || 1));
              setPreview(null);
              setPublishedVersion(null);
            }}
          />

          <label htmlFor="tp-workflow-parallel">Parallel workers</label>
          <input
            id="tp-workflow-parallel"
            type="number"
            min={1}
            value={parallelCount}
            disabled={isManualSequence}
            onChange={(event) => {
              setParallelCount(Math.max(1, Number(event.target.value) || 1));
              setPreview(null);
              setPublishedVersion(null);
            }}
          />

          <label htmlFor="tp-workflow-processes">Processes</label>
          <input
            id="tp-workflow-processes"
            type="number"
            min={1}
            value={processCount}
            disabled={isManualSequence}
            onChange={(event) => {
              setProcessCount(Math.max(1, Number(event.target.value) || 1));
              setPreview(null);
              setPublishedVersion(null);
            }}
          />

          <label htmlFor="tp-workflow-seed">Run seed</label>
          <input
            id="tp-workflow-seed"
            type="number"
            value={runSeed}
            onChange={(event) => setRunSeed(Number(event.target.value) || 0)}
          />

          <label htmlFor="tp-workflow-agent">Agent</label>
          <select
            id="tp-workflow-agent"
            value={runAgent}
            onChange={(event) => setRunAgent(event.target.value)}
          >
            {AGENT_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>

          <label htmlFor="tp-workflow-model-base-url">Model base URL</label>
          <input
            id="tp-workflow-model-base-url"
            value={runModelBaseUrl}
            onChange={(event) => setRunModelBaseUrl(event.target.value)}
            placeholder="http://127.0.0.1:1234/v1"
          />

          <label htmlFor="tp-workflow-model-name">Model name</label>
          <input
            id="tp-workflow-model-name"
            value={runModelName}
            onChange={(event) => setRunModelName(event.target.value)}
            placeholder="local-model"
          />

          <label htmlFor="tp-workflow-model-api-key">Model API key</label>
          <input
            id="tp-workflow-model-api-key"
            type="password"
            value={runModelApiKey}
            onChange={(event) => setRunModelApiKey(event.target.value)}
            placeholder="Optional for local models"
            autoComplete="off"
          />

          <label htmlFor="tp-workflow-image-url-format">Image URL format</label>
          <select
            id="tp-workflow-image-url-format"
            value={runImageUrlFormat}
            onChange={(event) => setRunImageUrlFormat(event.target.value)}
          >
            <option value="data_url">OpenAI data URL</option>
            <option value="bare_base64">BigModel bare base64</option>
          </select>
        </div>

        <TargetComparisonPolicyFields
          enabled={effectivePairedEnabled}
          disabled={isManualSequence}
          policy={{
            targetConstraints,
            initialStatePolicy,
            execution: executionMode,
          }}
          onEnabledChange={(enabled) => {
            setPairedEnabled(enabled);
            setPreview(null);
            setPublishedVersion(null);
          }}
          onPolicyChange={(patch) => {
            if (patch.targetConstraints) setTargetConstraints(patch.targetConstraints);
            if (patch.initialStatePolicy) setInitialStatePolicy(patch.initialStatePolicy);
            if (patch.execution) setExecutionMode(patch.execution);
            setPreview(null);
            setPublishedVersion(null);
          }}
        />
      </section>

      {error ? (
        <section className="tp-inline-alert" role="alert">
          {error}
        </section>
      ) : null}

      {preview ? (
        <section className="tp-panel">
          <h2>Compile preview</h2>
          <p>{preview.total_episodes} total episodes</p>
          <dl className="tp-workflow-preview-facts">
            <div>
              <dt>Strategy</dt>
              <dd>{preview.execution_strategy}</dd>
            </div>
            <div>
              <dt>Order</dt>
              <dd>{preview.ordered_task_ids.join(' -> ')}</dd>
            </div>
          </dl>
          {preview.violations && preview.violations.length > 0 ? (
            <div data-testid="tp-constraint-violations" className="tp-constraint-violations">
              <p className="tp-inline-alert">
                {preview.violations.length} target constraint{' '}
                {preview.violations.length === 1 ? 'violation' : 'violations'} (advisory; create-run
                will reject)
              </p>
              <ul>
                {preview.violations.map((violation, index) => (
                  <li key={`${violation.code}-${index}`}>
                    <code>{violation.code}</code>
                    <span> — {violation.message}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}

      {publishedVersion ? (
        <section className="tp-panel">
          <h2>Published version {publishedVersion.version_no}</h2>
          <p>Definition hash {publishedVersion.definition_hash}</p>
        </section>
      ) : null}

      {runViolations.length > 0 ? (
        <section
          className="tp-alert"
          role="alert"
          data-testid="tp-run-violations"
        >
          <h2>Run rejected: comparison constraints violated</h2>
          <ul>
            {runViolations.map((violation, index) => (
              <li key={`${violation.code}-${index}`}>
                <code>{violation.code}</code>
                <span> — {violation.message}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </>
  );
}
