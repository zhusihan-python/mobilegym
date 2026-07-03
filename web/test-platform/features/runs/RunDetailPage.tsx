import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { getRun } from '../../api/client';
import type { RunDetail } from '../../api/types';

type DetailState =
  | { status: 'loading' }
  | { status: 'loaded'; run: RunDetail }
  | { status: 'error'; message: string };

export function RunDetailPage() {
  const { runId = '' } = useParams();
  const [state, setState] = useState<DetailState>({ status: 'loading' });

  useEffect(() => {
    let active = true;
    getRun(runId)
      .then((run) => {
        if (active) setState({ status: 'loaded', run });
      })
      .catch((error) => {
        if (active) {
          setState({
            status: 'error',
            message: error instanceof Error ? error.message : 'Unable to load the run.',
          });
        }
      });
    return () => {
      active = false;
    };
  }, [runId]);

  if (state.status === 'loading') {
    return <section className="tp-panel">Loading run plan...</section>;
  }

  if (state.status === 'error') {
    return (
      <section className="tp-alert" role="alert">
        <h2>Run could not be loaded</h2>
        <p>{state.message}</p>
      </section>
    );
  }

  const { run } = state;
  return (
    <>
      <section className="tp-panel tp-run-overview">
        <div className="tp-run-heading">
          <div>
            <Link to="/runs">Back to runs</Link>
            <h2>Run overview</h2>
            <p>{run.name ?? run.id}</p>
          </div>
          <span className="tp-run-state">{run.state}</span>
        </div>
        <dl className="tp-run-facts">
          <div>
            <dt>Plan</dt>
            <dd>{run.progress.planned_episodes} planned episodes</dd>
          </div>
          <div>
            <dt>Lane work</dt>
            <dd>{run.progress.planned_lane_episodes} planned lane episodes</dd>
          </div>
          <div>
            <dt>Fingerprint</dt>
            <dd className="tp-mono">{run.fingerprint}</dd>
          </div>
        </dl>
      </section>

      <section className="tp-panel">
        <h2>Frozen target revisions</h2>
        <table>
          <thead>
            <tr>
              <th>Lane</th>
              <th>Target revision</th>
              <th>Metadata hash</th>
            </tr>
          </thead>
          <tbody>
            {run.target_revisions.map((revision) => {
              const lane = run.lanes.find((item) => item.target_id === revision.target_id);
              return (
                <tr key={`${revision.target_id}:${revision.target_revision_id}`}>
                  <td>{lane?.lane_key ?? revision.target_id}</td>
                  <td className="tp-mono">{revision.target_revision_id}</td>
                  <td className="tp-mono">{revision.metadata_hash}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="tp-panel">
        <h2>Episode identities</h2>
        <table>
          <thead>
            <tr>
              <th>Episode</th>
              <th>Seed</th>
              <th>Trial</th>
              <th>Max steps</th>
            </tr>
          </thead>
          <tbody>
            {run.episode_identities.map((episode) => (
              <tr key={episode.episode_key}>
                <td className="tp-mono">{episode.episode_key}</td>
                <td>{episode.instance_seed}</td>
                <td>{episode.trial_id}</td>
                <td>{episode.max_steps}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
