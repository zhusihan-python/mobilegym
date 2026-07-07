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
  WorkflowDefinition,
  WorkflowSummary,
  WorkflowVersion,
} from '../../api/types';

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
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
  const [targetId, setTargetId] = useState('');
  const [repeatCount, setRepeatCount] = useState(1);
  const [parallelCount, setParallelCount] = useState(1);
  const [processCount, setProcessCount] = useState(1);
  // VS-10 Contract 2: paired comparison policy (baseline vs candidate). When
  // enabled, the matrix node grows a second lane and the definition gains a
  // compare node carrying the three policy axes.
  const [pairedEnabled, setPairedEnabled] = useState(false);
  const [baselineTargetId, setBaselineTargetId] = useState('');
  const [candidateTargetId, setCandidateTargetId] = useState('');
  const [targetConstraints, setTargetConstraints] = useState<string[]>([
    'same_app',
    'same_device',
    'same_data',
  ]);
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
        const secondTargetId = targetsResponse.items[1]?.id ?? firstTargetId;
        setState({
          status: 'loaded',
          tasks: tasksResponse.items,
          targets: targetsResponse.items,
          workflows: workflowsResponse.items,
        });
        setTargetId((current) => current || firstTargetId);
        setBaselineTargetId((current) => current || firstTargetId);
        setCandidateTargetId((current) => current || secondTargetId);
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

  const definition = useMemo(
    () =>
      buildDefinition(
        {
          selectedTaskIds,
          targetId,
          repeatCount,
          parallelCount,
          processCount,
          paired: pairedEnabled
            ? {
                baselineTargetId,
                candidateTargetId,
                targetConstraints,
                initialStatePolicy,
                execution: executionMode,
              }
            : null,
        },
      ),
    [
      repeatCount,
      selectedTaskIds,
      targetId,
      parallelCount,
      processCount,
      pairedEnabled,
      baselineTargetId,
      candidateTargetId,
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

  const saveDraft = async (): Promise<WorkflowSummary> => {
    if (activeWorkflow) {
      if (definitionsEqual(activeWorkflow.draft_definition, definition)) {
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
    if (!trimmedAgent || !trimmedModelBaseUrl || !trimmedModelName) return;
    window.localStorage.setItem(AGENT_STORAGE_KEY, trimmedAgent);
    window.localStorage.setItem(MODEL_BASE_URL_STORAGE_KEY, trimmedModelBaseUrl);
    window.localStorage.setItem(MODEL_NAME_STORAGE_KEY, trimmedModelName);
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
          <button type="button" onClick={validatePreview} disabled={busy || !canSubmit(selectedTaskIds, targetId, pairedEnabled, baselineTargetId, candidateTargetId)}>
            Validate preview
          </button>
          <button type="button" onClick={publish} disabled={busy || !canSubmit(selectedTaskIds, targetId, pairedEnabled, baselineTargetId, candidateTargetId)}>
            Publish workflow
          </button>
          {publishedVersion ? (
            <button
              type="button"
              onClick={launch}
              disabled={busy || !runAgent.trim() || !runModelBaseUrl.trim() || !runModelName.trim()}
            >
              Launch version {publishedVersion.version_no}
            </button>
          ) : null}
        </div>
      </section>

      <section className="tp-panel tp-workflow-editor">
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
        </div>

        {/* VS-10 Contract 2: paired comparison policy (baseline vs candidate).
            When enabled the matrix node grows a second lane and the definition
            gains a compare node carrying the three policy axes. */}
        <div className="tp-workflow-paired">
          <label className="tp-checkbox-row">
            <input
              type="checkbox"
              checked={pairedEnabled}
              onChange={(event) => {
                setPairedEnabled(event.target.checked);
                setPreview(null);
                setPublishedVersion(null);
              }}
              aria-label="Paired comparison (baseline vs candidate)"
            />
            <span>Paired comparison (baseline vs candidate)</span>
          </label>

          {pairedEnabled ? (
            <div className="tp-workflow-paired-controls">
              <label htmlFor="tp-workflow-baseline-target">Baseline target</label>
              <select
                id="tp-workflow-baseline-target"
                value={baselineTargetId}
                onChange={(event) => {
                  setBaselineTargetId(event.target.value);
                  setPreview(null);
                  setPublishedVersion(null);
                }}
              >
                <option value="">Select baseline target</option>
                {state.targets.map((target) => (
                  <option key={target.id} value={target.id}>
                    {target.name}
                  </option>
                ))}
              </select>

              <label htmlFor="tp-workflow-candidate-target">Candidate target</label>
              <select
                id="tp-workflow-candidate-target"
                value={candidateTargetId}
                onChange={(event) => {
                  setCandidateTargetId(event.target.value);
                  setPreview(null);
                  setPublishedVersion(null);
                }}
              >
                <option value="">Select candidate target</option>
                {state.targets.map((target) => (
                  <option key={target.id} value={target.id}>
                    {target.name}
                  </option>
                ))}
              </select>

              <fieldset className="tp-workflow-constraints">
                <legend>Target constraints</legend>
                {['same_app', 'same_device', 'same_data'].map((axis) => (
                  <label key={axis} className="tp-checkbox-row">
                    <input
                      type="checkbox"
                      checked={targetConstraints.includes(axis)}
                      onChange={(event) => {
                        setTargetConstraints((current) =>
                          event.target.checked
                            ? [...current, axis]
                            : current.filter((item) => item !== axis),
                        );
                        setPreview(null);
                        setPublishedVersion(null);
                      }}
                      aria-label={`Constraint ${axis}`}
                    />
                    <span>{axis}</span>
                  </label>
                ))}
              </fieldset>

              <label htmlFor="tp-workflow-initial-state-policy">Initial state policy</label>
              <select
                id="tp-workflow-initial-state-policy"
                value={initialStatePolicy}
                onChange={(event) => {
                  setInitialStatePolicy(event.target.value);
                  setPreview(null);
                  setPublishedVersion(null);
                }}
              >
                <option value="task_projection">task_projection</option>
                <option value="strict_snapshot">strict_snapshot</option>
              </select>

              <label htmlFor="tp-workflow-execution">Execution</label>
              <select
                id="tp-workflow-execution"
                value={executionMode}
                onChange={(event) => {
                  setExecutionMode(event.target.value);
                  setPreview(null);
                  setPublishedVersion(null);
                }}
              >
                <option value="serial">serial</option>
                <option value="parallel">parallel</option>
              </select>
            </div>
          ) : null}
        </div>
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

function canSubmit(
  selectedTaskIds: string[],
  targetId: string,
  pairedEnabled: boolean = false,
  baselineTargetId: string = '',
  candidateTargetId: string = '',
) {
  const base = selectedTaskIds.length > 0 && Boolean(targetId);
  if (!pairedEnabled) return base;
  return base && Boolean(baselineTargetId) && Boolean(candidateTargetId);
}

function definitionsEqual(
  left: WorkflowDefinition | null,
  right: WorkflowDefinition,
) {
  return left ? JSON.stringify(left) === JSON.stringify(right) : false;
}

type PairedConfig = {
  baselineTargetId: string;
  candidateTargetId: string;
  targetConstraints: string[];
  initialStatePolicy: string;
  execution: string;
};

type BuildDefinitionInput = {
  selectedTaskIds: string[];
  targetId: string;
  repeatCount: number;
  parallelCount: number;
  processCount: number;
  paired: PairedConfig | null;
};

function buildDefinition(input: BuildDefinitionInput): WorkflowDefinition {
  const {
    selectedTaskIds,
    targetId,
    repeatCount,
    parallelCount,
    processCount,
    paired,
  } = input;
  // VS-10 Contract 2: when paired comparison is enabled, the matrix node grows
  // two lanes (baseline + candidate) with explicit roles, and a compare node
  // carries the three policy axes. The single-lane path is unchanged.
  const lanes = paired
    ? {
        baseline: { target_id: paired.baselineTargetId, role: 'baseline' },
        candidate: { target_id: paired.candidateTargetId, role: 'candidate' },
      }
    : { candidate: { target_id: targetId } };

  const nodes: WorkflowDefinition['nodes'] = [
    {
      id: 'tasks',
      type: 'task_selection',
      depends_on: [],
      config: {
        task_ids: selectedTaskIds,
        sample_n: 1,
      },
    },
    {
      id: 'matrix',
      type: 'matrix',
      depends_on: ['tasks'],
      config: {
        lanes,
        repeat_n: repeatCount,
      },
    },
    {
      id: 'execute',
      type: 'execute',
      depends_on: ['matrix'],
      config: {
        parallel: Math.max(1, parallelCount),
        processes: Math.max(1, processCount),
      },
    },
  ];

  if (paired) {
    nodes.push({
      id: 'compare',
      type: 'compare',
      depends_on: ['execute'],
      config: {
        target_constraints: paired.targetConstraints,
        initial_state_policy: paired.initialStatePolicy,
        execution: paired.execution,
      },
    });
  }

  return {
    schema_version: 1,
    name: 'WeChat smoke',
    nodes,
  };
}
