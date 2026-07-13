import type { EpisodeReplay, EpisodeReplayStep, RunDetail } from '../../api/types';

export type ReplayOption = {
  id: string;
  episodeAttemptId?: string | null;
  episodeKey: string;
  laneKey: string;
  attemptNo: number | 'latest';
  taskId: string;
  sequenceIndex: number | null;
  sequenceGroupId: string | null;
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
  const episodeMetadata = new Map(
    run.episode_identities.map((item) => [
      item.episode_key,
      {
        taskId: item.task_id,
        sequenceIndex: item.sequence_index,
        sequenceGroupId: item.sequence_group_id,
      },
    ]),
  );
  const attempts = run.episode_attempts ?? [];

  if (attempts.length > 0) {
    return sortReplayOptions(
      attempts.map((attempt, order) => {
        const outcome = attempt.outcome ? String(attempt.outcome).toUpperCase() : null;
        const metadata = episodeMetadata.get(attempt.episode_key);
        return {
          option: {
            id: replayOptionId(attempt.episode_key, attempt.lane_key, attempt.attempt_no),
            episodeAttemptId: attempt.episode_attempt_id ?? null,
            episodeKey: attempt.episode_key,
            laneKey: attempt.lane_key,
            attemptNo: attempt.attempt_no,
            taskId: metadata?.taskId ?? baseTaskLabel(attempt.episode_key),
            sequenceIndex: metadata?.sequenceIndex ?? null,
            sequenceGroupId: metadata?.sequenceGroupId ?? null,
            outcome,
            errorCode: attempt.error_code,
            artifactRoot: attempt.artifact_root,
          },
          order,
        };
      }),
    );
  }

  const laneKey = defaultLaneKey(run);
  return sortReplayOptions(
    run.episode_identities.map((episode, order) => ({
      option: {
        id: replayOptionId(episode.episode_key, laneKey, 'latest'),
        episodeAttemptId: null,
        episodeKey: episode.episode_key,
        laneKey,
        attemptNo: 'latest',
        taskId: episode.task_id,
        sequenceIndex: episode.sequence_index,
        sequenceGroupId: episode.sequence_group_id,
        outcome: null,
        errorCode: null,
        artifactRoot: null,
      },
      order,
    })),
  );
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
    const attemptDelta = attemptRank(b.attemptNo) - attemptRank(a.attemptNo);
    if (attemptDelta !== 0) return attemptDelta;
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

export function replayFromState(state: ReplayLoadState): EpisodeReplay | null {
  return state.status === 'loaded' ? state.replay : null;
}

export function stepFromReplayState(
  state: ReplayLoadState,
  selectedStepIndex: number,
): EpisodeReplayStep | null {
  const replay = replayFromState(state);
  return replay?.steps[selectedStepIndex] ?? null;
}

export function formatInlineJson(value: unknown, fallback = 'n/a', maxLength = 90) {
  if (value === null || value === undefined) {
    return fallback;
  }
  const text = typeof value === 'string' ? value : JSON.stringify(value);
  if (!text || text === '{}' || text === '[]') {
    return fallback;
  }
  return text.length > maxLength ? `${text.slice(0, maxLength - 3)}...` : text;
}

export function formatPrettyJson(value: unknown, fallback = 'n/a') {
  if (value === null || value === undefined) {
    return fallback;
  }
  return JSON.stringify(value, null, 2);
}

export function isAnswerCompletionAccepted(replay: EpisodeReplay | null) {
  return replay?.result?.answer_completion_accepted === true;
}

export function terminalMarkerForReplay(replay: EpisodeReplay | null) {
  if (!replay) {
    return null;
  }
  if (isAnswerCompletionAccepted(replay)) {
    return 'answer_completion_accepted';
  }
  const stopReason = readStringPath(replay.result, ['execution', 'stop_reason']);
  if (stopReason) {
    return stopReason;
  }
  return replay.error_code ?? replay.outcome;
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

function attemptRank(attemptNo: number | 'latest') {
  return attemptNo === 'latest' ? Number.MAX_SAFE_INTEGER : attemptNo;
}

function sortReplayOptions(entries: Array<{ option: ReplayOption; order: number }>) {
  return [...entries]
    .sort((a, b) => {
      const sequenceDelta =
        sequenceRank(a.option.sequenceIndex) - sequenceRank(b.option.sequenceIndex);
      if (sequenceDelta !== 0) {
        return sequenceDelta;
      }
      return a.order - b.order;
    })
    .map((entry) => entry.option);
}

function sequenceRank(sequenceIndex: number | null) {
  return typeof sequenceIndex === 'number' ? sequenceIndex : Number.MAX_SAFE_INTEGER;
}

function baseTaskLabel(episodeKey: string) {
  return episodeKey.split('|')[0] || episodeKey;
}

function readStringPath(value: unknown, path: string[]) {
  let current: unknown = value;
  for (const key of path) {
    if (!current || typeof current !== 'object' || !(key in current)) {
      return null;
    }
    current = (current as Record<string, unknown>)[key];
  }
  return typeof current === 'string' ? current : null;
}
