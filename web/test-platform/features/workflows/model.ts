import type { WorkflowDefinition } from '../../api/types';

export const MANUAL_SEQUENCE_STATE_POLICY = 'isolated';
export const MANUAL_SEQUENCE_FAILURE_POLICY = 'continue';
export const TARGET_COMPARISON_CONSTRAINTS = ['same_app', 'same_device', 'same_data'];

export type WorkflowMode = 'batch' | 'manual_sequence';

export type PairedConfig = {
  targetConstraints: string[];
  initialStatePolicy: string;
  execution: string;
};

export type BuildWorkflowDefinitionInput = {
  workflowMode: WorkflowMode;
  selectedTaskIds: string[];
  targetId: string;
  repeatCount: number;
  parallelCount: number;
  processCount: number;
  paired: PairedConfig | null;
};

export function canSubmitWorkflow(
  selectedTaskIds: string[],
  targetId: string,
  pairedEnabled = false,
) {
  if (pairedEnabled) return selectedTaskIds.length > 0;
  return selectedTaskIds.length > 0 && Boolean(targetId);
}

export function workflowDefinitionsEqual(
  left: WorkflowDefinition | null,
  right: WorkflowDefinition,
) {
  return left ? JSON.stringify(left) === JSON.stringify(right) : false;
}

export function buildWorkflowDefinition(
  input: BuildWorkflowDefinitionInput,
): WorkflowDefinition {
  const {
    workflowMode,
    selectedTaskIds,
    targetId,
    repeatCount,
    parallelCount,
    processCount,
    paired,
  } = input;
  const isManualSequence = workflowMode === 'manual_sequence';
  const effectivePaired = isManualSequence ? null : paired;
  const effectiveRepeatCount = isManualSequence ? 1 : repeatCount;
  const effectiveParallelCount = isManualSequence ? 1 : parallelCount;
  const effectiveProcessCount = isManualSequence ? 1 : processCount;
  const laneConfig = effectivePaired
    ? {
        lane_slots: {
          baseline: { role: 'baseline' },
          candidate: { role: 'candidate' },
        },
      }
    : { lanes: { candidate: { target_id: targetId } } };
  const taskSelectionConfig: Record<string, unknown> = {
    task_ids: selectedTaskIds,
    sample_n: 1,
  };
  if (isManualSequence) {
    taskSelectionConfig.order_policy = 'manual';
  }
  const executeConfig: Record<string, unknown> = {
    parallel: Math.max(1, effectiveParallelCount),
    processes: Math.max(1, effectiveProcessCount),
  };
  if (isManualSequence) {
    executeConfig.execution_strategy = 'linear_sequence';
    executeConfig.state_policy = MANUAL_SEQUENCE_STATE_POLICY;
    executeConfig.failure_policy = MANUAL_SEQUENCE_FAILURE_POLICY;
  }

  const nodes: WorkflowDefinition['nodes'] = [
    {
      id: 'tasks',
      type: 'task_selection',
      depends_on: [],
      config: taskSelectionConfig,
    },
    {
      id: 'matrix',
      type: 'matrix',
      depends_on: ['tasks'],
      config: {
        ...laneConfig,
        repeat_n: effectiveRepeatCount,
      },
    },
    {
      id: 'execute',
      type: 'execute',
      depends_on: ['matrix'],
      config: executeConfig,
    },
  ];

  if (effectivePaired) {
    nodes.push({
      id: 'compare',
      type: 'compare',
      depends_on: ['execute'],
      config: {
        target_constraints: effectivePaired.targetConstraints,
        initial_state_policy: effectivePaired.initialStatePolicy,
        execution: effectivePaired.execution,
      },
    });
  }

  return {
    schema_version: effectivePaired ? 2 : 1,
    name: 'WeChat smoke',
    nodes,
  };
}
