import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';

import {
  ApiError,
  cancelRun,
  getComparison,
  getBaselineEligibility,
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
import type {
  ArtifactItem,
  BaselineEligibility,
  Comparison,
  RunDetail,
  RunDiagnostics,
  RunReport,
} from '../../api/types';
import { RunObservatory } from './RunObservatory';
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

type BaselineEligibilityState =
  | { status: 'loading' }
  | { status: 'loaded'; value: BaselineEligibility }
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
  const [followupModelApiKey, setFollowupModelApiKey] = useState('');

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
          liveEpisodes: new Map(),
          activeLiveEpisodeKeys: new Set<string>(),
          activeLiveEpisodeKey: null,
          latestLiveEpisodeKey: null,
          coalescedEventCount: 0,
        };
        dispose = streamRunEvents(
          runId,
          (event) => {
            if (!liveRef.current) return;
            const next = reduceRunEvent(liveRef.current, event);
            liveRef.current = next;
            if (event.type === 'run.completed' || event.type === 'run.failed' || event.type === 'run.cancelled') {
              setState({ status: 'loaded', run: next.snapshot });
            }
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
  const followupRequiresModelApiKey = runRequiresModelApiKey(run);
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
  const outcomeCounts = run.outcome_counts ?? {
    pass: 0,
    fail: 0,
    error: 0,
    cancelled: 0,
    incomplete: run.progress.planned_lane_episodes,
  };
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
    const modelApiKey = followupModelApiKey.trim();
    if (followupRequiresModelApiKey && !modelApiKey) {
      setFollowupMessage(
        'Model API key is required for retry/resume because run secrets are not persisted.',
      );
      return;
    }
    setFollowupBusy(kind);
    setFollowupMessage(null);
    const followupInput = modelApiKey ? { execution: { modelApiKey } } : undefined;
    const request = kind === 'retry'
      ? retryRun(run.id, followupInput)
      : resumeRun(run.id, followupInput);
    request
      .then((result) => {
        setFollowupMessage(
          `${kind === 'retry' ? 'Retry' : 'Resume'} queued attempt ${result.attempt_no} for ${result.selected_lane_episodes.length} lane episodes.`,
        );
        setFollowupModelApiKey('');
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
            <span className="tp-run-state" data-testid="tp-run-state">
              {run.state}
            </span>
            {FOLLOWUP_RUN_STATES.has(run.state) && followupRequiresModelApiKey ? (
              <label className="tp-followup-secret">
                <span>Model API key</span>
                <input
                  type="password"
                  value={followupModelApiKey}
                  onChange={(event) => setFollowupModelApiKey(event.target.value)}
                  placeholder="Required for Retry/Resume"
                  autoComplete="off"
                />
              </label>
            ) : null}
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
        <dl
          className="tp-run-facts"
          data-testid="tp-run-completion-facts"
        >
          <div>
            <dt>Execution</dt>
            <dd>{run.state}</dd>
          </div>
          <div>
            <dt>Verdict</dt>
            <dd>{run.gate_verdict ?? 'pending'}</dd>
          </div>
          <div>
            <dt>Pass</dt>
            <dd data-count="pass">{outcomeCounts.pass}</dd>
          </div>
          <div>
            <dt>Fail</dt>
            <dd data-count="fail">{outcomeCounts.fail}</dd>
          </div>
          <div>
            <dt>Error</dt>
            <dd data-count="error">{outcomeCounts.error}</dd>
          </div>
          <div>
            <dt>Cancelled</dt>
            <dd data-count="cancelled">{outcomeCounts.cancelled}</dd>
          </div>
          <div>
            <dt>Incomplete</dt>
            <dd data-count="incomplete">{outcomeCounts.incomplete}</dd>
          </div>
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

      {run.imported ? (
        <section className="tp-alert">
          <h2>Imported legacy run</h2>
          <p>
            Source path: <span className="tp-mono">{run.imported.source_path}</span>
          </p>
          <p>
            Missing provenance: {run.imported.provenance_missing.length > 0
              ? run.imported.provenance_missing.join(', ')
              : 'none'}
          </p>
        </section>
      ) : null}

      <RunObservatory run={run} live={liveRef.current} diagnostics={diagnostics} />

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
        <section className="tp-panel" data-testid="tp-episode-attempts">
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
          {report.status === 'loaded' && report.report.comparison?.coverage ? (
            <p
              className="tp-comparison-coverage"
              data-testid="tp-comparison-coverage"
            >
              {['total_pairs', 'paired_pairs', 'unpaired_pairs', 'coverage_rate']
                .filter((key) => key in report.report.comparison.coverage)
                .map(
                  (key) => `${key}: ${report.report.comparison.coverage[key]}`,
                )
                .join(', ')}
            </p>
          ) : null}
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

      <section className="tp-panel" data-testid="tp-episode-identities">
        <h2>Episode identities</h2>
        <table>
          <thead>
            <tr>
              <th>Seq</th>
              <th>Group</th>
              <th>Task</th>
              <th>Episode</th>
              <th>Seed</th>
              <th>Trial</th>
              <th>Max steps</th>
            </tr>
          </thead>
          <tbody>
            {run.episode_identities.map((episode) => (
              <tr key={episode.episode_key}>
                <td>{formatSequenceIndex(episode.sequence_index)}</td>
                <td>{episode.sequence_group_id ?? '—'}</td>
                <td className="tp-mono">{episode.task_base_id}</td>
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
  const [baselineName, setBaselineName] = useState('');
  const defaultLaneKey =
    run.lanes.find((lane) => lane.role === 'baseline')?.lane_key ??
    run.lanes[0]?.lane_key ??
    '';
  const [selectedLaneKey, setSelectedLaneKey] = useState(defaultLaneKey);
  const [eligibility, setEligibility] = useState<BaselineEligibilityState>({
    status: 'loading',
  });

  useEffect(() => {
    if (report.status !== 'loaded' || !selectedLaneKey) return;
    let active = true;
    setEligibility({ status: 'loading' });
    getBaselineEligibility(run.id, selectedLaneKey)
      .then((value) => {
        if (active) setEligibility({ status: 'loaded', value });
      })
      .catch((error) => {
        if (active) {
          setEligibility({
            status: 'error',
            message: error instanceof Error ? error.message : 'Eligibility check failed.',
          });
        }
      });
    return () => {
      active = false;
    };
  }, [report.status, run.id, selectedLaneKey]);

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
  const sequenceGroups = data.sequence?.groups ?? [];
  const hasComparisonPairs = data.comparison.pairs.length > 0;
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
    const displayName = baselineName.trim();
    if (!displayName) {
      setMessage('Baseline name is required.');
      return;
    }
    promoteBaseline(run.id, displayName, selectedLaneKey)
      .then((baseline) => {
        setMessage(`Promoted ${baseline.display_name}.`);
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
          <label htmlFor="tp-baseline-lane">Baseline lane</label>
          <select
            id="tp-baseline-lane"
            value={selectedLaneKey}
            onChange={(event) => setSelectedLaneKey(event.target.value)}
          >
            {run.lanes.map((lane) => (
              <option key={lane.id} value={lane.lane_key}>
                {lane.lane_key}
              </option>
            ))}
          </select>
          <label htmlFor="tp-baseline-name">Baseline name</label>
          <input
            id="tp-baseline-name"
            value={baselineName}
            maxLength={80}
            onChange={(event) => setBaselineName(event.target.value)}
            placeholder="Required"
          />
          <button
            type="button"
            onClick={promote}
            disabled={
              eligibility.status !== 'loaded'
              || !eligibility.value.eligible
              || !baselineName.trim()
            }
          >
            Promote baseline
          </button>
        </div>
      </div>

      <div data-testid="tp-baseline-eligibility">
        {eligibility.status === 'loading' ? <p>Checking strict baseline eligibility...</p> : null}
        {eligibility.status === 'error' ? <p>{eligibility.message}</p> : null}
        {eligibility.status === 'loaded' && eligibility.value.eligible ? (
          <p>Eligible for strict baseline promotion.</p>
        ) : null}
        {eligibility.status === 'loaded' && !eligibility.value.eligible ? (
          <ul>
            {eligibility.value.reasons.map((reason) => (
              <li key={reason.code}>{reason.message}</li>
            ))}
          </ul>
        ) : null}
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

      {hasComparisonPairs ? (
        <label className="tp-inline-control">
          <input
            type="checkbox"
            checked={regressionsOnly}
            onChange={(event) => setRegressionsOnly(event.currentTarget.checked)}
          />
          Regression pairs only
        </label>
      ) : null}

      {message ? <p className="tp-kicker">{message}</p> : null}

      {data.reliability && data.reliability.tasks.length > 0 ? (
        <div data-testid="tp-report-reliability">
          <h3>Reliability</h3>
          <dl>
            <div>
              <dt>Pass@1</dt>
              <dd>{data.reliability.summary.pass_at_1 ?? '—'}</dd>
            </div>
            <div>
              <dt>Flaky tasks</dt>
              <dd>{data.reliability.summary.flaky_tasks}</dd>
            </div>
            <div>
              <dt>Insufficient trials</dt>
              <dd>{data.reliability.summary.insufficient_trials_tasks}</dd>
            </div>
          </dl>
          <table>
            <thead>
              <tr>
                <th>Lane</th>
                <th>Task</th>
                <th>Pass@1</th>
                <th>Valid</th>
                <th>Success</th>
                <th>Failure</th>
                <th>Flakiness</th>
              </tr>
            </thead>
            <tbody>
              {data.reliability.tasks.map((task) => (
                <tr key={`${task.lane_key}:${task.materialization_key}`}>
                  <td>{task.lane_key}</td>
                  <td className="tp-mono">{task.task_id}</td>
                  <td>{task.pass_at_k['1'] ?? '—'}</td>
                  <td>{task.counts.valid}</td>
                  <td>{task.counts.success}</td>
                  <td>{task.counts.failure}</td>
                  <td>{task.flakiness ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {data.infrastructure ? (
        <div data-testid="tp-report-infrastructure">
          <h3>Infrastructure</h3>
          <p>
            {data.infrastructure.available
              ? `${data.infrastructure.sources.length} monitor source(s) found.`
              : `No monitor data (${data.infrastructure.reason ?? 'unavailable'}).`}
          </p>
          {data.infrastructure.scan_truncated_lanes.length > 0 ? (
            <p className="tp-hint" role="alert">
              Scan overflow in lane(s): {data.infrastructure.scan_truncated_lanes.join(', ')}.
              Some multiprocess monitor sources may be missing.
            </p>
          ) : null}
          {data.infrastructure.excluded_source_count > 0 ? (
            <p className="tp-hint" role="alert">
              {data.infrastructure.excluded_source_count} monitor source(s) exceeded the limit and were excluded.
            </p>
          ) : null}
          {data.infrastructure.sources.map((src) => (
            <div key={`${src.lane_key}:${src.relative_path}`} data-testid={`tp-infra-source-${src.lane_key}`}>
              <div className="tp-kicker">
                {src.lane_key} · {src.status === 'truncated' ? 'partial' : src.status}
                {!src.available ? ` · ${src.status}` : ''}
                {src.sample_window ? ` · ${src.sample_window.sample_count} samples` : ''}
              </div>
              <table>
                <thead>
                  <tr><th>Dimension</th><th>Available</th><th>Key p95</th></tr>
                </thead>
                <tbody>
                  {Object.entries(src.dimensions).map(([dim, info]) => {
                    const metrics = info.metrics ?? {};
                    const firstKey = Object.keys(metrics)[0];
                    const p95 = firstKey ? metrics[firstKey]?.p95 : null;
                    return (
                      <tr key={dim}>
                        <td>{dim}</td>
                        <td>{info.available ? 'yes' : (info.reason ?? 'no')}</td>
                        <td>{firstKey ? `${firstKey}: ${p95 ?? '—'}` : '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {src.artifact_id ? (
                <a href={`/api/platform/v1/runs/${run.id}/artifacts/${encodeURIComponent(src.artifact_id)}/content`}>
                  Raw monitor data
                </a>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {sequenceGroups.length > 0 ? (
        <div className="tp-report-sequences" data-testid="tp-report-sequences">
          {sequenceGroups.map((group) => (
            <section
              key={group.sequence_group_id}
              data-testid={`tp-report-sequence-${group.sequence_group_id}`}
            >
              <div className="tp-report-subheading">
                <h3>Manual sequence</h3>
                <span className="tp-mono">{group.sequence_group_id}</span>
              </div>
              <dl className="tp-run-facts">
                <div>
                  <dt>Steps</dt>
                  <dd>{group.summary.planned_lane_episodes ?? group.items.length}</dd>
                </div>
                <div>
                  <dt>Success rate</dt>
                  <dd>{formatRate(group.summary.success_rate)}</dd>
                </div>
                <div>
                  <dt>Errors</dt>
                  <dd>{group.summary.errors ?? 0}</dd>
                </div>
              </dl>
              <table>
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Task</th>
                    <th>Lane</th>
                    <th>Outcome</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {group.items.map((item) => {
                    const outcome = item.outcome ?? item.status;
                    const outcomeClass = item.outcome ?? 'pending';
                    return (
                      <tr key={`${item.sequence_group_id}:${item.lane_key}:${item.episode_key}`}>
                        <td>{item.step ? `Step ${item.step}` : '—'}</td>
                        <td className="tp-mono">{item.task_id}</td>
                        <td>{item.lane_key}</td>
                        <td>
                          <span className={`tp-outcome tp-outcome-${outcomeClass.toLowerCase()}`}>
                            {outcome}
                          </span>
                        </td>
                        <td>{item.error_code ?? '—'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </section>
          ))}
        </div>
      ) : null}

      {hasComparisonPairs ? (
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
      ) : null}
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

function formatSequenceIndex(value: number | null | undefined) {
  return value === null || value === undefined ? '—' : String(value + 1);
}

function runRequiresModelApiKey(run: RunDetail): boolean {
  const lanes = Array.isArray(run.run_plan.lanes) ? run.run_plan.lanes : [];
  return lanes.some((lane) => {
    if (!lane || typeof lane !== 'object') return false;
    const runnerConfig = (lane as { runner_config?: unknown }).runner_config;
    return (
      !!runnerConfig
      && typeof runnerConfig === 'object'
      && (runnerConfig as { model_api_key_configured?: unknown }).model_api_key_configured === true
    );
  });
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
  const normalizedRoot = artifactRoot === '.' ? '' : artifactRoot;
  const runPath = `${runId}/${normalizedRoot}`.replace(/\\/g, '/').replace(/\/$/, '');
  return `/run_explorer.html?run=${encodeURIComponent(runPath)}`;
}

function artifactContentHref(runId: string, artifactId: string) {
  return `/api/platform/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(
    artifactId,
  )}/content`;
}
