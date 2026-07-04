import { describe, it, expect } from 'vitest';

import type { RunDetail, RunEvent } from '../web/test-platform/api/types';
import { reduceRunEvent, countEpisodes, type RunLiveState } from '../web/test-platform/features/runs/runEvents';

function baseSnapshot(state = 'running'): RunDetail {
  return {
    id: 'r1',
    project_id: 'p1',
    workflow_version_id: 'wv1',
    name: 'parallel run',
    state,
    fingerprint: 'fp',
    progress: { planned_episodes: 4, planned_lane_episodes: 4, completed_episodes: 0 },
    lanes: [],
    gate_verdict: null,
    created_at: '2026-07-04T00:00:00.000Z',
    started_at: null,
    ended_at: null,
    run_plan: {},
    target_revisions: [],
    episode_identities: [],
    episode_attempts: [],
  };
}

function baseState(): RunLiveState {
  return {
    snapshot: baseSnapshot(),
    lastSequence: 0,
    connected: true,
    replaying: false,
    completedEpisodeKeys: new Set<string>(),
    activeWorkers: new Set<string>(),
  };
}

function event(type: string, sequence: number, payload: Record<string, unknown> = {}, extra: Partial<RunEvent> = {}): RunEvent {
  return {
    id: `e${sequence}`,
    run_id: 'r1',
    sequence,
    type,
    occurred_at: '2026-07-04T00:00:00.000Z',
    payload,
    payload_version: 1,
    run_attempt_id: null,
    lane_id: null,
    lane_attempt_id: null,
    episode_id: null,
    episode_attempt_id: null,
    worker_id: null,
    ...extra,
  };
}

describe('reduceRunEvent — parallel worker health', () => {
  it('tracks active workers via worker.started / worker.stopped', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('worker.started', 1, {}, { worker_id: 'W0' }));
    expect(state.activeWorkers).toEqual(new Set(['W0']));

    state = reduceRunEvent(state, event('worker.started', 2, {}, { worker_id: 'W1' }));
    expect(state.activeWorkers).toEqual(new Set(['W0', 'W1']));

    // One worker stops: removed from the active set.
    state = reduceRunEvent(state, event('worker.stopped', 3, {}, { worker_id: 'W0' }));
    expect(state.activeWorkers).toEqual(new Set(['W1']));
  });

  it('ignores worker.stopped for an unknown worker (idempotent)', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('worker.stopped', 1, {}, { worker_id: 'W9' }));
    expect(state.activeWorkers.size).toBe(0);
  });
});

describe('reduceRunEvent — parallel episode progress (deduped)', () => {
  it('counts completed episodes by episode_key (deduped across reconnect)', () => {
    let state = baseState();
    // Two episodes complete (keys k0, k1).
    state = reduceRunEvent(state, event('episode.completed', 1, { episode_key: 'k0' }, { worker_id: 'W0' }));
    state = reduceRunEvent(state, event('episode.completed', 2, { episode_key: 'k1' }, { worker_id: 'W1' }));
    expect(state.completedEpisodeKeys).toEqual(new Set(['k0', 'k1']));

    // Redelivering the same episode_key does NOT double-count.
    state = reduceRunEvent(state, event('episode.completed', 3, { episode_key: 'k0' }, { worker_id: 'W0' }));
    expect(state.completedEpisodeKeys.size).toBe(2);
  });

  it('counts error and cancelled episodes toward completed', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('episode.error', 1, { episode_key: 'k0' }, { worker_id: 'W0' }));
    state = reduceRunEvent(state, event('episode.cancelled', 2, { episode_key: 'k1' }, { worker_id: 'W1' }));
    expect(state.completedEpisodeKeys).toEqual(new Set(['k0', 'k1']));
  });

  it('falls back to event.worker_id/episode_key fields when payload lacks them', () => {
    let state = baseState();
    state = reduceRunEvent(
      state,
      event('episode.completed', 1, {}, { worker_id: 'W0', episode_id: 'k0' }),
    );
    // episode_key is null on the event (only episode_id set); the reducer should
    // not crash and should simply not add to the set (no episode_key available).
    expect(state.completedEpisodeKeys.size).toBe(0);
  });
});
