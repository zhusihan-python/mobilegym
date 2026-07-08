import type { ReplayOption } from './episodeReplay';

export function EpisodePicker({
  options,
  selectedId,
  onSelect,
}: {
  options: ReplayOption[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  if (options.length === 0) {
    return (
      <div className="tp-episode-picker" data-testid="tp-episode-picker-empty">
        <span>No replayable episodes yet</span>
      </div>
    );
  }

  const selected = options.find((option) => option.id === selectedId) ?? options[0];

  return (
    <label className="tp-episode-picker">
      <span>Episode</span>
      <select
        aria-label="Replay episode"
        value={selected.id}
        onChange={(event) => onSelect(event.currentTarget.value)}
      >
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {episodeOptionLabel(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function episodeOptionLabel(option: ReplayOption) {
  const outcome = option.outcome ?? 'planned';
  const taskLabel =
    typeof option.sequenceIndex === 'number'
      ? `Step ${option.sequenceIndex + 1}: ${option.taskId}`
      : option.taskId;
  return `${taskLabel} | ${option.laneKey} | attempt ${option.attemptNo} | ${outcome}`;
}
