import { useEffect, useMemo, useState } from 'react';

import { getTask, listTasks } from '../../api/client';
import type { TaskCatalogItem, TaskCatalogResponse } from '../../api/types';

type TasksState =
  | { status: 'loading' }
  | { status: 'loaded'; data: TaskCatalogResponse }
  | { status: 'error'; message: string };

export function TasksPage() {
  const [suite, setSuite] = useState('');
  const [tasks, setTasks] = useState<TasksState>({ status: 'loading' });
  const [detail, setDetail] = useState<
    | { status: 'idle' }
    | { status: 'loading' }
    | { status: 'loaded'; task: TaskCatalogItem }
    | { status: 'error'; message: string }
  >({ status: 'idle' });

  useEffect(() => {
    let active = true;
    setTasks({ status: 'loading' });
    listTasks(suite ? { suite } : {})
      .then((data) => {
        if (active) setTasks({ status: 'loaded', data });
      })
      .catch((error) => {
        if (active) {
          setTasks({
            status: 'error',
            message: error instanceof Error ? error.message : 'Unable to load tasks.',
          });
        }
      });
    return () => {
      active = false;
    };
  }, [suite]);

  const suiteOptions = useMemo(() => {
    if (tasks.status !== 'loaded') return suite ? [suite] : [];
    const options = new Set(tasks.data.items.map((task) => task.suite));
    if (suite) options.add(suite);
    return [...options].sort();
  }, [suite, tasks]);

  const openDetail = (taskId: string) => {
    setDetail({ status: 'loading' });
    getTask(taskId)
      .then((task) => setDetail({ status: 'loaded', task }))
      .catch((error) => {
        setDetail({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to load task details.',
        });
      });
  };

  return (
    <>
      <section className="tp-panel tp-page-actions">
        <div>
          <h2>Task catalog</h2>
          <p>Browse BaseTask definitions discovered from the benchmark registry.</p>
        </div>
        <label className="tp-inline-field" htmlFor="tp-task-suite">
          <span>Suite</span>
          <select id="tp-task-suite" value={suite} onChange={(event) => setSuite(event.target.value)}>
            <option value="">All suites</option>
            {suiteOptions.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      </section>

      {tasks.status === 'loading' ? <section className="tp-panel">Loading tasks...</section> : null}

      {tasks.status === 'error' ? (
        <section className="tp-alert" role="alert">
          <h2>Tasks could not be loaded</h2>
          <p>{tasks.message}</p>
        </section>
      ) : null}

      {tasks.status === 'loaded' ? (
        <section className="tp-panel">
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Suite</th>
                <th>Difficulty</th>
                <th>Apps</th>
                <th>Capabilities</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {tasks.data.items.map((task) => (
                <tr key={task.task_base_id}>
                  <td>{task.task_base_id}</td>
                  <td>{task.suite}</td>
                  <td>{task.difficulty}</td>
                  <td>{task.apps.join(', ') || '-'}</td>
                  <td>{task.capabilities.length ? `${task.capabilities.length} tags` : '-'}</td>
                  <td>
                    <button
                      className="tp-text-button"
                      type="button"
                      onClick={() => openDetail(task.task_base_id)}
                      aria-label={`Open details for ${task.task_base_id}`}
                    >
                      Details
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {detail.status === 'loading' ? <section className="tp-panel">Loading task details...</section> : null}
      {detail.status === 'error' ? (
        <section className="tp-alert" role="alert">
          <h2>Task details could not be loaded</h2>
          <p>{detail.message}</p>
        </section>
      ) : null}
      {detail.status === 'loaded' ? <TaskDetail task={detail.task} /> : null}
    </>
  );
}

function TaskDetail({ task }: { task: TaskCatalogItem }) {
  return (
    <section className="tp-panel tp-task-detail" aria-label={`Task details for ${task.task_base_id}`}>
      <h2>{task.class_name}</h2>
      <dl className="tp-target-details">
        <div>
          <dt>Task ID</dt>
          <dd>{task.task_base_id}</dd>
        </div>
        <div>
          <dt>Capabilities</dt>
          <dd>{task.capabilities.join(', ') || '-'}</dd>
        </div>
        <div>
          <dt>Objective</dt>
          <dd>{task.objective}</dd>
        </div>
        <div>
          <dt>Composition</dt>
          <dd>{task.composition}</dd>
        </div>
      </dl>
    </section>
  );
}
