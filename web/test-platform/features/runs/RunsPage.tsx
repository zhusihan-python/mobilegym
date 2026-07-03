import { useEffect, useState } from 'react';
import { useOutletContext } from 'react-router-dom';

import { listRuns } from '../../api/client';
import type { CollectionResponse, Project, RunSummary } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';

type RunsState =
  | { status: 'loading' }
  | { status: 'loaded'; data: CollectionResponse<RunSummary> }
  | { status: 'error'; message: string };

export function RunsPage() {
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const [runs, setRuns] = useState<RunsState>({ status: 'loading' });

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
              <td>{run.name ?? run.id}</td>
              <td>{run.state}</td>
              <td>{run.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
