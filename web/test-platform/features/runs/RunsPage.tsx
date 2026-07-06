import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate, useOutletContext } from 'react-router-dom';

import { importLegacyRun, listRuns } from '../../api/client';
import type { CollectionResponse, Project, RunSummary } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';

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

  return (
    <>
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
