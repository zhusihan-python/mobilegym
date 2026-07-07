import type { EpisodeReplayStep, RunDetail } from '../../api/types';
import {
  formatInlineJson,
  replayFromState,
  stepFromReplayState,
  type ReplayLoadState,
  type ReplayOption,
} from './episodeReplay';
import type { LiveEpisodeProgress, RunLiveState } from './runEvents';

export function AgentConsole({
  run,
  live,
  selectedOption,
  replayState,
  selectedStepIndex,
  liveProgress,
}: {
  run: RunDetail;
  live: RunLiveState | null;
  selectedOption: ReplayOption | null;
  replayState: ReplayLoadState;
  selectedStepIndex: number;
  liveProgress: LiveEpisodeProgress | null;
}) {
  const replay = replayFromState(replayState);
  const selectedStep = stepFromReplayState(replayState, selectedStepIndex);
  const totalSteps = replay?.steps.length ?? 0;
  const stepLabel = selectedStep
    ? `${selectedStep.step ?? selectedStepIndex + 1} / ${totalSteps}`
    : liveProgress && liveProgress.stepCount > 0
      ? `live ${liveProgress.stepCount}${liveProgress.maxSteps ? ` / ${liveProgress.maxSteps}` : ''}`
      : 'n/a';
  const action = selectedStep
    ? actionLabel(selectedStep)
    : liveProgress?.lastActionType ?? 'n/a';

  return (
    <aside className="tp-agent-console" aria-label="Replay console">
      <span className="tp-kicker">Agent console</span>
      <dl>
        <div>
          <dt>Run state</dt>
          <dd>{run.state}</dd>
        </div>
        <div>
          <dt>Stream</dt>
          <dd>{live?.connected ? 'connected' : 'snapshot'}</dd>
        </div>
        <div>
          <dt>Episode</dt>
          <dd className="tp-mono">{selectedOption?.episodeKey ?? 'n/a'}</dd>
        </div>
        <div>
          <dt>Lane</dt>
          <dd>{selectedOption?.laneKey ?? 'n/a'}</dd>
        </div>
        <div>
          <dt>Current step</dt>
          <dd>{stepLabel}</dd>
        </div>
        <div>
          <dt>Action</dt>
          <dd>{action}</dd>
        </div>
        <div>
          <dt>Thought</dt>
          <dd>{selectedStep?.thought || 'n/a'}</dd>
        </div>
        <div>
          <dt>Outcome</dt>
          <dd>
            {selectedOption?.outcome ? (
              <span className={`tp-outcome tp-outcome-${selectedOption.outcome.toLowerCase()}`}>
                {selectedOption.outcome}
              </span>
            ) : (
              'planned'
            )}
          </dd>
        </div>
        <div>
          <dt>Error</dt>
          <dd>{selectedOption?.errorCode ?? 'n/a'}</dd>
        </div>
      </dl>
    </aside>
  );
}

function actionLabel(step: EpisodeReplayStep) {
  const type = step.action_type || 'UNKNOWN';
  const data = formatInlineJson(step.action_data, '', 64);
  return data ? `${type} ${data}` : type;
}
