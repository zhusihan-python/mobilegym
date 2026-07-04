import type { RunDetail, RunEvent } from '../../api/types';

/**
 * Live run view model, updated by `reduceRunEvent` from the SSE stream.
 *
 * The snapshot is the authoritative REST-fetched RunDetail. `lastSequence`
 * tracks the highest committed event id seen so reconnects resume correctly.
 * `connected` and `replaying` reflect the SSE transport state, not run state.
 */
export type RunLiveState = {
  snapshot: RunDetail;
  lastSequence: number;
  connected: boolean;
  replaying: boolean;
};

/**
 * Per-outcome counters derived from episode_attempts. The run.state itself is
 * driven by run.* events; these counters track functional progress.
 */
export type EpisodeCounts = {
  pass: number;
  fail: number;
  error: number;
  cancelled: number;
};

/**
 * Reduce one committed run event into the live state.
 *
 * This is a PURE function — it has no side effects and is the unit under test.
 * Unknown event types must not crash the UI; they only advance `lastSequence`.
 */
export function reduceRunEvent(state: RunLiveState, event: RunEvent): RunLiveState {
  // Events arrive in order, but a reconnect may redeliver one we already saw.
  if (event.sequence <= state.lastSequence) {
    return state;
  }

  const next: RunLiveState = {
    ...state,
    lastSequence: event.sequence,
  };

  switch (event.type) {
    case 'run.started':
      next.snapshot = { ...state.snapshot, state: 'running' };
      break;
    case 'run.cancel_requested':
      // Keep displaying `running` until the terminal cancel event lands — the
      // request flag is orthogonal to the persisted lifecycle state.
      next.snapshot = { ...state.snapshot };
      break;
    case 'run.cancelled':
      next.snapshot = { ...state.snapshot, state: 'cancelled' };
      break;
    case 'run.completed':
      next.snapshot = { ...state.snapshot, state: 'completed' };
      break;
    case 'run.failed':
      next.snapshot = { ...state.snapshot, state: 'failed' };
      break;
    default:
      // Unknown / step / metric events: only advance lastSequence (already set).
      break;
  }

  return next;
}

/**
 * Tally episode outcome counts from a snapshot's episode_attempts.
 */
export function countEpisodes(snapshot: RunDetail): EpisodeCounts {
  const counts: EpisodeCounts = { pass: 0, fail: 0, error: 0, cancelled: 0 };
  for (const attempt of snapshot.episode_attempts ?? []) {
    const outcome = (attempt.outcome ?? '').toUpperCase();
    if (outcome === 'PASS') counts.pass += 1;
    else if (outcome === 'FAIL') counts.fail += 1;
    else if (outcome === 'ERROR') counts.error += 1;
    else if (outcome === 'CANCELLED') counts.cancelled += 1;
  }
  return counts;
}
