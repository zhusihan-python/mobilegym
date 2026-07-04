import { useEffect, useMemo, useState } from 'react';
import { useNavigate, useOutletContext } from 'react-router-dom';

import {
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
  Project,
  Target,
  TaskCatalogItem,
  WorkflowCompilePreview,
  WorkflowDefinition,
  WorkflowSummary,
  WorkflowVersion,
} from '../../api/types';

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
  const [activeWorkflow, setActiveWorkflow] = useState<WorkflowSummary | null>(null);
  const [preview, setPreview] = useState<WorkflowCompilePreview | null>(null);
  const [publishedVersion, setPublishedVersion] = useState<WorkflowVersion | null>(null);
  const [runSeed, setRunSeed] = useState(0);
  const [error, setError] = useState<string | null>(null);
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

  const definition = useMemo(
    () => buildDefinition(selectedTaskIds, targetId, repeatCount, parallelCount),
    [repeatCount, selectedTaskIds, targetId, parallelCount],
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
    setBusy(true);
    setError(null);
    createRun({
      workflowVersionId: publishedVersion.id,
      name: activeWorkflow?.name ?? publishedVersion.definition.name,
      seed: runSeed,
      idempotencyKey: crypto.randomUUID(),
    })
      .then((run) => navigate(`/runs/${run.id}`))
      .catch((runError) => {
        setError(runError instanceof Error ? runError.message : 'Run creation failed.');
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
          <button type="button" onClick={validatePreview} disabled={busy || !canSubmit(selectedTaskIds, targetId)}>
            Validate preview
          </button>
          <button type="button" onClick={publish} disabled={busy || !canSubmit(selectedTaskIds, targetId)}>
            Publish workflow
          </button>
          {publishedVersion ? (
            <button type="button" onClick={launch} disabled={busy}>
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

          <label htmlFor="tp-workflow-seed">Run seed</label>
          <input
            id="tp-workflow-seed"
            type="number"
            value={runSeed}
            onChange={(event) => setRunSeed(Number(event.target.value) || 0)}
          />
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

function canSubmit(selectedTaskIds: string[], targetId: string) {
  return selectedTaskIds.length > 0 && Boolean(targetId);
}

function definitionsEqual(
  left: WorkflowDefinition | null,
  right: WorkflowDefinition,
) {
  return left ? JSON.stringify(left) === JSON.stringify(right) : false;
}

function buildDefinition(
  selectedTaskIds: string[],
  targetId: string,
  repeatCount: number,
  parallelCount: number = 1,
): WorkflowDefinition {
  return {
    schema_version: 1,
    name: 'WeChat smoke',
    nodes: [
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
          lanes: { candidate: { target_id: targetId } },
          repeat_n: repeatCount,
        },
      },
      {
        id: 'execute',
        type: 'execute',
        depends_on: ['matrix'],
        config: { parallel: Math.max(1, parallelCount) },
      },
    ],
  };
}
