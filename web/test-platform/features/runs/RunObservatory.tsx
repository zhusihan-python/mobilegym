import { useEffect, useMemo, useState } from 'react';

import { getEpisodeReplay } from '../../api/client';
import type { RunDetail } from '../../api/types';
import { AgentConsole } from './AgentConsole';
import { EpisodePicker } from './EpisodePicker';
import { EvidenceDock } from './EvidenceDock';
import { PhoneReplayStage } from './PhoneReplayStage';
import { StepTimeline } from './StepTimeline';
import {
  buildReplayOptions,
  chooseDefaultReplayOption,
  type ReplayLoadState,
} from './episodeReplay';
import type { RunLiveState } from './runEvents';

export function RunObservatory({
  run,
  live,
}: {
  run: RunDetail;
  live: RunLiveState | null;
}) {
  const options = useMemo(() => buildReplayOptions(run), [run]);
  const defaultOption = useMemo(() => chooseDefaultReplayOption(options), [options]);
  const [selectedId, setSelectedId] = useState<string | null>(defaultOption?.id ?? null);
  const [replayState, setReplayState] = useState<ReplayLoadState>({ status: 'idle' });
  const [selectedStepIndex, setSelectedStepIndex] = useState(0);
  const [screenshotMode, setScreenshotMode] = useState<'annotated' | 'raw'>('annotated');

  useEffect(() => {
    setSelectedId((current) => {
      if (current && options.some((option) => option.id === current)) {
        return current;
      }
      return defaultOption?.id ?? null;
    });
  }, [defaultOption?.id, options]);

  const selectedOption = options.find((option) => option.id === selectedId) ?? defaultOption;

  useEffect(() => {
    if (!selectedOption) {
      setReplayState({ status: 'empty', message: 'No episode has been planned for this run.' });
      setSelectedStepIndex(0);
      return;
    }

    let active = true;
    setReplayState({ status: 'loading' });
    setSelectedStepIndex(0);
    getEpisodeReplay({
      runId: run.id,
      episodeKey: selectedOption.episodeKey,
      laneKey: selectedOption.laneKey,
      attemptNo: selectedOption.attemptNo,
    })
      .then((replay) => {
        if (!active) return;
        setReplayState({ status: 'loaded', replay });
        setSelectedStepIndex(Math.max(0, replay.steps.length - 1));
      })
      .catch((error) => {
        if (!active) return;
        setReplayState({
          status: run.state === 'running' || run.state === 'queued'
            ? 'empty'
            : 'error',
          message: error instanceof Error
            ? error.message
            : 'Replay artifacts are not available yet.',
        });
      });
    return () => {
      active = false;
    };
  }, [run.id, run.state, selectedOption?.attemptNo, selectedOption?.episodeKey, selectedOption?.laneKey]);

  return (
    <section className="tp-observatory" data-testid="tp-run-observatory">
      <div className="tp-observatory-header">
        <div>
          <span className="tp-kicker">Run Observatory</span>
          <h2>Simulator replay</h2>
        </div>
        <EpisodePicker
          options={options}
          selectedId={selectedOption?.id ?? null}
          onSelect={setSelectedId}
        />
      </div>

      <div className="tp-observatory-grid">
        <StepTimeline
          replayState={replayState}
          selectedStepIndex={selectedStepIndex}
          onSelectStep={setSelectedStepIndex}
        />
        <PhoneReplayStage
          runId={run.id}
          replayState={replayState}
          selectedStepIndex={selectedStepIndex}
          screenshotMode={screenshotMode}
          onSelectStep={setSelectedStepIndex}
          onScreenshotModeChange={setScreenshotMode}
        />
        <div className="tp-observatory-side">
          <AgentConsole
            run={run}
            live={live}
            selectedOption={selectedOption ?? null}
            replayState={replayState}
            selectedStepIndex={selectedStepIndex}
          />
          <EvidenceDock
            runId={run.id}
            replayState={replayState}
            selectedStepIndex={selectedStepIndex}
          />
        </div>
      </div>
    </section>
  );
}
