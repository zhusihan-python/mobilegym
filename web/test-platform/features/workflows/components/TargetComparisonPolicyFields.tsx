import {
  TARGET_COMPARISON_CONSTRAINTS,
  type PairedConfig,
} from '../model';

export function TargetComparisonPolicyFields({
  enabled,
  disabled,
  policy,
  onEnabledChange,
  onPolicyChange,
}: {
  enabled: boolean;
  disabled: boolean;
  policy: PairedConfig;
  onEnabledChange: (enabled: boolean) => void;
  onPolicyChange: (patch: Partial<PairedConfig>) => void;
}) {
  return (
    <div className="tp-workflow-paired">
      <label className="tp-checkbox-row">
        <input
          type="checkbox"
          checked={enabled}
          disabled={disabled}
          onChange={(event) => onEnabledChange(event.target.checked)}
          aria-label="Paired comparison (baseline vs candidate)"
        />
        <span>Paired comparison (baseline vs candidate)</span>
      </label>

      {enabled ? (
        <div className="tp-workflow-paired-controls">
          <p className="tp-kicker">
            Exact baseline and candidate Target Revisions are selected in Run Launch.
          </p>

          <fieldset className="tp-workflow-constraints">
            <legend>Target constraints</legend>
            {TARGET_COMPARISON_CONSTRAINTS.map((axis) => (
              <label key={axis} className="tp-checkbox-row">
                <input
                  type="checkbox"
                  checked={policy.targetConstraints.includes(axis)}
                  onChange={(event) => {
                    onPolicyChange({
                      targetConstraints: event.target.checked
                        ? [...policy.targetConstraints, axis]
                        : policy.targetConstraints.filter((item) => item !== axis),
                    });
                  }}
                  aria-label={`Constraint ${axis}`}
                />
                <span>{axis}</span>
              </label>
            ))}
          </fieldset>

          <label htmlFor="tp-workflow-initial-state-policy">Initial state policy</label>
          <select
            id="tp-workflow-initial-state-policy"
            value={policy.initialStatePolicy}
            onChange={(event) => onPolicyChange({ initialStatePolicy: event.target.value })}
          >
            <option value="task_projection">task_projection</option>
            <option value="strict_snapshot">strict_snapshot</option>
          </select>

          <label htmlFor="tp-workflow-execution">Execution</label>
          <select
            id="tp-workflow-execution"
            value={policy.execution}
            onChange={(event) => onPolicyChange({ execution: event.target.value })}
          >
            <option value="serial">serial</option>
            <option value="parallel">parallel</option>
          </select>
        </div>
      ) : null}
    </div>
  );
}
