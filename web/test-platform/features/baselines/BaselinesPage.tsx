import { useEffect, useState } from 'react';
import { Link, useOutletContext, useParams } from 'react-router-dom';

import { archiveBaseline, getBaseline, listBaselines } from '../../api/client';
import type { Baseline, BaselineDetail, Project } from '../../api/types';
import { EmptyState } from '../../components/EmptyState';

type CatalogState =
  | { status: 'loading' }
  | { status: 'loaded'; items: Baseline[] }
  | { status: 'error'; message: string };

export function BaselinesPage() {
  const { selectedProject } = useOutletContext<{ selectedProject: Project }>();
  const [state, setState] = useState<CatalogState>({ status: 'loading' });

  useEffect(() => {
    let active = true;
    setState({ status: 'loading' });
    listBaselines(selectedProject.id)
      .then((response) => {
        if (active) setState({ status: 'loaded', items: response.items });
      })
      .catch((error) => {
        if (active) {
          setState({
            status: 'error',
            message: error instanceof Error ? error.message : 'Unable to load baselines.',
          });
        }
      });
    return () => {
      active = false;
    };
  }, [selectedProject.id]);

  if (state.status === 'loading') {
    return <section className="tp-panel">Loading baselines...</section>;
  }
  if (state.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Baselines could not be loaded</h2>
        <p>{state.message}</p>
      </section>
    );
  }
  if (state.items.length === 0) {
    return (
      <EmptyState
        title="No active baselines"
        body={`Promote an eligible run lane to create a baseline for ${selectedProject.name}.`}
      />
    );
  }

  return (
    <section className="tp-panel" data-testid="tp-baselines-list">
      <h2>Active baselines</h2>
      <p>Named strict baselines for {selectedProject.name}.</p>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Source run</th>
            <th>Lane</th>
            <th>Target revision</th>
            <th>Workflow version</th>
            <th>Report version</th>
            <th>Created</th>
          </tr>
        </thead>
        <tbody>
          {state.items.map((baseline) => (
            <tr key={baseline.id}>
              <td><Link to={`/baselines/${baseline.id}`}>{baseline.display_name}</Link></td>
              <td>{baseline.source_run_name ?? baseline.source_run_id}</td>
              <td>{baseline.lane_key}</td>
              <td className="tp-mono">{baseline.target_revision_id}</td>
              <td className="tp-mono">{baseline.workflow_version_id}</td>
              <td>v{baseline.report_schema_version}</td>
              <td>{baseline.created_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

type DetailState =
  | { status: 'loading' }
  | { status: 'loaded'; detail: BaselineDetail }
  | { status: 'error'; message: string };

export function BaselineDetailPage() {
  const { baselineId = '' } = useParams();
  const [state, setState] = useState<DetailState>({ status: 'loading' });
  const [message, setMessage] = useState<string | null>(null);
  const [archiving, setArchiving] = useState(false);

  useEffect(() => {
    let active = true;
    getBaseline(baselineId)
      .then((detail) => {
        if (active) setState({ status: 'loaded', detail });
      })
      .catch((error) => {
        if (active) {
          setState({
            status: 'error',
            message: error instanceof Error ? error.message : 'Unable to load the baseline.',
          });
        }
      });
    return () => {
      active = false;
    };
  }, [baselineId]);

  if (state.status === 'loading') {
    return <section className="tp-panel">Loading baseline...</section>;
  }
  if (state.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Baseline could not be loaded</h2>
        <p>{state.message}</p>
      </section>
    );
  }

  const { baseline, source_report: sourceReport, replays } = state.detail;
  const archive = () => {
    setArchiving(true);
    setMessage(null);
    archiveBaseline(baseline.id)
      .then((archived) => {
        setState({
          status: 'loaded',
          detail: { ...state.detail, baseline: archived },
        });
        setMessage('Baseline archived. Its source evidence remains available.');
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : 'Unable to archive the baseline.');
      })
      .finally(() => setArchiving(false));
  };

  return (
    <>
      <section className="tp-panel">
        <Link to="/baselines">Back to baselines</Link>
        <div className="tp-run-heading">
          <div>
            <h2>{baseline.display_name}</h2>
            <p>{baseline.archived_at ? 'Archived strict baseline' : 'Active strict baseline'}</p>
          </div>
          <button
            type="button"
            onClick={archive}
            disabled={archiving || baseline.archived_at !== null}
          >
            {archiving ? 'Archiving...' : 'Archive baseline'}
          </button>
        </div>
        {message ? <p role="status">{message}</p> : null}
        <dl className="tp-run-facts">
          <div><dt>Selected lane</dt><dd>{baseline.lane_key}</dd></div>
          <div><dt>Target revision</dt><dd className="tp-mono">{baseline.target_revision_id}</dd></div>
          <div><dt>Workflow version</dt><dd className="tp-mono">{baseline.workflow_version_id}</dd></div>
          <div><dt>Report version</dt><dd>v{baseline.report_schema_version}</dd></div>
          <div><dt>Created</dt><dd>{baseline.created_at}</dd></div>
          <div><dt>Archived</dt><dd>{baseline.archived_at ?? 'No'}</dd></div>
        </dl>
      </section>

      <section className="tp-panel">
        <h2>Immutable source</h2>
        <p>
          <Link to={`/runs/${baseline.source_run_id}`}>Open source run</Link>{' '}
          <a href={sourceReport.href}>Open source report</a>
        </p>
        <dl className="tp-run-facts">
          <div><dt>Run attempt</dt><dd className="tp-mono">{sourceReport.run_attempt_id}</dd></div>
          <div><dt>Report ID</dt><dd className="tp-mono">{sourceReport.id}</dd></div>
        </dl>
        <h3>Selected-lane replays</h3>
        {replays.length > 0 ? (
          <ul>
            {replays.map((replay) => (
              <li key={replay.episode_attempt_id}>
                <a href={replay.href}>Replay {replay.episode_key}</a>
              </li>
            ))}
          </ul>
        ) : (
          <p>No replayable episodes were recorded.</p>
        )}
      </section>
    </>
  );
}
