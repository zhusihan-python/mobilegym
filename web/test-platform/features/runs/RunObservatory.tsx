import { useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { getEpisodeReplay } from '../../api/client';
import type { RunDetail } from '../../api/types';
import { AgentConsole } from './AgentConsole';
import { EpisodePicker } from './EpisodePicker';
import {
  EvidenceDock,
  type EvidenceDiagnosticsState,
} from './EvidenceDock';
import { PhoneReplayStage } from './PhoneReplayStage';
import { RunSettingsDrawer } from './RunSettingsDrawer';
import { StepTimeline } from './StepTimeline';
import {
  buildReplayOptions,
  chooseDefaultReplayOption,
  type ReplayLoadState,
  type ReplayOption,
} from './episodeReplay';
import {
  appendLocationNotice,
  mergeObservatorySearchParams,
  observatoryPath,
  resolveObservatoryLocation,
  type EvidenceTab,
  type ObservatorySelection,
  type ScreenshotMode,
} from './observatoryLocation';
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
  const [searchParams, setSearchParams] = useSearchParams();
  const searchKey = searchParams.toString();
  const options = useMemo(() => buildReplayOptions(run), [run]);
  const defaultOption = useMemo(() => chooseDefaultReplayOption(options), [options]);
  const resolvedLocation = useMemo(
    () => resolveObservatoryLocation(new URLSearchParams(searchKey), options, defaultOption),
    [defaultOption, options, searchKey],
  );
  const requestedStepRef = useRef(resolvedLocation.requestedStep);
  requestedStepRef.current = resolvedLocation.requestedStep;
  const [selectedId, setSelectedId] = useState<string | null>(
    resolvedLocation.option?.id ?? null,
  );
  const [selectionTouched, setSelectionTouched] = useState(
    resolvedLocation.hadSelectionRequest,
  );
  const [replayState, setReplayState] = useState<ReplayLoadState>({ status: 'idle' });
  const [selectedStepIndex, setSelectedStepIndex] = useState(0);
  const [stepNotice, setStepNotice] = useState<string | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const screenshotMode = resolvedLocation.screenshotMode;
  const activeEvidenceTab = resolvedLocation.evidenceTab;
  const followsLive = run.state === 'running' || run.state === 'queued';

  useEffect(() => {
    setSelectionTouched(resolvedLocation.hadSelectionRequest);
    setSelectedId(resolvedLocation.option?.id ?? null);
    setStepNotice(null);
  }, [resolvedLocation.hadSelectionRequest, resolvedLocation.option?.id, searchKey]);

  useEffect(() => {
    setSelectedId((current) => {
      if (!selectionTouched) {
        return defaultOption?.id ?? null;
      }
      if (current && options.some((option) => option.id === current)) {
        return current;
      }
      return defaultOption?.id ?? null;
    });
  }, [defaultOption?.id, options, selectionTouched]);

  const activeLiveProgress = followsLive
    ? liveProgressByKey(live, live?.activeLiveEpisodeKey)
      ?? liveProgressByKey(live, live?.latestLiveEpisodeKey)
    : null;
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
  const incidentAttemptNo = stableIncidentAttemptNo(selectedOption, replayState);

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
        const replayStep = resolveReplayStep(replay.steps, requestedStepRef.current);
        setSelectedStepIndex(replayStep.index);
        setStepNotice(replayStep.notice);
        setReplayState({ status: 'loaded', replay });
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

  useEffect(() => {
    if (replayState.status !== 'loaded') return;
    const replayStep = resolveReplayStep(
      replayState.replay.steps,
      resolvedLocation.requestedStep,
    );
    setSelectedStepIndex(replayStep.index);
    setStepNotice(replayStep.notice);
  }, [replayState, resolvedLocation.requestedStep]);

  const updateObservatoryLocation = (values: ObservatorySelection) => {
    setSearchParams(
      (current) => mergeObservatorySearchParams(current, values),
      { replace: true },
    );
    setCopyMessage(null);
  };

  const selectEpisode = (id: string) => {
    setSelectionTouched(true);
    setSelectedId(id);
    const option = options.find((candidate) => candidate.id === id);
    if (option) {
      updateObservatoryLocation({
        lane: option.laneKey,
        episode: option.episodeKey,
        attempt: option.attemptNo,
        step: null,
        screenshot: screenshotMode,
        evidence: activeEvidenceTab,
      });
    }
  };

  const selectStep = (index: number) => {
    setSelectedStepIndex(index);
    if (replayState.status === 'loaded') {
      const step = replayState.replay.steps[index];
      if (step && selectedOption) {
        updateObservatoryLocation({
          lane: selectedOption.laneKey,
          episode: selectedOption.episodeKey,
          attempt: selectedOption.attemptNo,
          step: step.step ?? index + 1,
          screenshot: screenshotMode,
          evidence: activeEvidenceTab,
        });
      }
    }
  };

  const selectScreenshotMode = (mode: ScreenshotMode) => {
    updateObservatoryLocation({
      ...selectionForOption(selectedOption),
      step: selectedReplayStep(replayState, selectedStepIndex),
      screenshot: mode,
      evidence: activeEvidenceTab,
    });
  };

  const selectEvidenceTab = (tab: EvidenceTab) => {
    updateObservatoryLocation({
      ...selectionForOption(selectedOption),
      step: selectedReplayStep(replayState, selectedStepIndex),
      screenshot: screenshotMode,
      evidence: tab,
    });
  };

  const copyIncidentLink = async () => {
    if (!selectedOption || incidentAttemptNo === null) return;
    const clipboard = window.navigator.clipboard;
    if (!clipboard?.writeText) {
      setCopyMessage('Clipboard access is unavailable.');
      return;
    }
    const url = new URL(observatoryPath(run.id, {
      lane: selectedOption.laneKey,
      episode: selectedOption.episodeKey,
      attempt: incidentAttemptNo,
      step: selectedReplayStep(replayState, selectedStepIndex),
      screenshot: screenshotMode,
      evidence: activeEvidenceTab,
    }), window.location.origin);
    try {
      await clipboard.writeText(url.toString());
      setCopyMessage('Incident link copied.');
    } catch {
      setCopyMessage('Unable to copy the incident link.');
    }
  };

  const locationNotice = stepNotice
    ? appendLocationNotice(resolvedLocation.notice, stepNotice)
    : resolvedLocation.notice;

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
          <button
            type="button"
            onClick={copyIncidentLink}
            disabled={!selectedOption || incidentAttemptNo === null}
          >
            Copy incident link
          </button>
          <button type="button" onClick={() => setSettingsOpen((open) => !open)}>
            Settings
          </button>
        </div>
      </div>
      {settingsOpen ? (
        <RunSettingsDrawer run={run} onClose={() => setSettingsOpen(false)} />
      ) : null}
      {locationNotice ? (
        <p className="tp-alert" data-testid="tp-observatory-location-notice">
          {locationNotice}
        </p>
      ) : null}
      {copyMessage ? <p className="tp-kicker">{copyMessage}</p> : null}

      <div className="tp-observatory-grid">
        <StepTimeline
          replayState={replayState}
          selectedStepIndex={selectedStepIndex}
          onSelectStep={selectStep}
          liveProgress={selectedLiveProgress}
          coalescedEventCount={live?.coalescedEventCount ?? 0}
        />
        <PhoneReplayStage
          runId={run.id}
          replayState={replayState}
          selectedStepIndex={selectedStepIndex}
          screenshotMode={screenshotMode}
          onSelectStep={selectStep}
          onScreenshotModeChange={selectScreenshotMode}
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
            activeTab={activeEvidenceTab}
            onActiveTabChange={selectEvidenceTab}
          />
        </div>
      </div>
    </section>
  );
}

