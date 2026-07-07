import { useState } from 'react';

import type { EpisodeReplay, EpisodeReplayStep } from '../../api/types';
import {
  artifactContentHref,
  formatPrettyJson,
  isAnswerCompletionAccepted,
  replayFromState,
  stepFromReplayState,
  terminalMarkerForReplay,
  type ReplayLoadState,
} from './episodeReplay';

type EvidenceTab = 'judge' | 'prompt' | 'response' | 'state' | 'artifacts';

const EVIDENCE_TABS: Array<{ id: EvidenceTab; label: string }> = [
  { id: 'judge', label: 'Judge' },
  { id: 'prompt', label: 'Prompt' },
  { id: 'response', label: 'Response' },
  { id: 'state', label: 'State/route' },
  { id: 'artifacts', label: 'Artifacts' },
];

export function EvidenceDock({
  runId,
  replayState,
  selectedStepIndex,
}: {
  runId: string;
  replayState: ReplayLoadState;
  selectedStepIndex: number;
}) {
  const [activeTab, setActiveTab] = useState<EvidenceTab>('judge');
  const replay = replayFromState(replayState);
  const selectedStep = stepFromReplayState(replayState, selectedStepIndex);

  return (
    <aside className="tp-evidence-dock" aria-label="Evidence dock">
      <div className="tp-dock-header">
        <span className="tp-kicker">Evidence</span>
        <strong>{selectedStep ? `Step ${selectedStep.step ?? selectedStepIndex + 1}` : 'No step'}</strong>
      </div>
      <div className="tp-evidence-tabs" role="tablist" aria-label="Evidence tabs">
        {EVIDENCE_TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="tp-evidence-body" role="tabpanel">
        {replay ? (
          <EvidencePanel
            activeTab={activeTab}
            runId={runId}
            replay={replay}
            selectedStep={selectedStep}
          />
        ) : (
          <p className="tp-dock-empty">{emptyEvidenceMessage(replayState)}</p>
        )}
      </div>
    </aside>
  );
}

function EvidencePanel({
  activeTab,
  runId,
  replay,
  selectedStep,
}: {
  activeTab: EvidenceTab;
  runId: string;
  replay: EpisodeReplay;
  selectedStep: EpisodeReplayStep | null;
}) {
  if (activeTab === 'judge') {
    return <JudgeEvidence replay={replay} />;
  }
  if (activeTab === 'prompt') {
    return (
      <ArtifactEvidence
        runId={runId}
        label="Open prompt artifact"
        artifactId={selectedStep?.model_prompt_artifact_id ?? null}
        emptyMessage="No prompt artifact for this step."
      />
    );
  }
  if (activeTab === 'response') {
    return (
      <ArtifactEvidence
        runId={runId}
        label="Open response artifact"
        artifactId={selectedStep?.model_response_artifact_id ?? null}
        emptyMessage="No response artifact for this step."
      />
    );
  }
  if (activeTab === 'state') {
    return <StateEvidence selectedStep={selectedStep} />;
  }
  return <ArtifactList runId={runId} selectedStep={selectedStep} />;
}

function JudgeEvidence({ replay }: { replay: EpisodeReplay }) {
  return (
    <div className="tp-evidence-section">
      <dl className="tp-evidence-facts">
        <div>
          <dt>Outcome</dt>
          <dd>{replay.outcome ?? 'pending'}</dd>
        </div>
        <div>
          <dt>Error</dt>
          <dd>{replay.error_code ?? 'n/a'}</dd>
        </div>
        <div>
          <dt>Terminal</dt>
          <dd>{terminalMarkerForReplay(replay) ?? 'n/a'}</dd>
        </div>
      </dl>
      {isAnswerCompletionAccepted(replay) ? (
        <span className="tp-compat-badge" data-testid="tp-answer-completion-badge">
          answer_completion_accepted
        </span>
      ) : null}
      <pre className="tp-evidence-json" data-testid="tp-judge-result-json">
        {formatPrettyJson(replay.result)}
      </pre>
    </div>
  );
}

function ArtifactEvidence({
  runId,
  label,
  artifactId,
  emptyMessage,
}: {
  runId: string;
  label: string;
  artifactId: string | null;
  emptyMessage: string;
}) {
  if (!artifactId) {
    return <p className="tp-dock-empty">{emptyMessage}</p>;
  }
  return (
    <a className="tp-evidence-link" href={artifactContentHref(runId, artifactId)}>
      {label}
    </a>
  );
}

function StateEvidence({ selectedStep }: { selectedStep: EpisodeReplayStep | null }) {
  if (!selectedStep) {
    return <p className="tp-dock-empty">No step selected.</p>;
  }
  return (
    <div className="tp-evidence-section">
      <h3>Route</h3>
      <pre className="tp-evidence-json">{formatPrettyJson(selectedStep.route)}</pre>
      <h3>Action data</h3>
      <pre className="tp-evidence-json">{formatPrettyJson(selectedStep.action_data)}</pre>
      {selectedStep.thought ? (
        <>
          <h3>Thought</h3>
          <p>{selectedStep.thought}</p>
        </>
      ) : null}
      {selectedStep.explain ? (
        <>
          <h3>Explain</h3>
          <p>{selectedStep.explain}</p>
        </>
      ) : null}
      {selectedStep.summary ? (
        <>
          <h3>Summary</h3>
          <p>{selectedStep.summary}</p>
        </>
      ) : null}
    </div>
  );
}

function ArtifactList({
  runId,
  selectedStep,
}: {
  runId: string;
  selectedStep: EpisodeReplayStep | null;
}) {
  const artifacts = [
    ['Raw screenshot', selectedStep?.screenshot_artifact_id ?? null],
    ['Annotated screenshot', selectedStep?.screenshot_annotated_artifact_id ?? null],
    ['Prompt', selectedStep?.model_prompt_artifact_id ?? null],
    ['Response', selectedStep?.model_response_artifact_id ?? null],
  ] as const;

  if (!selectedStep) {
    return <p className="tp-dock-empty">No step selected.</p>;
  }

  return (
    <ul className="tp-evidence-artifacts">
      {artifacts.map(([label, artifactId]) => (
        <li key={label}>
          <span>{label}</span>
          {artifactId ? (
            <a href={artifactContentHref(runId, artifactId)}>Open</a>
          ) : (
            <em>missing</em>
          )}
        </li>
      ))}
    </ul>
  );
}

function emptyEvidenceMessage(replayState: ReplayLoadState) {
  if (replayState.status === 'loading') {
    return 'Loading evidence...';
  }
  if (replayState.status === 'empty' || replayState.status === 'error') {
    return replayState.message;
  }
  return 'Evidence will appear after replay loads.';
}
