import { describe, it, expect } from 'vitest';

import type { RunDetail, RunEvent } from '../web/test-platform/api/types';
import {
  reduceRunEvent,
  type RunLiveState,
  type ShardHealth,
} from '../web/test-platform/features/runs/runEvents';

function baseSnapshot(state = 'running'): RunDetail {
  return {
    id: 'r1',
    project_id: 'p1',
    workflow_version_id: 'wv1',
    name: 'multiprocess run',
    state,
    fingerprint: 'fp',
    progress: { planned_episodes: 4, planned_lane_episodes: 4, completed_episodes: 0, completed_lane_episodes: 0 },
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
    activeShards: new Map<string, ShardHealth>(),
    liveEpisodes: new Map(),
    activeLiveEpisodeKeys: new Set<string>(),
    activeLiveEpisodeKey: null,
    latestLiveEpisodeKey: null,
    coalescedEventCount: 0,
  };
}

function event(
  type: string,
  sequence: number,
  payload: Record<string, unknown> = {},
  extra: Partial<RunEvent> = {},
): RunEvent {
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

describe('reduceRunEvent — multiprocess shard health', () => {
  it('tracks shard status via shard.started / shard.stopped', () => {
    let state = baseState();
    // Two shards start (rank 0 and 1).
    state = reduceRunEvent(state, event('shard.started', 1, { shard_rank: 0 }));
    state = reduceRunEvent(state, event('shard.started', 2, { shard_rank: 1 }));
    expect(state.activeShards.get('p00')?.status).toBe('started');
    expect(state.activeShards.get('p01')?.status).toBe('started');

    // One shard stops cleanly (exitcode 0).
    state = reduceRunEvent(
      state,
      event('shard.stopped', 3, { shard_rank: 0, exitcode: 0 }),
    );
    expect(state.activeShards.get('p00')?.status).toBe('stopped');
    expect(state.activeShards.get('p00')?.exitcode).toBe(0);
    // The other shard is still started.
    expect(state.activeShards.get('p01')?.status).toBe('started');
  });

  it('records a fatal shard exitcode from shard.fatal', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('shard.started', 1, { shard_rank: 0 }));
    // A fatal crash with exitcode 137.
    state = reduceRunEvent(
      state,
      event('shard.fatal', 2, { shard_rank: 0, exitcode: 137, error: 'OOMKilled' }),
    );
    expect(state.activeShards.get('p00')?.status).toBe('fatal');
    expect(state.activeShards.get('p00')?.exitcode).toBe(137);
  });

  it('derives shard rank key from payload shard_rank', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('shard.started', 1, { shard_rank: 5 }));
    expect(state.activeShards.get('p05')?.status).toBe('started');
  });
});

describe('reduceRunEvent — completed episodes deduped by episode_key', () => {
  it('counts completed from episode.* events (deduped)', () => {
    let state = baseState();
    state = reduceRunEvent(
      state,
      event('episode.completed', 1, { episode_key: 'k0' }, { worker_id: 'p00-W0' }),
    );
    state = reduceRunEvent(
      state,
      event('episode.completed', 2, { episode_key: 'k1' }, { worker_id: 'p00-W0' }),
    );
    state = reduceRunEvent(
      state,
      event('episode.completed', 3, { episode_key: 'k0' }, { worker_id: 'p01-W0' }),
    );
    // Deduped: only k0, k1 counted (redelivery of k0 ignored).
    expect(state.completedEpisodeKeys).toEqual(new Set(['k0', 'k1']));
  });
});

describe('reduceRunEvent — stream.events_coalesced', () => {
  it('advances lastSequence without crashing', () => {
    let state = baseState();
    state = reduceRunEvent(
      state,
      event('stream.events_coalesced', 7, { total: 42, by_shard: { p00: 42 } }),
    );
    // The coalesced event must be acknowledged (lastSequence advances).
    expect(state.lastSequence).toBe(7);
  });
});
