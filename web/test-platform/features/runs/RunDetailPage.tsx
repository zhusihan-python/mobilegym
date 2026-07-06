import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  ApiError,
  cancelRun,
  getComparison,
  getDiagnostics,
  getReport,
  getReportExport,
  getRun,
  listArtifacts,
  promoteBaseline,
  resumeRun,
  retryRun,
  streamRunEvents,
} from '../../api/client';
import type { ArtifactItem, Comparison, RunDetail, RunDiagnostics, RunReport } from '../../api/types';
import { reduceRunEvent, type RunLiveState, type ShardHealth } from './runEvents';

type DetailState =
  | { status: 'loading' }
  | { status: 'loaded'; run: RunDetail }
  | { status: 'error'; message: string };

type ComparisonState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'loaded'; comparison: Comparison }
  | { status: 'none' }
  | { status: 'error' };

type ReportState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'loaded'; report: RunReport }
  | { status: 'none' }
  | { status: 'error'; message: string };

type DiagnosticsState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'loaded'; diagnostics: RunDiagnostics; artifacts: ArtifactItem[] }
  | { status: 'error'; message: string };

const ACTIVE_RUN_STATES = new Set(['queued', 'preparing', 'running', 'evaluating', 'reporting']);
const REPORTABLE_RUN_STATES = new Set(['completed', 'failed']);
const FOLLOWUP_RUN_STATES = new Set(['completed', 'failed', 'cancelled']);

