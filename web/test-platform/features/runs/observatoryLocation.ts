import { chooseDefaultReplayOption, type ReplayOption } from './episodeReplay';

export type EvidenceTab =
  | 'judge'
  | 'prompt'
  | 'response'
  | 'state'
  | 'diagnostics'
  | 'artifacts';

export type ScreenshotMode = 'annotated' | 'raw';

export type ObservatorySelection = {
  lane?: string | null;
  episode?: string | null;
  attempt?: number | 'latest' | null;
  step?: number | null;
  screenshot?: ScreenshotMode | null;
  evidence?: EvidenceTab | null;
};

export type ResolvedObservatoryLocation = {
  option: ReplayOption | null;
  hadSelectionRequest: boolean;
  requestedStep: number | null;
  screenshotMode: ScreenshotMode;
  evidenceTab: EvidenceTab;
  notice: string | null;
};

const EVIDENCE_TAB_IDS = new Set<EvidenceTab>([
  'judge',
  'prompt',
  'response',
  'state',
  'diagnostics',
  'artifacts',
]);

export function resolveObservatoryLocation(
  params: URLSearchParams,
  options: ReplayOption[],
  defaultOption: ReplayOption | null,
): ResolvedObservatoryLocation {
  const requestedLane = params.get('lane');
  const requestedEpisode = params.get('episode');
  const requestedAttempt = params.get('attempt');
  const hadSelectionRequest = Boolean(
    requestedLane || requestedEpisode || requestedAttempt,
  );
  const matches = hadSelectionRequest
    ? options.filter((option) => (
      (!requestedLane || option.laneKey === requestedLane)
      && (!requestedEpisode || option.episodeKey === requestedEpisode)
      && (!requestedAttempt || String(option.attemptNo) === requestedAttempt)
    ))
    : [];
  const requestedOption = chooseDefaultReplayOption(matches);

  const screenshotParam = params.get('screenshot');
  const screenshotMode: ScreenshotMode =
    screenshotParam === 'raw' || screenshotParam === 'annotated'
      ? screenshotParam
      : 'annotated';
  const evidenceParam = params.get('evidence');
  const evidenceTab = evidenceParam && EVIDENCE_TAB_IDS.has(evidenceParam as EvidenceTab)
    ? evidenceParam as EvidenceTab
    : 'judge';

  const stepParam = params.get('step');
  const parsedStep = stepParam === null ? null : Number(stepParam);
  const requestedStep = parsedStep !== null && Number.isInteger(parsedStep) && parsedStep > 0
    ? parsedStep
    : null;

  const notices: string[] = [];
  if (hadSelectionRequest && !requestedOption) {
    notices.push('Requested replay selection is no longer available; showing the default evidence.');
  }
  if (screenshotParam && screenshotParam !== 'raw' && screenshotParam !== 'annotated') {
    notices.push('Requested screenshot mode is invalid; showing annotated evidence.');
  }
  if (evidenceParam && !EVIDENCE_TAB_IDS.has(evidenceParam as EvidenceTab)) {
    notices.push('Requested evidence tab is invalid; showing Judge evidence.');
  }
  if (stepParam !== null && requestedStep === null) {
    notices.push('Requested replay step is invalid; showing the final step.');
  }

  return {
    option: requestedOption ?? defaultOption,
    hadSelectionRequest,
    requestedStep,
    screenshotMode,
    evidenceTab,
    notice: notices.length > 0 ? notices.join(' ') : null,
  };
}

export function mergeObservatorySearchParams(
  current: URLSearchParams,
  values: ObservatorySelection,
) {
  const next = new URLSearchParams(current);
  setParam(next, 'lane', values.lane);
  setParam(next, 'episode', values.episode);
  setParam(next, 'attempt', values.attempt);
  setParam(next, 'step', values.step);
  setParam(next, 'screenshot', values.screenshot);
  setParam(next, 'evidence', values.evidence);
  return next;
}

export function observatoryPath(runId: string, selection: ObservatorySelection) {
  const route = observatoryRoute(runId, selection);
  return `/test-platform${route.pathname}${route.search}`;
}

export function observatoryRoute(runId: string, selection: ObservatorySelection) {
  const params = mergeObservatorySearchParams(new URLSearchParams(), selection);
  const search = params.toString();
  return {
    pathname: `/runs/${encodeURIComponent(runId)}`,
    search: search ? `?${search}` : '',
  };
}

export function appendLocationNotice(current: string | null, next: string) {
  if (!current) return next;
  return current.includes(next) ? current : `${current} ${next}`;
}

function setParam(
  params: URLSearchParams,
  key: keyof ObservatorySelection,
  value: string | number | null | undefined,
) {
  if (value === undefined) return;
  if (value === null) params.delete(key);
  else params.set(key, String(value));
}