function selectionForOption(option: ReplayOption | null): ObservatorySelection {
  if (!option) return {};
  return {
    lane: option.laneKey,
    episode: option.episodeKey,
    attempt: option.attemptNo,
  };
}

function selectedReplayStep(state: ReplayLoadState, index: number) {
  if (state.status !== 'loaded') return null;
  const step = state.replay.steps[index];
  return step ? step.step ?? index + 1 : null;
}

function stableIncidentAttemptNo(
  option: ReplayOption | null,
  state: ReplayLoadState,
): number | null {
  if (!option) return null;
  if (typeof option.attemptNo === 'number') return option.attemptNo;
  if (
    state.status === 'loaded'
    && state.replay.lane_key === option.laneKey
    && state.replay.episode_key === option.episodeKey
  ) {
    return state.replay.attempt_no;
  }
  return null;
}

function resolveReplayStep(
  steps: Array<{ step: number | null }>,
  requestedStep: number | null,
) {
  const requestedIndex = requestedStep === null
    ? -1
    : steps.findIndex((step, index) => (step.step ?? index + 1) === requestedStep);
  return {
    index: requestedIndex >= 0 ? requestedIndex : Math.max(0, steps.length - 1),
    notice: requestedStep !== null && requestedIndex < 0
      ? `Requested replay step ${requestedStep} is no longer available; showing the final step.`
      : null,
  };
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
