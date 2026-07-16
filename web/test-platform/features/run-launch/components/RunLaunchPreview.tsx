import type { RunLaunchPreview as RunLaunchPreviewData } from '../../../api/types';

export function RunLaunchPreview({
  preview,
  secretBindings,
  createDisabled,
  creating,
  onSecretBindingChange,
  onCreate,
}: {
  preview: RunLaunchPreviewData;
  secretBindings: Record<string, string>;
  createDisabled: boolean;
  creating: boolean;
  onSecretBindingChange: (slot: string, value: string) => void;
  onCreate: () => void;
}) {
  return (
    <section className="tp-panel" data-testid="tp-run-launch-preview">
      <div className="tp-run-heading">
        <div>
          <h2>Exact launch preview</h2>
          <p>{preview.episode_count} Prepared Episode / {preview.comparison_intent}</p>
        </div>
        <button type="button" onClick={onCreate} disabled={createDisabled}>
          {creating ? 'Creating...' : 'Create run'}
        </button>
      </div>
      <dl className="tp-run-facts">
        <div><dt>Workflow Version</dt><dd className="tp-mono">{preview.workflow_version_id}</dd></div>
        <div><dt>Workflow hash</dt><dd className="tp-mono">{preview.workflow_version_hash}</dd></div>
        <div><dt>Run Plan fingerprint</dt><dd className="tp-mono">{preview.run_plan_fingerprint}</dd></div>
        <div><dt>Preview token</dt><dd className="tp-mono">{preview.preview_token}</dd></div>
      </dl>
      {preview.constraint_violations.length > 0 ? (
        <div className="tp-inline-alert" role="alert">
          <h3>Target constraints are not satisfied</h3>
          <ul>
            {preview.constraint_violations.map((violation) => (
              <li key={`${violation.constraint}:${violation.code}`}>
                <code>{violation.code}</code> — {violation.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview.execution_profile_diff ? (
        <div>
          <h3>Execution Profile Revision diff</h3>
          <dl className="tp-run-facts">
            {preview.execution_profile_diff.changes.map((change) => (
              <div key={change.path}>
                <dt>{change.path}</dt>
                <dd>
                  <span>{formatDiffValue(change.before)}</span>
                  {' → '}
                  <span>{formatDiffValue(change.after)}</span>
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
      {preview.credential_requirements.length > 0 ? (
        <div className="tp-form-grid">
          <h3>Transient credentials</h3>
          {preview.credential_requirements.map((slot) => (
            <label key={slot}>
              <span>{credentialSlotLabel(slot)}</span>
              <input
                type="password"
                autoComplete="off"
                value={secretBindings[slot] ?? ''}
                onChange={(event) => onSecretBindingChange(slot, event.target.value)}
              />
            </label>
          ))}
          <p className="tp-kicker">
            Values are used only for this launch and are not stored by the browser.
          </p>
        </div>
      ) : null}
      {preview.lane_bindings.map((binding) => (
        <dl className="tp-run-facts" key={binding.lane_slot}>
          <div><dt>Lane Slot</dt><dd>{binding.lane_slot}</dd></div>
          <div><dt>Target Revision</dt><dd className="tp-mono">{binding.target_revision_id}</dd></div>
          <div><dt>Target hash</dt><dd className="tp-mono">{binding.target_revision_hash}</dd></div>
          <div><dt>Execution Profile Revision</dt><dd className="tp-mono">{binding.execution_profile_revision_id}</dd></div>
          <div><dt>Public spec hash</dt><dd className="tp-mono">{binding.execution_profile_public_hash}</dd></div>
          <div><dt>Lane fingerprint</dt><dd className="tp-mono">{binding.lane_fingerprint}</dd></div>
        </dl>
      ))}
    </section>
  );
}

function formatDiffValue(value: unknown): string {
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

function credentialSlotLabel(slot: string): string {
  return slot === 'model_api_key' ? 'Model API key' : slot;
}
