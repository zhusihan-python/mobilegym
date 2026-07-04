import { describe, it, expect } from 'vitest';

import type { RunDetail, RunEvent } from '../web/test-platform/api/types';
import { reduceRunEvent, countEpisodes, type RunLiveState } from '../web/test-platform/features/runs/runEvents';

function baseSnapshot(state = 'running'): RunDetail {
  return {
    id: 'r1',
    project_id: 'p1',
    workflow_version_id: 'wv1',
    name: 'run',
    state,
    fingerprint: 'fp',
    progress: { planned_episodes: 1, planned_lane_episodes: 1, completed_episodes: 0 },
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
  return { snapshot: baseSnapshot(), lastSequence: 0, connected: true, replaying: false };
}

function event(type: string, sequence: number, payload: Record<string, unknown> = {}): RunEvent {
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
  };
}

describe('reduceRunEvent', () => {
  it('transitions run state on run.* lifecycle events', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('run.started', 1));
    expect(state.snapshot.state).toBe('running');

    state = reduceRunEvent(state, event('run.cancel_requested', 2));
    // cancel_requested is orthogonal; state stays running until terminal.
    expect(state.snapshot.state).toBe('running');

    state = reduceRunEvent(state, event('run.cancelled', 3));
    expect(state.snapshot.state).toBe('cancelled');
  });

  it('reaches completed on run.completed', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('run.completed', 1));
    expect(state.snapshot.state).toBe('completed');
    expect(state.lastSequence).toBe(1);
  });

  it('reaches failed on run.failed', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('run.failed', 1));
    expect(state.snapshot.state).toBe('failed');
  });

  it('advances lastSequence for unknown event types without crashing', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('episode.step_recorded', 7, { step: 3 }));
    expect(state.lastSequence).toBe(7);
    expect(state.snapshot.state).toBe('running');
  });

  it('ignores redelivered events with sequence <= lastSequence (no double counting)', () => {
    let state = baseState();
    state = reduceRunEvent(state, event('run.completed', 5));
    expect(state.lastSequence).toBe(5);
    const before = state;
    // A reconnect redelivers sequence 5 — state must be unchanged.
    state = reduceRunEvent(state, event('run.completed', 5));
    expect(state).toBe(before);
    // And an out-of-order older event is ignored too.
    state = reduceRunEvent(state, event('run.started', 2));
    expect(state).toBe(before);
  });
});

describe('countEpisodes', () => {
  it('tallies PASS/FAIL/ERROR/CANCELLED outcomes', () => {
    const snapshot = baseSnapshot();
    snapshot.episode_attempts = [
      { episode_key: 'e1', lane_key: 'candidate', attempt_no: 1, state: 'completed', outcome: 'PASS', error_code: null, artifact_root: 'a' },
      { episode_key: 'e2', lane_key: 'candidate', attempt_no: 1, state: 'completed', outcome: 'FAIL', error_code: 'ASSERTION_FAILURE', artifact_root: 'b' },
      { episode_key: 'e3', lane_key: 'candidate', attempt_no: 1, state: 'completed', outcome: 'ERROR', error_code: 'EXECUTION_ERROR', artifact_root: 'c' },
      { episode_key: 'e4', lane_key: 'candidate', attempt_no: 1, state: 'cancelled', outcome: 'CANCELLED', error_code: 'CANCELLED', artifact_root: 'd' },
    ];
    expect(countEpisodes(snapshot)).toEqual({ pass: 1, fail: 1, error: 1, cancelled: 1 });
  });

  it('returns zeros when episode_attempts is absent', () => {
    expect(countEpisodes(baseSnapshot())).toEqual({ pass: 0, fail: 0, error: 0, cancelled: 0 });
  });
});
