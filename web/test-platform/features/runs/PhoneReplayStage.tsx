import { ChevronLeft, ChevronRight } from 'lucide-react';

import type { EpisodeReplay, EpisodeReplayStep } from '../../api/types';
import type { ReplayLoadState } from './episodeReplay';
import { artifactContentHref, stepImageArtifactId } from './episodeReplay';
import type { LiveEpisodeProgress } from './runEvents';

export function PhoneReplayStage({
  runId,
  replayState,
  selectedStepIndex,
  screenshotMode,
  onSelectStep,
  onScreenshotModeChange,
  liveProgress,
}: {
  runId: string;
  replayState: ReplayLoadState;
  selectedStepIndex: number;
  screenshotMode: 'annotated' | 'raw';
  onSelectStep: (index: number) => void;
  onScreenshotModeChange: (mode: 'annotated' | 'raw') => void;
  liveProgress: LiveEpisodeProgress | null;
}) {
  const replay = replayState.status === 'loaded' ? replayState.replay : null;
  const steps = replay?.steps ?? [];
  const selectedStep = steps[selectedStepIndex] ?? null;
  const artifactId = stepImageArtifactId(selectedStep, screenshotMode);
  const imageHref = artifactId ? artifactContentHref(runId, artifactId) : null;

  return (
    <div className="tp-phone-stage" data-testid="tp-phone-stage">
      <div className="tp-phone-stage-toolbar">
        <div>
          <span className="tp-kicker">Replay</span>
          <strong>{stageTitle(replayState, replay, liveProgress)}</strong>
        </div>
        <div className="tp-stage-controls" role="group" aria-label="Replay controls">
          <button
            type="button"
            aria-label="Previous replay step"
            onClick={() => onSelectStep(Math.max(0, selectedStepIndex - 1))}
            disabled={selectedStepIndex <= 0}
          >
            <ChevronLeft size={16} />
          </button>
          <span data-testid="tp-replay-step-count">
            {steps.length > 0 ? `${selectedStepIndex + 1} / ${steps.length}` : '0 / 0'}
          </span>
          <button
            type="button"
            aria-label="Next replay step"
            onClick={() => onSelectStep(Math.min(steps.length - 1, selectedStepIndex + 1))}
            disabled={steps.length === 0 || selectedStepIndex >= steps.length - 1}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </div>

      <div className="tp-phone-frame">
        <div className="tp-phone-screen">
          {imageHref ? (
            <img
              src={imageHref}
              alt={`Replay screenshot for step ${selectedStep?.step ?? selectedStepIndex + 1}`}
              data-testid="tp-replay-screenshot"
            />
          ) : (
            <StagePlaceholder
              replayState={replayState}
              selectedStep={selectedStep}
              liveProgress={liveProgress}
            />
          )}
        </div>
      </div>

      <div className="tp-phone-stage-footer">
        <div className="tp-step-summary">
          <span className="tp-kicker">Selected step</span>
          <strong>{selectedStep ? stepLabel(selectedStep) : 'No step selected'}</strong>
          {selectedStep?.thought ? <p>{selectedStep.thought}</p> : null}
        </div>
        <label className="tp-segmented-control">
          <span>Image</span>
          <select
            aria-label="Replay screenshot mode"
            value={screenshotMode}
            onChange={(event) => onScreenshotModeChange(event.currentTarget.value as 'annotated' | 'raw')}
          >
            <option value="annotated">Annotated</option>
            <option value="raw">Raw</option>
          </select>
        </label>
      </div>
    </div>
  );
}

function StagePlaceholder({
  replayState,
  selectedStep,
  liveProgress,
}: {
  replayState: ReplayLoadState;
  selectedStep: EpisodeReplayStep | null;
  liveProgress: LiveEpisodeProgress | null;
}) {
  let message = 'Replay screenshot will appear here.';
  if (liveProgress && replayState.status !== 'loaded') {
    message = liveProgress.stepCount > 0
      ? `Live run is recording step ${liveProgress.stepCount}; screenshots will appear after replay artifacts are available.`
      : 'Live episode has started; waiting for the first recorded step.';
  } else if (replayState.status === 'loading') {
    message = 'Loading replay...';
  } else if (replayState.status === 'empty' || replayState.status === 'error') {
    message = replayState.message;
  } else if (selectedStep) {
    message = 'This step has no screenshot artifact.';
  }
  return (
    <div className="tp-phone-placeholder" data-testid="tp-phone-placeholder">
      <span>{message}</span>
    </div>
  );
}

function stageTitle(
  replayState: ReplayLoadState,
  replay: EpisodeReplay | null,
  liveProgress: LiveEpisodeProgress | null,
) {
  if (replayState.status === 'loading') return 'Loading trajectory';
  if (!replay && liveProgress) return 'Live episode';
  if (!replay) return 'No trajectory loaded';
  return `${replay.lane_key} / attempt ${replay.attempt_no}`;
}

function stepLabel(step: EpisodeReplayStep) {
  const stepNo = step.step ?? '?';
  return `Step ${stepNo}: ${step.action_type || 'UNKNOWN'}`;
}
