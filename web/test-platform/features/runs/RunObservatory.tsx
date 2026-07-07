import { useEffect, useMemo, useState } from 'react';

import { getEpisodeReplay } from '../../api/client';
import type { RunDetail } from '../../api/types';
import { AgentConsole } from './AgentConsole';
import { EpisodePicker } from './EpisodePicker';
import { EvidenceDock, type EvidenceDiagnosticsState } from './EvidenceDock';
import { PhoneReplayStage } from './PhoneReplayStage';
import { RunSettingsDrawer } from './RunSettingsDrawer';
import { StepTimeline } from './StepTimeline';
import {
  buildReplayOptions,
  chooseDefaultReplayOption,
  type ReplayLoadState,
  type ReplayOption,
} from './episodeReplay';
import type { LiveEpisodeProgress, RunLiveState } from './runEvents';

export function RunObservatory({
  run,
  live,
  diagnostics,
}: {
  run: RunDetail;
  live: RunLiveState | null;
  diagnostics: EvidenceDiagnosticsState;
}) {
  const options = useMemo(() => buildReplayOptions(run), [run]);
  const defaultOption = useMemo(() => chooseDefaultReplayOption(options), [options]);
  const [selectedId, setSelectedId] = useState<string | null>(defaultOption?.id ?? null);
  const [selectionTouched, setSelectionTouched] = useState(false);
  const [replayState, setReplayState] = useState<ReplayLoadState>({ status: 'idle' });
  const [selectedStepIndex, setSelectedStepIndex] = useState(0);
  const [screenshotMode, setScreenshotMode] = useState<'annotated' | 'raw'>('annotated');
  const [settingsOpen, setSettingsOpen] = useState(false);

  useEffect(() => {
    setSelectedId((current) => {
      if (current && options.some((option) => option.id === current)) {
        return current;
      }
      return defaultOption?.id ?? null;
    });
  }, [defaultOption?.id, options]);

  const activeLiveProgress = liveProgressByKey(live, live?.activeLiveEpisodeKey)
    ?? liveProgressByKey(live, live?.latestLiveEpisodeKey);
  const activeLiveOption = liveOptionForProgress(options, activeLiveProgress);
  useEffect(() => {
    if (!selectionTouched && activeLiveOption) {
      setSelectedId(activeLiveOption.id);
    }
  }, [activeLiveOption?.id, selectionTouched]);

  const selectedOption = options.find((option) => option.id === selectedId) ?? defaultOption;
  const selectedLiveProgress = selectedOption
    ? liveProgressForOption(live, selectedOption)
    : activeLiveProgress;

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
  }, [
    run.id,
    run.state,
    selectedLiveProgress?.terminalType,
    selectedOption?.attemptNo,
    selectedOption?.episodeKey,
    selectedOption?.laneKey,
  ]);

  const selectEpisode = (id: string) => {
    setSelectionTouched(true);
    setSelectedId(id);
  };

  return (
    <section className="tp-observatory" data-testid="tp-run-observatory">
      <div className="tp-observatory-header">
        <div>
          <span className="tp-kicker">Run Observatory</span>
          <h2>Simulator replay</h2>
        </div>
        <div className="tp-observatory-actions">
          <EpisodePicker
            options={options}
            selectedId={selectedOption?.id ?? null}
            onSelect={selectEpisode}
          />
          <button type="button" onClick={() => setSettingsOpen((open) => !open)}>
            Settings
          </button>
        </div>
      </div>
      {settingsOpen ? (
        <RunSettingsDrawer run={run} onClose={() => setSettingsOpen(false)} />
      ) : null}

      <div className="tp-observatory-grid">
        <StepTimeline
          replayState={replayState}
          selectedStepIndex={selectedStepIndex}
          onSelectStep={setSelectedStepIndex}
          liveProgress={selectedLiveProgress}
          coalescedEventCount={live?.coalescedEventCount ?? 0}
        />
        <PhoneReplayStage
          runId={run.id}
          replayState={replayState}
          selectedStepIndex={selectedStepIndex}
          screenshotMode={screenshotMode}
          onSelectStep={setSelectedStepIndex}
          onScreenshotModeChange={setScreenshotMode}
          liveProgress={selectedLiveProgress}
        />
        <div className="tp-observatory-side">
          <AgentConsole
            run={run}
            live={live}
            selectedOption={selectedOption ?? null}
            replayState={replayState}
            selectedStepIndex={selectedStepIndex}
            liveProgress={selectedLiveProgress}
          />
          <EvidenceDock
            runId={run.id}
            replayState={replayState}
            selectedStepIndex={selectedStepIndex}
            diagnostics={diagnostics}
          />
        </div>
      </div>
    </section>
  );
}

function liveProgressByKey(live: RunLiveState | null, key: string | null | undefined) {
  return key ? live?.liveEpisodes.get(key) ?? null : null;
}

function liveProgressForOption(
  live: RunLiveState | null,
  option: ReplayOption | null,
): LiveEpisodeProgress | null {
  if (!live || !option) {
    return null;
  }
  for (const progress of live.liveEpisodes.values()) {
    if (progress.episodeKey === option.episodeKey) {
      return progress;
    }
  }
  return null;
}

function liveOptionForProgress(
  options: ReplayOption[],
  progress: LiveEpisodeProgress | null,
) {
  if (!progress) {
    return null;
  }
  return options.find((option) => option.episodeKey === progress.episodeKey) ?? null;
}
