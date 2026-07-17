import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';

import {
  compileWorkflowPreview,
  createWorkflow,
  listTasks,
  listWorkflows,
  publishWorkflow,
  updateWorkflowDraft,
} from '../../api/client';
import type {
  Project,
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

type LoadState =
  | { status: 'loading' }
  | {
      status: 'loaded';
      tasks: TaskCatalogItem[];
      workflows: WorkflowSummary[];
    }
  | { status: 'error'; message: string };

export function WorkflowsPage() {
  const navigate = useNavigate();
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [workflowMode, setWorkflowMode] = useState<WorkflowMode>('batch');
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
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
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    setState({ status: 'loading' });
    Promise.all([
      listTasks(),
      listWorkflows(selectedProject.id),
    ])
      .then(([tasksResponse, workflowsResponse]) => {
        if (!active) return;
        setState({
          status: 'loaded',
          tasks: tasksResponse.items,
          workflows: workflowsResponse.items,
        });
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
          <p>Define tasks and Lane Slots before publishing an immutable version.</p>
        </div>
        <div className="tp-workflow-actions">
          <button
            type="button"
            onClick={validatePreview}
            disabled={
              busy ||
              !canSubmitWorkflow(selectedTaskIds)
            }
          >
            Validate preview
          </button>
          <button
            type="button"
            onClick={publish}
            disabled={
              busy ||
              !canSubmitWorkflow(selectedTaskIds)
            }
          >
            Publish workflow
          </button>
          {publishedVersion ? (
            <button
              type="button"
              onClick={() => navigate('/run-launch')}
              disabled={busy}
            >
              Open Run Launch
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

    </>
  );
}