export function RunDetailPage() {
  const { runId = '' } = useParams();
  const [state, setState] = useState<DetailState>({ status: 'loading' });
  // Hold the live event state in a ref so the SSE callback always sees the
  // latest lastSequence without re-subscribing on every render.
  const liveRef = useRef<RunLiveState | null>(null);
  const [liveVersion, setLiveVersion] = useState(0);
  // VS-09: paired-run comparison (side-by-side view). Fetched when the run has
  // 2 lanes (a paired run) and is in a terminal/running state.
  const [comparison, setComparison] = useState<ComparisonState>({ status: 'idle' });
  const [report, setReport] = useState<ReportState>({ status: 'idle' });
  const [diagnostics, setDiagnostics] = useState<DiagnosticsState>({ status: 'idle' });
  const [followupMessage, setFollowupMessage] = useState<string | null>(null);
  const [followupBusy, setFollowupBusy] = useState<'retry' | 'resume' | null>(null);

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
          completedEpisodeKeys: new Set<string>(),
          activeWorkers: new Set<string>(),
          activeShards: new Map<string, ShardHealth>(),
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

  // VS-09: fetch the comparison when this is a paired run (2 lanes). Derived
  // from ``state`` (not the live snapshot) so the hook is unconditional w.r.t.
  // the early returns below. ``runState`` changes (queued→running→completed)
  // drive the refetch so the comparison appears once recorded.
  const baseRun = state.status === 'loaded' ? state.run : null;
  const isPaired = baseRun ? baseRun.lanes.length === 2 : false;
  const runState = baseRun?.state ?? '';
  useEffect(() => {
    if (!isPaired) {
      setComparison({ status: 'idle' });
      return;
    }
    let active = true;
    setComparison({ status: 'loading' });
    getComparison(runId)
      .then((comp) => {
        if (active) setComparison({ status: 'loaded', comparison: comp });
      })
      .catch(() => {
        if (active) setComparison({ status: 'none' });
      });
    return () => {
      active = false;
    };
  }, [runId, isPaired, runState]);

  useEffect(() => {
    if (!REPORTABLE_RUN_STATES.has(runState)) {
      setReport({ status: 'idle' });
      return;
    }
    let active = true;
    setReport({ status: 'loading' });
    getReport(runId)
      .then((data) => {
        if (active) setReport({ status: 'loaded', report: data });
      })
      .catch((error) => {
        if (!active) return;
        setReport({
          status: 'error',
          message: error instanceof Error ? error.message : 'No report is available.',
        });
      });
    return () => {
      active = false;
    };
  }, [runId, runState]);

  useEffect(() => {
    if (!REPORTABLE_RUN_STATES.has(runState)) {
      setDiagnostics({ status: 'idle' });
      return;
    }
    let active = true;
    setDiagnostics({ status: 'loading' });
    Promise.all([getDiagnostics(runId), listArtifacts(runId)])
      .then(([diagnosticData, artifactData]) => {
        if (active) {
          setDiagnostics({
            status: 'loaded',
            diagnostics: diagnosticData,
            artifacts: artifactData.items,
          });
        }
      })
      .catch((error) => {
        if (!active) return;
        setDiagnostics({
          status: 'error',
          message: error instanceof Error ? error.message : 'No diagnostics are available.',
        });
      });
    return () => {
      active = false;
    };
  }, [runId, runState]);

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
  const runAttempts = run.run_attempts ?? [];
  const laneAttempts = run.lane_attempts ?? [];
  const episodeAttempts = run.episode_attempts ?? [];
  // VS-07 live parallel progress: active workers + completed/total episodes.
  const activeWorkers = liveRef.current?.activeWorkers ?? new Set<string>();
  const completedEpisodeKeys = liveRef.current?.completedEpisodeKeys ?? new Set<string>();
  // VS-08 live multiprocess shard health.
  const activeShards = liveRef.current?.activeShards ?? new Map<string, ShardHealth>();
  const plannedEpisodes = run.progress.planned_episodes;
  const plannedLaneEpisodes = run.progress.planned_lane_episodes;
  const completedLaneEpisodes = run.progress.completed_lane_episodes ?? 0;
  // VS-09 Contract 9: paired runs use lane-scoped progress
  // (completed_lane_episodes / planned_lane_episodes). The live dedup set is
  // lane-aware too, so it lines up with the lane-episode denominator.
  const liveCompletedLane = Math.max(completedEpisodeKeys.size, completedLaneEpisodes);
  const liveCompleted = isPaired
    ? liveCompletedLane
    : Math.max(completedEpisodeKeys.size, run.progress.completed_episodes);
  // Reference liveVersion so React re-renders when the ref mutates.
  void liveVersion;

  const refreshRun = () => {
    getRun(runId)
      .then((fresh) => {
        setState({ status: 'loaded', run: fresh });
        if (liveRef.current) {
          liveRef.current = { ...liveRef.current, snapshot: fresh };
          setLiveVersion((v) => v + 1);
        }
      })
      .catch(() => {
        /* keep the current snapshot; follow-up action already succeeded */
      });
  };

  const runFollowup = (kind: 'retry' | 'resume') => {
    setFollowupBusy(kind);
    setFollowupMessage(null);
    const request = kind === 'retry' ? retryRun(run.id) : resumeRun(run.id);
    request
      .then((result) => {
        setFollowupMessage(
          `${kind === 'retry' ? 'Retry' : 'Resume'} queued attempt ${result.attempt_no} for ${result.selected_lane_episodes.length} lane episodes.`,
        );
        refreshRun();
      })
      .catch((error) => {
        setFollowupMessage(formatFollowupError(error));
      })
      .finally(() => setFollowupBusy(null));
  };

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
            {FOLLOWUP_RUN_STATES.has(run.state) ? (
              <>
                <button
                  type="button"
                  onClick={() => runFollowup('retry')}
                  disabled={followupBusy !== null}
                >
                  {followupBusy === 'retry' ? 'Retrying...' : 'Retry run'}
                </button>
                <button
                  type="button"
                  onClick={() => runFollowup('resume')}
                  disabled={followupBusy !== null}
                >
                  {followupBusy === 'resume' ? 'Resuming...' : 'Resume run'}
                </button>
              </>
            ) : null}
          </div>
        </div>
        {followupMessage ? (
          <p className="tp-kicker" data-testid="tp-followup-message">
            {followupMessage}
          </p>
        ) : null}
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
            <dd>
              <span data-testid="tp-completed-episodes">{liveCompleted}</span>
              {' / '}
              <span data-testid="tp-planned-episodes">
                {isPaired ? plannedLaneEpisodes : plannedEpisodes}
              </span>
              {' completed '}
              {isPaired ? 'lane episodes' : 'episodes'}
            </dd>
          </div>
          <div>
            <dt>Active workers</dt>
            <dd>
              <span data-testid="tp-active-workers">{activeWorkers.size}</span>
            </dd>
          </div>
          {activeShards.size > 0 ? (
            <div>
              <dt>Shards</dt>
              <dd>
                <ul className="tp-shard-list" data-testid="tp-shard-list">
                  {Array.from(activeShards.entries()).map(([rank, health]) => (
                    <li key={rank} className={`tp-shard tp-shard-${health.status}`}>
                      <span data-testid={`tp-shard-${rank}`}>
                        {rank}: {health.status}
                        {health.exitcode !== null ? ` (exit ${health.exitcode})` : ''}
                      </span>
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
          ) : null}
          <div>
            <dt>Fingerprint</dt>
            <dd className="tp-mono">{run.fingerprint}</dd>
          </div>
        </dl>
      </section>

      <ReportPanel run={run} report={report} />
      <DiagnosticsPanel run={run} diagnostics={diagnostics} />

      {runAttempts.length > 0 ? (
        <section className="tp-panel">
          <h2>Attempt history</h2>
          <table>
            <thead>
              <tr>
                <th>Attempt</th>
                <th>Reason</th>
                <th>State</th>
                <th>Started</th>
                <th>Ended</th>
              </tr>
            </thead>
            <tbody>
              {runAttempts.map((attempt) => (
                <tr key={attempt.id}>
                  <td>{attempt.attempt_no}</td>
                  <td>{attempt.reason}</td>
                  <td>{attempt.state}</td>
                  <td>{attempt.started_at ?? '—'}</td>
                  <td>{attempt.ended_at ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {laneAttempts.length > 0 ? (
        <section className="tp-panel">
          <h2>Lane attempts</h2>
          <table>
            <thead>
              <tr>
                <th>Attempt</th>
                <th>Lane</th>
                <th>Reason</th>
                <th>State</th>
                <th>Artifact root</th>
                <th>Explorer</th>
              </tr>
            </thead>
            <tbody>
              {laneAttempts.map((attempt) => (
                <tr key={attempt.id}>
                  <td>{attempt.attempt_no ?? '—'}</td>
                  <td>{attempt.lane_key}</td>
                  <td>{attempt.reason ?? '—'}</td>
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

      {isPaired && comparison.status === 'loaded' ? (
        <section className="tp-panel" data-testid="tp-comparison">
          <h2>Comparison</h2>
          <p className="tp-comparison-summary">
            {comparison.comparison.summary
              ? Object.entries(comparison.comparison.summary)
                  .map(([k, v]) => `${k}: ${v}`)
                  .join(', ')
              : 'No pairs yet.'}
          </p>
          <table>
            <thead>
              <tr>
                <th>Pair</th>
                <th>Classification</th>
                <th>Baseline</th>
                <th>Candidate</th>
                <th>Integrity</th>
                <th>Prepared params</th>
                <th>Instruction</th>
              </tr>
            </thead>
            <tbody>
              {comparison.comparison.pairs.map((pair) => (
                <tr key={pair.id} data-testid={`tp-comparison-pair-${pair.pair_key}`}>
                  <td className="tp-mono">{pair.pair_key}</td>
                  <td>
                    <span
                      className={`tp-classification tp-classification-${pair.classification}`}
                      data-testid="tp-pair-classification"
                    >
                      {pair.classification}
                    </span>
                  </td>
                  <td>{pair.delta.baseline_outcome ?? '—'}</td>
                  <td>{pair.delta.candidate_outcome ?? '—'}</td>
                  <td data-testid="tp-pair-integrity">
                    {pair.integrity.status}
                    {pair.integrity.status !== 'ok' && pair.integrity.reason
                      ? ` (${pair.integrity.reason})`
                      : ''}
                  </td>
                  <td className="tp-mono" data-testid="tp-pair-prepared-params">
                    {pair.prepared
                      ? JSON.stringify(pair.prepared.params)
                      : '—'}
                  </td>
                  <td className="tp-mono">
                    {pair.prepared?.instruction ?? '—'}
                  </td>
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

function ReportPanel({ run, report }: { run: RunDetail; report: ReportState }) {
  const [regressionsOnly, setRegressionsOnly] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  if (!REPORTABLE_RUN_STATES.has(run.state)) {
    return (
      <section className="tp-panel" data-testid="tp-report-panel">
        <h2>Report</h2>
        <p>Report will be available after the run reaches a terminal state.</p>
      </section>
    );
  }

  if (report.status === 'loading' || report.status === 'idle') {
    return (
      <section className="tp-panel" data-testid="tp-report-panel">
        <h2>Report</h2>
        <p>Loading report...</p>
      </section>
    );
  }

  if (report.status === 'error') {
    return (
      <section className="tp-panel" data-testid="tp-report-panel">
        <h2>Report</h2>
        <p>{report.message}</p>
      </section>
    );
  }

  if (report.status === 'none') {
    return null;
  }

  const data = report.report;
  const counts = data.comparison.classification_counts;
  const visiblePairs = regressionsOnly
    ? data.comparison.pairs.filter((pair) => pair.classification === 'regression')
    : data.comparison.pairs;

  const exportReport = (format: 'json' | 'html') => {
    getReportExport(run.id, format)
      .then((content) => {
        downloadText(
          content,
          `${run.id}-report.${format === 'json' ? 'json' : 'html'}`,
          format === 'json' ? 'application/json' : 'text/html',
        );
        setMessage(format === 'json' ? 'JSON export ready.' : 'Printable HTML ready.');
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : 'Export failed.');
      });
  };

  const promote = () => {
    promoteBaseline(run.id)
      .then((baseline) => {
        setMessage(`Promoted baseline for ${baseline.lane_key}.`);
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : 'Baseline promotion failed.');
      });
  };

  return (
    <section className="tp-panel" data-testid="tp-report-panel">
      <div className="tp-run-heading">
        <div>
          <h2>Report</h2>
          <p>
            Gate verdict:{' '}
            <span className={`tp-run-state tp-gate-${data.gate.verdict}`} data-testid="tp-gate-verdict">
              {data.gate.verdict}
            </span>
          </p>
        </div>
        <div className="tp-run-actions">
          <button type="button" onClick={() => exportReport('json')}>
            Export JSON
          </button>
          <button type="button" onClick={() => exportReport('html')}>
            Printable HTML
          </button>
          <button type="button" onClick={promote}>
            Promote baseline
          </button>
        </div>
      </div>

      <dl className="tp-run-facts">
        <div>
          <dt>Success rate</dt>
          <dd>{formatRate(data.functional.summary.success_rate)}</dd>
        </div>
        <div>
          <dt>Regressions</dt>
          <dd data-testid="tp-report-regressions">{counts.regressions ?? 0}</dd>
        </div>
        <div>
          <dt>Candidate errors</dt>
          <dd>{counts.candidate_errors ?? 0}</dd>
        </div>
        <div>
          <dt>Runtime p95 delta</dt>
          <dd data-testid="tp-report-runtime-delta">
            {formatPercentValue(data.comparison.runtime_s.percent_delta)}
          </dd>
        </div>
      </dl>

      <label className="tp-inline-control">
        <input
          type="checkbox"
          checked={regressionsOnly}
          onChange={(event) => setRegressionsOnly(event.currentTarget.checked)}
        />
        Regression pairs only
      </label>

      {message ? <p className="tp-kicker">{message}</p> : null}

      <div className="tp-report-pairs">
        {visiblePairs.map((pair) => (
          <details key={pair.pair_key} data-testid={`tp-report-pair-${pair.pair_key}`}>
            <summary>
              <span className="tp-mono">{pair.pair_key}</span>
              {' · '}
              <span className={`tp-classification tp-classification-${pair.classification}`}>
                {pair.classification}
              </span>
            </summary>
            <pre className="tp-report-diff">{JSON.stringify(pair.delta, null, 2)}</pre>
          </details>
        ))}
      </div>
    </section>
  );
}

function DiagnosticsPanel({
  run,
  diagnostics,
}: {
  run: RunDetail;
  diagnostics: DiagnosticsState;
}) {
  const [errorsOnly, setErrorsOnly] = useState(false);

  if (!REPORTABLE_RUN_STATES.has(run.state)) {
    return (
      <section className="tp-panel" data-testid="tp-diagnostics-panel">
        <h2>Diagnostics</h2>
        <p>Diagnostics will be available after the run reaches a terminal state.</p>
      </section>
    );
  }

  if (diagnostics.status === 'loading' || diagnostics.status === 'idle') {
    return (
      <section className="tp-panel" data-testid="tp-diagnostics-panel">
        <h2>Diagnostics</h2>
        <p>Loading diagnostics...</p>
      </section>
    );
  }

  if (diagnostics.status === 'error') {
    return (
      <section className="tp-panel" data-testid="tp-diagnostics-panel">
        <h2>Diagnostics</h2>
        <p>{diagnostics.message}</p>
      </section>
    );
  }

  const data = diagnostics.diagnostics;
  const visibleItems = errorsOnly
    ? data.items.filter((item) => item.severity === 'error')
    : data.items;

  return (
    <section className="tp-panel" data-testid="tp-diagnostics-panel">
      <div className="tp-run-heading">
        <div>
          <h2>Diagnostics</h2>
          <p>
            <span data-testid="tp-diagnostics-total">{data.summary.total}</span>
            {' records'}
          </p>
        </div>
      </div>

      <dl className="tp-run-facts">
        <div>
          <dt>Errors</dt>
          <dd data-testid="tp-diagnostics-errors">{data.summary.by_severity.error ?? 0}</dd>
        </div>
        <div>
          <dt>Warnings</dt>
          <dd>{data.summary.by_severity.warning ?? 0}</dd>
        </div>
        <div>
          <dt>Execution</dt>
          <dd>{data.summary.by_category.execution ?? 0}</dd>
        </div>
        <div>
          <dt>Comparison</dt>
          <dd>{data.summary.by_category.comparison ?? 0}</dd>
        </div>
      </dl>

      <label className="tp-inline-control">
        <input
          type="checkbox"
          checked={errorsOnly}
          onChange={(event) => setErrorsOnly(event.currentTarget.checked)}
        />
        Errors only
      </label>

      <table>
        <thead>
          <tr>
            <th>Code</th>
            <th>Severity</th>
            <th>Entity</th>
            <th>Message</th>
            <th>Artifacts</th>
          </tr>
        </thead>
        <tbody>
          {visibleItems.map((item) => (
            <tr key={item.id} data-testid={`tp-diagnostic-${item.code}`}>
              <td className="tp-mono">{item.code}</td>
              <td>
                <span className={`tp-classification tp-diagnostic-${item.severity}`}>
                  {item.severity}
                </span>
              </td>
              <td>
                {item.entity_type}
                {item.episode_attempt_id ? (
                  <span className="tp-mono"> {item.episode_attempt_id}</span>
                ) : null}
                {item.pair_key ? <span className="tp-mono"> {item.pair_key}</span> : null}
              </td>
              <td>{item.message}</td>
              <td>
                {item.artifact_refs.length > 0
                  ? item.artifact_refs.map((ref) => (
                      <a key={ref} href={runExplorerHref(run.id, ref)}>
                        {ref}
                      </a>
                    ))
                  : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {diagnostics.artifacts.length > 0 ? (
        <div className="tp-artifact-browser">
          <h3>Artifacts</h3>
          <table>
            <thead>
              <tr>
                <th>Path</th>
                <th>Kind</th>
                <th>Size</th>
                <th>Content</th>
              </tr>
            </thead>
            <tbody>
              {diagnostics.artifacts.map((artifact) => (
                <tr key={artifact.id} data-testid={`tp-artifact-${artifact.id}`}>
                  <td className="tp-mono">{artifact.relative_path}</td>
                  <td>{artifact.kind}</td>
                  <td>{artifact.size_bytes ?? '—'}</td>
                  <td>
                    <a href={artifactContentHref(run.id, artifact.id)}>Open</a>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function formatRate(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return `${Math.round(value * 1000) / 10}%`;
}

function formatPercentValue(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '—';
  }
  return `${Math.round(value * 10) / 10}%`;
}

function formatFollowupError(error: unknown) {
  if (error instanceof ApiError) {
    const details = error.details.length > 0 ? ` ${JSON.stringify(error.details)}` : '';
    return `${error.code}: ${error.message}${details}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Follow-up action failed.';
}

function downloadText(content: string, filename: string, type: string) {
  if (typeof Blob === 'undefined' || typeof URL.createObjectURL !== 'function') {
    return;
  }
  const blob = new Blob([content], { type });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}

function runExplorerHref(runId: string, artifactRoot: string) {
  const runPath = `${runId}/${artifactRoot}`.replace(/\\/g, '/');
  return `/run_explorer.html?run=${encodeURIComponent(runPath)}`;
}

function artifactContentHref(runId: string, artifactId: string) {
  return `/api/platform/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(
    artifactId,
  )}/content`;
}
