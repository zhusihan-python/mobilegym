import type { RunDetail } from '../../api/types';
import { formatPrettyJson } from './episodeReplay';

export function RunSettingsDrawer({
  run,
  onClose,
}: {
  run: RunDetail;
  onClose: () => void;
}) {
  return (
    <section className="tp-settings-drawer" aria-label="Run settings">
      <div className="tp-settings-header">
        <div>
          <span className="tp-kicker">Settings</span>
          <h3>Frozen run configuration</h3>
        </div>
        <button type="button" onClick={onClose}>Close</button>
      </div>
      <dl className="tp-settings-facts">
        <div>
          <dt>Run state</dt>
          <dd>{run.state}</dd>
        </div>
        <div>
          <dt>Execution identity</dt>
          <dd>{run.execution_identity.label}</dd>
        </div>
        <div>
          <dt>Fingerprint</dt>
          <dd className="tp-mono">{run.fingerprint}</dd>
        </div>
        <div>
          <dt>Lanes</dt>
          <dd>{run.lanes.map((lane) => lane.lane_key).join(', ') || 'n/a'}</dd>
        </div>
        <div>
          <dt>Planned episodes</dt>
          <dd>{run.progress.planned_episodes}</dd>
        </div>
      </dl>
      <pre className="tp-settings-json" data-testid="tp-run-settings-json">
        {formatPrettyJson(redactSensitive(run.run_plan))}
      </pre>
    </section>
  );
}

function redactSensitive(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((item) => redactSensitive(item));
  }
  if (!value || typeof value !== 'object') {
    return value;
  }
  const result: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    result[key] = isSensitiveKey(key) ? '[redacted]' : redactSensitive(child);
  }
  return result;
}

function isSensitiveKey(key: string) {
  return /(api[_-]?key|token|secret|password|authorization|bearer)/i.test(key);
}
