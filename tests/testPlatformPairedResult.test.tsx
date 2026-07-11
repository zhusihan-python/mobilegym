import { describe, it, expect, vi } from 'vitest';

import type { RunDetail, RunEvent, Comparison } from '../web/test-platform/api/types';
import {
  reduceRunEvent,
  type RunLiveState,
  type ShardHealth,
} from '../web/test-platform/features/runs/runEvents';
import { getComparison } from '../web/test-platform/api/client';

function pairedSnapshot(state = 'running'): RunDetail {
  return {
    id: 'r1',
    project_id: 'p1',
    workflow_version_id: 'wv1',
    name: 'paired run',
    state,
    fingerprint: 'fp',
    progress: {
      planned_episodes: 1,
      planned_lane_episodes: 2,
      completed_episodes: 0,
      completed_lane_episodes: 0,
    },
    outcome_counts: { pass: 0, fail: 0, error: 0, cancelled: 0, incomplete: 2 },
    lanes: [
      { id: 'lane-b', lane_key: 'baseline', role: 'baseline', target_id: 't1', target_revision_id: 'rev1' },
      { id: 'lane-c', lane_key: 'candidate', role: 'candidate', target_id: 't2', target_revision_id: 'rev2' },
    ],
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

function pairedState(): RunLiveState {
  return {
    snapshot: pairedSnapshot(),
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

describe('reduceRunEvent — paired run progress (lane-aware dedup)', () => {
  it('counts the SAME episode_key twice when each comes from a different lane_attempt_id', () => {
    // Contract 9: paired runs dedup by lane_attempt_id + episode_key (not just
    // episode_key), so baseline + candidate of the same episode both count.
    let state = pairedState();
    // Baseline lane completes episode k0.
    state = reduceRunEvent(
      state,
      event('episode.completed', 1, { episode_key: 'k0' }, { lane_attempt_id: 'la-baseline' }),
    );
    expect(state.completedEpisodeKeys.size).toBe(1);
    // Candidate lane completes the SAME episode_key k0 — must count again
    // (different lane_attempt_id).
    state = reduceRunEvent(
      state,
      event('episode.completed', 2, { episode_key: 'k0' }, { lane_attempt_id: 'la-candidate' }),
    );
    expect(state.completedEpisodeKeys.size).toBe(2);

    // Redelivering the baseline one (same lane_attempt_id + episode_key) does
    // NOT double-count.
    state = reduceRunEvent(
      state,
      event('episode.completed', 3, { episode_key: 'k0' }, { lane_attempt_id: 'la-baseline' }),
    );
    expect(state.completedEpisodeKeys.size).toBe(2);
  });

  it('falls back to episode_key-only dedup when lane_attempt_id is absent (single-lane runs)', () => {
    // Single-lane events have no lane_attempt_id; legacy dedup still works.
    let state = pairedState();
    state = reduceRunEvent(state, event('episode.completed', 1, { episode_key: 'k0' }));
    state = reduceRunEvent(state, event('episode.completed', 2, { episode_key: 'k0' }));
    expect(state.completedEpisodeKeys.size).toBe(1);
  });
});

describe('Comparison type + getComparison client', () => {
  it('getComparison calls the comparison endpoint for a run', async () => {
    const comparison: Comparison = {
      id: 'comp1',
      run_id: 'r1',
      run_attempt_id: 'ra1',
      baseline_lane_id: 'lane-b',
      candidate_lane_id: 'lane-c',
      policy: {},
      summary: { regression: 1 },
      created_at: '2026-07-04T00:00:00.000Z',
      pairs: [
        {
          id: 'pair1',
          comparison_id: 'comp1',
          pair_key: 'k0',
          baseline_episode_attempt_id: 'ea-b',
          candidate_episode_attempt_id: 'ea-c',
          classification: 'regression',
          integrity: { status: 'ok' },
          delta: { baseline_outcome: 'PASS', candidate_outcome: 'FAIL' },
          prepared: {
            params: { choice: 'sampled-1' },
            instruction: 'Choose sampled-1',
            projection_hash: 'sha256:proj',
          },
        },
      ],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(comparison), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchMock);
    try {
      const result = await getComparison('r1');
      const firstPair = result.pairs[0];
      expect(firstPair).toBeDefined();
      expect(firstPair.classification).toBe('regression');
      expect(firstPair.prepared?.params).toEqual({ choice: 'sampled-1' });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const url = String(fetchMock.mock.calls[0][0]);
      expect(url).toContain('/runs/r1/comparison');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
