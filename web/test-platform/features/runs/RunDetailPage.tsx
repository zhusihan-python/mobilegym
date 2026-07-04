import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import { cancelRun, getRun, streamRunEvents } from '../../api/client';
import type { RunDetail } from '../../api/types';
import { reduceRunEvent, type RunLiveState } from './runEvents';

type DetailState =
  | { status: 'loading' }
  | { status: 'loaded'; run: RunDetail }
  | { status: 'error'; message: string };

const ACTIVE_RUN_STATES = new Set(['queued', 'preparing', 'running', 'evaluating', 'reporting']);

export function RunDetailPage() {
  const { runId = '' } = useParams();
  const [state, setState] = useState<DetailState>({ status: 'loading' });
  // Hold the live event state in a ref so the SSE callback always sees the
  // latest lastSequence without re-subscribing on every render.
  const liveRef = useRef<RunLiveState | null>(null);
  const [liveVersion, setLiveVersion] = useState(0);

  useEffect(() => {
    let active = true;
    let dispose: (() => void) | null = null;
    liveRef.current = null;
    getRun(runId)
      .then((run) => {
        if (!active) return;
        setState({ status: 'loaded', run });
        // Subscribe to live events once the snapshot is loaded.
        liveRef.current = {
          snapshot: run,
          lastSequence: 0,
          connected: true,
          replaying: false,
        };
        dispose = streamRunEvents(
          runId,
          (event) => {
            if (!liveRef.current) return;
            liveRef.current = reduceRunEvent(liveRef.current, event);
            setLiveVersion((v) => v + 1);
          },
          () => {
            // reset_required: refetch a fresh snapshot via REST.
            getRun(runId)
              .then((fresh) => {
                if (!active) return;
                if (liveRef.current) {
                  liveRef.current = { ...liveRef.current, snapshot: fresh };
                  setLiveVersion((v) => v + 1);
                } else {
                  setState({ status: 'loaded', run: fresh });
                }
              })
              .catch(() => {
                /* keep the existing snapshot on refetch error */
              });
          },
        );
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
      if (dispose) dispose();
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

  // Prefer the live-event-patched snapshot when available.
  const run = liveRef.current?.snapshot ?? state.run;
  const laneAttempts = run.lane_attempts ?? [];
  const episodeAttempts = run.episode_attempts ?? [];
  // Reference liveVersion so React re-renders when the ref mutates.
  void liveVersion;
  return (
    <>
      <section className="tp-panel tp-run-overview">
        <div className="tp-run-heading">
          <div>
            <Link to="/runs">Back to runs</Link>
            <h2>Run overview</h2>
            <p>{run.name ?? run.id}</p>
          </div>
          <div className="tp-run-actions">
            <span className="tp-run-state">{run.state}</span>
            {ACTIVE_RUN_STATES.has(run.state) ? (
              <button
                type="button"
                className="tp-run-cancel"
                onClick={() => {
                  cancelRun(runId).catch(() => {
                    /* the snapshot will reflect the outcome on the next event */
                  });
                }}
              >
                Cancel run
              </button>
            ) : null}
          </div>
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
            <dt>Completed</dt>
            <dd>{run.progress.completed_episodes} completed episodes</dd>
          </div>
          <div>
            <dt>Fingerprint</dt>
            <dd className="tp-mono">{run.fingerprint}</dd>
          </div>
        </dl>
      </section>

      {laneAttempts.length > 0 ? (
        <section className="tp-panel">
          <h2>Lane attempts</h2>
          <table>
            <thead>
              <tr>
                <th>Lane</th>
                <th>State</th>
                <th>Artifact root</th>
                <th>Explorer</th>
              </tr>
            </thead>
            <tbody>
              {laneAttempts.map((attempt) => (
                <tr key={attempt.id}>
                  <td>{attempt.lane_key}</td>
                  <td>{attempt.state}</td>
                  <td className="tp-mono">{attempt.artifact_root}</td>
                  <td>
                    <a href={runExplorerHref(run.id, attempt.artifact_root)}>
                      Open in Run Explorer
                    </a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

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

      {episodeAttempts.length > 0 ? (
        <section className="tp-panel">
          <h2>Episode attempts</h2>
          <table>
            <thead>
              <tr>
                <th>Episode</th>
                <th>Lane</th>
                <th>Outcome</th>
                <th>Error</th>
                <th>Artifact root</th>
              </tr>
            </thead>
            <tbody>
              {episodeAttempts.map((attempt) => (
                <tr key={`${attempt.episode_key}:${attempt.lane_key}:${attempt.attempt_no}`}>
                  <td className="tp-mono">{attempt.episode_key}</td>
                  <td>{attempt.lane_key}</td>
                  <td>
                    <span className={`tp-outcome tp-outcome-${(attempt.outcome ?? 'pending').toLowerCase()}`}>
                      {attempt.outcome ?? attempt.state}
                    </span>
                  </td>
                  <td>{attempt.error_code ?? ''}</td>
                  <td className="tp-mono">{attempt.artifact_root}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

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

function runExplorerHref(runId: string, artifactRoot: string) {
  const runPath = `${runId}/${artifactRoot}`.replace(/\\/g, '/');
  return `/run_explorer.html?run=${encodeURIComponent(runPath)}`;
}
