import type { EpisodeReplay, EpisodeReplayStep } from '../../api/types';
import {
  formatInlineJson,
  replayFromState,
  terminalMarkerForReplay,
  type ReplayLoadState,
} from './episodeReplay';
import type { LiveEpisodeProgress } from './runEvents';

export function StepTimeline({
  replayState,
  selectedStepIndex,
  onSelectStep,
  liveProgress,
  coalescedEventCount,
}: {
  replayState: ReplayLoadState;
  selectedStepIndex: number;
  onSelectStep: (index: number) => void;
  liveProgress: LiveEpisodeProgress | null;
  coalescedEventCount: number;
}) {
  const replay = replayFromState(replayState);
  const steps = replay?.steps ?? [];
  const liveStepCount = liveProgress?.stepCount ?? 0;

  return (
    <aside className="tp-step-timeline" aria-label="Step timeline">
      <div className="tp-dock-header">
        <span className="tp-kicker">Steps</span>
        <strong>
          {steps.length > 0
            ? `${steps.length} recorded`
            : liveStepCount > 0
              ? `${liveStepCount} live`
              : 'No steps yet'}
        </strong>
      </div>
      {replay && steps.length > 0 ? (
        <ol>
          {steps.map((step, index) => (
            <StepTimelineItem
              key={`${step.step ?? index}:${step.action_type}:${index}`}
              replay={replay}
              step={step}
              index={index}
              selected={index === selectedStepIndex}
              terminal={index === steps.length - 1}
              onSelectStep={onSelectStep}
            />
          ))}
        </ol>
      ) : liveProgress ? (
        <div className="tp-live-step-card" aria-live="polite">
          <span className="tp-step-index">{liveStepCount || 0}</span>
          <span className="tp-step-copy">
            <strong>{liveStepCount > 0 ? `Live step ${liveStepCount}` : 'Episode started'}</strong>
            <small>{liveProgress.lastActionType ?? 'Waiting for first recorded step'}</small>
          </span>
        </div>
      ) : (
        <p className="tp-dock-empty">{emptyTimelineMessage(replayState)}</p>
      )}
      {coalescedEventCount > 0 ? (
        <p className="tp-live-warning">
          {coalescedEventCount} live event batch was coalesced; replay artifacts remain authoritative.
        </p>
      ) : null}
    </aside>
  );
}

function StepTimelineItem({
  replay,
  step,
  index,
  selected,
  terminal,
  onSelectStep,
}: {
  replay: EpisodeReplay;
  step: EpisodeReplayStep;
  index: number;
  selected: boolean;
  terminal: boolean;
  onSelectStep: (index: number) => void;
}) {
  const marker = terminal ? terminalMarkerForReplay(replay) : null;
  return (
    <li>
      <button
        type="button"
        className={selected ? 'selected' : ''}
        aria-current={selected ? 'step' : undefined}
        onClick={() => onSelectStep(index)}
      >
        <span className="tp-step-index">{step.step ?? index + 1}</span>
        <span className="tp-step-copy">
          <strong>
            Step {step.step ?? index + 1} {step.action_type || 'UNKNOWN'}
          </strong>
          <small>{formatInlineJson(step.action_data, 'No action data')}</small>
        </span>
        {marker ? (
          <span className={`tp-terminal-marker ${terminalMarkerClass(marker)}`}>{marker}</span>
        ) : null}
      </button>
    </li>
  );
}

function emptyTimelineMessage(replayState: ReplayLoadState) {
  if (replayState.status === 'loading') {
    return 'Loading replay steps...';
  }
  if (replayState.status === 'empty' || replayState.status === 'error') {
    return replayState.message;
  }
  return 'Recorded steps will appear here.';
}

function terminalMarkerClass(marker: string) {
  const normalized = marker.toLowerCase();
  if (
    normalized === 'complete' ||
    normalized === 'pass' ||
    normalized === 'answer_completion_accepted'
  ) {
    return 'tp-terminal-success';
  }
  if (normalized === 'error' || normalized.includes('error')) {
    return 'tp-terminal-error';
  }
  return 'tp-terminal-warning';
}
