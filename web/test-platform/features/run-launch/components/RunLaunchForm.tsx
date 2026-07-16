import type { ExecutionProfile, Target, WorkflowSummary } from '../../../api/types';
import type { RunLaunchSelection } from '../model';

export function RunLaunchForm({
  workflows,
  targets,
  profiles,
  selection,
  disabled,
  previewing,
  ready,
  onChange,
  onWorkflowChange,
  onPreview,
}: {
  workflows: WorkflowSummary[];
  targets: Target[];
  profiles: ExecutionProfile[];
  selection: RunLaunchSelection;
  disabled: boolean;
  previewing: boolean;
  ready: boolean;
  onChange: (patch: Partial<RunLaunchSelection>) => void;
  onWorkflowChange: (workflowVersionId: string) => void;
  onPreview: () => void;
}) {
  const targetComparison = selection.comparisonIntent === 'target_comparison';
  const executionComparison = selection.comparisonIntent === 'execution_comparison';
  const paired = targetComparison || executionComparison;
  const profileOptions = profiles.flatMap((profile) => (
    profile.head_revision ? [{ profile, revision: profile.head_revision }] : []
  ));
  return (
    <section className="tp-panel">
      <h2>Run Launch</h2>
      <p>Bind every Lane Slot to exact reviewed revisions.</p>
      <div className="tp-form-grid">
        <label>
          <span>Workflow Version</span>
          <select
            value={selection.workflowVersionId}
            onChange={(event) => onWorkflowChange(event.target.value)}
          >
            {workflows.map((workflow) => (
              <option key={workflow.latest_version!.id} value={workflow.latest_version!.id}>
                {workflow.name} / v{workflow.latest_version!.version_no} / {workflow.latest_version!.id}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Comparison intent</span>
          <select
            aria-label="Comparison intent"
            value={selection.comparisonIntent}
            disabled={!paired}
            onChange={(event) => {
              const comparisonIntent = event.target.value as RunLaunchSelection['comparisonIntent'];
              if (comparisonIntent === 'execution_comparison') {
                const baselineProfileRevisionId = selection.profileRevisionId;
                const candidateProfileRevisionId = profileOptions.find(
                  ({ revision }) => revision.id !== baselineProfileRevisionId,
                )?.revision.id ?? baselineProfileRevisionId;
                onChange({
                  comparisonIntent,
                  baselineProfileRevisionId,
                  profileRevisionId: candidateProfileRevisionId,
                });
              } else {
                onChange({ comparisonIntent });
              }
            }}
          >
            {paired ? (
              <>
                <option value="target_comparison">Target Comparison</option>
                <option value="execution_comparison">Execution Comparison</option>
              </>
            ) : (
              <option value="single">Single</option>
            )}
          </select>
        </label>
        {targetComparison ? (
          <label>
            <span>Baseline Target Revision</span>
            <select
              aria-label="Baseline Target Revision"
              value={selection.baselineTargetRevisionId}
              onChange={(event) => onChange({ baselineTargetRevisionId: event.target.value })}
            >
              {targets.map((target) => (
                <option key={target.latest_revision!.id} value={target.latest_revision!.id}>
                  {target.name} / {target.latest_revision!.id}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label>
          <span>{executionComparison
            ? 'Shared Target Revision'
            : targetComparison
              ? 'Candidate Target Revision'
              : 'Target Revision'}</span>
          <select
            aria-label={executionComparison
              ? 'Shared Target Revision'
              : targetComparison
                ? 'Candidate Target Revision'
                : 'Target Revision'}
            value={selection.candidateTargetRevisionId}
            onChange={(event) => onChange({ candidateTargetRevisionId: event.target.value })}
          >
            {targets.map((target) => (
              <option key={target.latest_revision!.id} value={target.latest_revision!.id}>
                {target.name} / {target.latest_revision!.id}
              </option>
            ))}
          </select>
        </label>
        {executionComparison ? (
          <>
            <ProfileRevisionSelect
              label="Baseline Execution Profile Revision"
              profiles={profiles}
              value={selection.baselineProfileRevisionId}
              onChange={(baselineProfileRevisionId) => onChange({ baselineProfileRevisionId })}
            />
            <ProfileRevisionSelect
              label="Candidate Execution Profile Revision"
              profiles={profiles}
              value={selection.profileRevisionId}
              onChange={(profileRevisionId) => onChange({ profileRevisionId })}
            />
          </>
        ) : (
          <ProfileRevisionSelect
            label={targetComparison
              ? 'Shared Execution Profile Revision'
              : 'Execution Profile Revision'}
            profiles={profiles}
            value={selection.profileRevisionId}
            onChange={(profileRevisionId) => onChange({ profileRevisionId })}
          />
        )}
        <label>
          <span>Run name</span>
          <input
            value={selection.name}
            onChange={(event) => onChange({ name: event.target.value })}
          />
        </label>
        <label>
          <span>Seed</span>
          <input
            type="number"
            value={selection.seed}
            onChange={(event) => onChange({ seed: event.target.value })}
          />
        </label>
        <button type="button" onClick={onPreview} disabled={!ready || disabled}>
          {previewing ? 'Previewing...' : 'Preview launch'}
        </button>
      </div>
      {!ready ? (
        <p className="tp-kicker">
          A published Workflow v2, healthy Target Revision, and published Execution Profile Revision are required.
        </p>
      ) : null}
    </section>
  );
}

function ProfileRevisionSelect({
  label,
  profiles,
  value,
  onChange,
}: {
  label: string;
  profiles: ExecutionProfile[];
  value: string;
  onChange: (revisionId: string) => void;
}) {
  return (
    <label>
      <span>{label}</span>
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {profiles.map((profile) => (
          <option key={profile.head_revision!.id} value={profile.head_revision!.id}>
            {profile.name} / revision {profile.head_revision!.revision_no} / {profile.head_revision!.id}
          </option>
        ))}
      </select>
    </label>
  );
}
