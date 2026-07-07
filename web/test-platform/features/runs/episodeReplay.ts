import type { EpisodeReplay, EpisodeReplayStep, RunDetail } from '../../api/types';

export type ReplayOption = {
  id: string;
  episodeKey: string;
  laneKey: string;
  attemptNo: number | 'latest';
  taskId: string;
  outcome: string | null;
  errorCode: string | null;
  artifactRoot: string | null;
};

export type ReplayLoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'loaded'; replay: EpisodeReplay }
  | { status: 'empty'; message: string }
  | { status: 'error'; message: string };

const DEBUG_OUTCOME_RANK: Record<string, number> = {
  FAIL: 0,
  ERROR: 0,
  CANCELLED: 1,
  PASS: 2,
};

export function buildReplayOptions(run: RunDetail): ReplayOption[] {
  const taskByEpisode = new Map(run.episode_identities.map((item) => [item.episode_key, item.task_id]));
  const attempts = run.episode_attempts ?? [];

  if (attempts.length > 0) {
    return attempts.map((attempt) => {
      const outcome = attempt.outcome ? String(attempt.outcome).toUpperCase() : null;
      return {
        id: replayOptionId(attempt.episode_key, attempt.lane_key, attempt.attempt_no),
        episodeKey: attempt.episode_key,
        laneKey: attempt.lane_key,
        attemptNo: attempt.attempt_no,
        taskId: taskByEpisode.get(attempt.episode_key) ?? baseTaskLabel(attempt.episode_key),
        outcome,
        errorCode: attempt.error_code,
        artifactRoot: attempt.artifact_root,
      };
    });
  }

  const laneKey = defaultLaneKey(run);
  return run.episode_identities.map((episode) => ({
    id: replayOptionId(episode.episode_key, laneKey, 'latest'),
    episodeKey: episode.episode_key,
    laneKey,
    attemptNo: 'latest',
    taskId: episode.task_id,
    outcome: null,
    errorCode: null,
    artifactRoot: null,
  }));
}

export function chooseDefaultReplayOption(options: ReplayOption[]): ReplayOption | null {
  if (options.length === 0) {
    return null;
  }
  return [...options].sort((a, b) => {
    const aRank = outcomeRank(a.outcome);
    const bRank = outcomeRank(b.outcome);
    if (aRank !== bRank) return aRank - bRank;
    if (a.laneKey !== b.laneKey) {
      if (a.laneKey === 'candidate') return -1;
      if (b.laneKey === 'candidate') return 1;
    }
    return 0;
  })[0];
}

export function stepImageArtifactId(
  step: EpisodeReplayStep | null,
  mode: 'annotated' | 'raw',
): string | null {
  if (!step) {
    return null;
  }
  if (mode === 'annotated') {
    return step.screenshot_annotated_artifact_id ?? step.screenshot_artifact_id;
  }
  return step.screenshot_artifact_id;
}

export function artifactContentHref(runId: string, artifactId: string) {
  return `/api/platform/v1/runs/${encodeURIComponent(runId)}/artifacts/${encodeURIComponent(
    artifactId,
  )}/content`;
}

export function replayOptionId(
  episodeKey: string,
  laneKey: string,
  attemptNo: number | 'latest',
) {
  return `${laneKey}::${episodeKey}::${attemptNo}`;
}

function defaultLaneKey(run: RunDetail) {
  if (run.lanes.some((lane) => lane.lane_key === 'candidate')) {
    return 'candidate';
  }
  return run.lanes[0]?.lane_key ?? 'candidate';
}

function outcomeRank(outcome: string | null) {
  if (!outcome) return 3;
  return DEBUG_OUTCOME_RANK[outcome.toUpperCase()] ?? 3;
}

function baseTaskLabel(episodeKey: string) {
  return episodeKey.split('|')[0] || episodeKey;
}
