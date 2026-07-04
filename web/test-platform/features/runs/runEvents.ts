import type { RunDetail, RunEvent } from '../../api/types';

/**
 * Live run view model, updated by `reduceRunEvent` from the SSE stream.
 *
 * The snapshot is the authoritative REST-fetched RunDetail. `lastSequence`
 * tracks the highest committed event id seen so reconnects resume correctly.
 * `connected` and `replaying` reflect the SSE transport state, not run state.
 *
 * VS-07 parallel fields:
 * - `completedEpisodeKeys`: episode_keys that reached a terminal outcome
 *   (completed/error/cancelled), deduped so a reconnect never double-counts.
 * - `activeWorkers`: worker_ids with a `worker.started` but no matching
 *   `worker.stopped` yet, for the live "active workers" count.
 */
export type RunLiveState = {
  snapshot: RunDetail;
  lastSequence: number;
  connected: boolean;
  replaying: boolean;
  completedEpisodeKeys: Set<string>;
  activeWorkers: Set<string>;
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

  // Copy the mutable Sets only if this event will mutate them; otherwise share
  // the reference (keeps referential equality for no-op reductions).
  let completedEpisodeKeys = state.completedEpisodeKeys;
  let activeWorkers = state.activeWorkers;

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
    case 'episode.completed':
    case 'episode.error':
    case 'episode.cancelled': {
      // Mark this episode complete (deduped by key). The episode_key is sourced
      // from the payload first (where the runner puts it) and falls back to the
      // event-level field. Redeliveries are already filtered by lastSequence,
      // but the Set guards against cross-transport duplicates too.
      const key = episodeKeyOf(event);
      if (key !== null) {
        if (!completedEpisodeKeys.has(key)) {
          completedEpisodeKeys = new Set(completedEpisodeKeys);
          completedEpisodeKeys.add(key);
        }
      }
      next.completedEpisodeKeys = completedEpisodeKeys;
      break;
    }
    case 'worker.started': {
      const workerId = event.worker_id;
      if (workerId) {
        if (!activeWorkers.has(workerId)) {
          activeWorkers = new Set(activeWorkers);
          activeWorkers.add(workerId);
        }
      }
      next.activeWorkers = activeWorkers;
      break;
    }
    case 'worker.stopped': {
      const workerId = event.worker_id;
      if (workerId && activeWorkers.has(workerId)) {
        activeWorkers = new Set(activeWorkers);
        activeWorkers.delete(workerId);
      }
      next.activeWorkers = activeWorkers;
      break;
    }
    default:
      // Unknown / step / metric events: only advance lastSequence (already set).
      break;
  }

  return next;
}

/**
 * Extract the episode_key from an event. The runner places it in the payload;
 * the persisted envelope may also carry it at the top level. Returns null when
 * no key is available (the caller leaves the episode uncounted rather than
 * crashing).
 */
function episodeKeyOf(event: RunEvent): string | null {
  const fromPayload = event.payload?.episode_key;
  if (typeof fromPayload === 'string' && fromPayload.length > 0) {
    return fromPayload;
  }
  return null;
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
