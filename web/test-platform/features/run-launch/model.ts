import type {
  ExecutionProfile,
  RunLaunchCommand,
  Target,
  WorkflowDefinition,
  WorkflowSummary,
} from '../../api/types';

export type ComparisonIntent = 'single' | 'target_comparison' | 'execution_comparison';

export type RunLaunchSelection = {
  workflowVersionId: string;
  comparisonIntent: ComparisonIntent;
  baselineTargetRevisionId: string;
  candidateTargetRevisionId: string;
  baselineProfileRevisionId: string;
  profileRevisionId: string;
  name: string;
  seed: string;
};

export function comparisonIntentForDefinition(
  definition: WorkflowDefinition | undefined,
): ComparisonIntent {
  const matrix = definition?.nodes.find((node) => node.type === 'matrix');
  const laneSlots = matrix?.config.lane_slots;
  if (
    laneSlots
    && typeof laneSlots === 'object'
    && !Array.isArray(laneSlots)
    && Object.hasOwn(laneSlots, 'baseline')
    && Object.hasOwn(laneSlots, 'candidate')
  ) {
    return 'target_comparison';
  }
  return 'single';
}

export function initialRunLaunchSelection(input: {
  workflows: WorkflowSummary[];
  targets: Target[];
  profiles: ExecutionProfile[];
}): RunLaunchSelection {
  const workflowVersionId = input.workflows[0]?.latest_version?.id ?? '';
  return {
    ...selectionForWorkflow(workflowVersionId, input.workflows, input.targets),
    baselineProfileRevisionId: input.profiles[0]?.head_revision?.id ?? '',
    profileRevisionId: input.profiles[0]?.head_revision?.id ?? '',
    seed: '20260715',
  };
}

export function selectionForWorkflow(
  workflowVersionId: string,
  workflows: WorkflowSummary[],
  targets: Target[],
): Pick<
  RunLaunchSelection,
  | 'workflowVersionId'
  | 'comparisonIntent'
  | 'baselineTargetRevisionId'
  | 'candidateTargetRevisionId'
  | 'name'
> {
  const workflow = workflows.find(
    (item) => item.latest_version?.id === workflowVersionId,
  );
  const comparisonIntent = comparisonIntentForDefinition(
    workflow?.latest_version?.definition,
  );
  const firstTargetRevisionId = targets[0]?.latest_revision?.id ?? '';
  const secondTargetRevisionId = targets[1]?.latest_revision?.id ?? firstTargetRevisionId;
  return {
    workflowVersionId,
    comparisonIntent,
    baselineTargetRevisionId: firstTargetRevisionId,
    candidateTargetRevisionId: comparisonIntent === 'target_comparison'
      ? secondTargetRevisionId
      : firstTargetRevisionId,
    name: workflow?.latest_version?.definition.name ?? '',
  };
}

export function buildRunLaunchCommand(
  projectId: string,
  selection: RunLaunchSelection,
): RunLaunchCommand {
  const candidate = {
    lane_slot: 'candidate',
    target_revision_id: selection.candidateTargetRevisionId,
    execution_profile_revision_id: selection.profileRevisionId,
  };
  return {
    project_id: projectId,
    workflow_version_id: selection.workflowVersionId,
    name: selection.name.trim() || undefined,
    seed: Number(selection.seed),
    comparison_intent: selection.comparisonIntent,
    lane_bindings: selection.comparisonIntent !== 'single'
      ? [
        {
          lane_slot: 'baseline',
          target_revision_id: selection.comparisonIntent === 'execution_comparison'
            ? selection.candidateTargetRevisionId
            : selection.baselineTargetRevisionId,
          execution_profile_revision_id: selection.comparisonIntent === 'execution_comparison'
            ? selection.baselineProfileRevisionId
            : selection.profileRevisionId,
        },
        candidate,
      ]
      : [candidate],
  };
}

export function isRunLaunchReady(selection: RunLaunchSelection): boolean {
  return Boolean(
    selection.workflowVersionId
    && selection.candidateTargetRevisionId
    && (
      selection.comparisonIntent === 'single'
      || (
        selection.comparisonIntent === 'target_comparison'
          ? selection.baselineTargetRevisionId
          : selection.baselineProfileRevisionId
      )
    )
    && selection.profileRevisionId
    && Number.isInteger(Number(selection.seed)),
  );
}
