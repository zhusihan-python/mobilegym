export type ReadinessCheck = {
  ready: boolean;
  message: string;
};

export type ReadinessResponse = {
  ready: boolean;
  checks: {
    database: ReadinessCheck;
    migrations: ReadinessCheck;
    runs_dir: ReadinessCheck;
  };
};

export type ApiErrorDetail = Record<string, unknown>;

export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    details: ApiErrorDetail[];
    request_id?: string;
  };
};

export type CollectionResponse<T> = {
  items: T[];
  next_cursor: string | null;
};

export type Project = {
  id: string;
  name: string;
  slug: string;
  archived_at: string | null;
  created_at: string;
  updated_at: string;
};

export type RunSummary = {
  id: string;
  project_id: string;
  workflow_version_id: string;
  name: string | null;
  state: string;
  fingerprint: string;
  progress: {
    planned_episodes: number;
    planned_lane_episodes: number;
    completed_episodes: number;
    completed_lane_episodes: number;
  };
  lanes: Array<{
    id: string;
    lane_key: string;
    role: string;
    target_id: string;
    target_revision_id: string;
  }>;
  gate_verdict: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  imported?: {
    source_path: string;
    source_name: string;
    provenance_missing: string[];
  } | null;
};

export type RunDetail = RunSummary & {
  run_plan: Record<string, unknown>;
  run_attempts?: Array<{
    id: string;
    attempt_no: number;
    reason: string;
    state: string;
    started_at: string | null;
    ended_at: string | null;
    created_at: string;
  }>;
  lane_attempts?: Array<{
    id: string;
    lane_id: string;
    lane_key: string;
    run_attempt_id?: string;
    attempt_no?: number;
    reason?: string;
    state: string;
    artifact_root: string;
    started_at: string | null;
    ended_at: string | null;
  }>;
  target_revisions: Array<{
    target_id: string;
    target_revision_id: string;
    metadata_hash: string;
  }>;
  episode_identities: Array<{
    episode_key: string;
    pair_key: string;
    task_base_id: string;
    task_id: string;
    instance_id: number;
    instance_seed: number;
    template_index: number | null;
    trial_id: number;
    max_steps: number;
    sequence_index: number | null;
    sequence_group_id: string | null;
  }>;
  episode_attempts?: Array<{
    episode_key: string;
    lane_key: string;
    run_attempt_id?: string;
    lane_attempt_id?: string;
    episode_attempt_id?: string;
    attempt_no: number;
    episode_attempt_no?: number;
    state: string;
    outcome: 'PASS' | 'FAIL' | 'ERROR' | 'CANCELLED' | 'SKIPPED' | string | null;
    error_code: string | null;
    artifact_root: string;
  }>;
};

export type FollowupRunAttempt = {
  run_id: string;
  run_attempt_id: string;
  attempt_no: number;
  reason: 'retry' | 'resume' | string;
  selected_lane_episodes: Array<{
    episode_key: string;
    lane_key: string;
    reason: string;
    sequence_index?: number | null;
    sequence_group_id?: string | null;
  }>;
};

export type RunEvent = {
  id: string;
  run_id: string;
  sequence: number;
  type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
  payload_version: number;
  run_attempt_id: string | null;
  lane_id: string | null;
  lane_attempt_id: string | null;
  episode_id: string | null;
  episode_attempt_id: string | null;
  worker_id: string | null;
};

/**
 * VS-09: a paired-run comparison. Joins baseline/candidate episode_attempts by
 * pair_key and carries the prepared DTO (params/instruction/projection_hash) so
 * the UI can show the frozen fixture both lanes reused.
 */
export type ComparisonPair = {
  id: string;
  comparison_id: string;
  pair_key: string;
  baseline_episode_attempt_id: string | null;
  candidate_episode_attempt_id: string | null;
  classification:
    | 'unpaired'
    | 'pairing_violation'
    | 'baseline_error'
    | 'candidate_error'
    | 'regression'
    | 'fixed'
    | 'stable_pass'
    | 'stable_fail'
    | string;
  integrity: {
    status: string;
    reason?: string;
    prepared_projection_hash?: string | null;
    baseline_actual_projection_hash?: string | null;
    candidate_actual_projection_hash?: string | null;
  } & Record<string, unknown>;
  delta: {
    baseline_outcome?: string | null;
    candidate_outcome?: string | null;
  } & Record<string, unknown>;
  prepared: {
    params: Record<string, unknown>;
    instruction: string | null;
    projection_hash: string | null;
  } | null;
};

export type Comparison = {
  id: string;
  run_id: string;
  run_attempt_id: string;
  baseline_lane_id: string;
  candidate_lane_id: string;
  policy: Record<string, unknown>;
  summary: Record<string, number> | null;
  created_at: string;
  pairs: ComparisonPair[];
};

export type GateResult = {
  schema_version: number;
  verdict: 'passed' | 'failed' | 'error' | string;
  thresholds: Record<string, number>;
  observed: Record<string, number | null>;
  reasons: Array<{
    metric: string;
    reason: string;
    threshold: number;
    observed: number | null;
  }>;
};

export type RunReport = {
  id: string;
  schema_version: number;
  run_id: string;
  run_attempt_id: string;
  input_hash: string;
  provenance: {
    project_id: string;
    run_id: string;
    run_attempt_id: string;
    workflow_version_id: string;
    run_plan_hash: string;
    task_source_digest: string;
    target_revision_ids: Record<string, string>;
    imported?: RunSummary['imported'];
  };
  functional: {
    summary: Record<string, number>;
    lanes: Record<string, Record<string, number>>;
    taxonomy: Record<string, Record<string, Record<string, number>>>;
  };
  performance: {
    summary: {
      unit: string;
      runtime_s: Record<string, number | null>;
      phases: Record<string, Record<string, number | null>>;
      excluded: Record<string, number>;
    };
  };
  comparison: {
    classification_counts: Record<string, number>;
    coverage: Record<string, number>;
    runtime_s: {
      unit: string;
      sample_count: number;
      baseline_p95: number | null;
      candidate_p95: number | null;
      absolute_delta: number | null;
      percent_delta: number | null;
    };
    phases: Record<string, Record<string, number | null>>;
    pairs: Array<{
      pair_key: string;
      classification: string;
      baseline_episode_attempt_id: string | null;
      candidate_episode_attempt_id: string | null;
      delta: Record<string, unknown>;
    }>;
    pair_deltas: Record<string, Record<string, unknown>>;
  };
  sequence?: {
    schema_version: number;
    groups: Array<{
      sequence_group_id: string;
      summary: Record<string, number>;
      items: Array<{
        sequence_index: number | null;
        step: number | null;
        sequence_group_id: string;
        episode_key: string;
        task_id: string;
        task_base_id: string;
        lane_key: string;
        status: string;
        outcome: string | null;
        error_code: string | null;
        episode_attempt_id: string | null;
      }>;
    }>;
  };
  gate: GateResult;
  created_at: string;
};

export type DiagnosticItem = {
  id: string;
  code: string;
  category: string;
  phase: string | null;
  severity: 'info' | 'warning' | 'error' | string;
  retryable: boolean;
  message: string;
  entity_type: string;
  run_id: string;
  run_attempt_id: string;
  lane_attempt_id?: string | null;
  episode_attempt_id?: string | null;
  episode_key?: string | null;
  lane_key?: string | null;
  comparison_id?: string | null;
  comparison_pair_id?: string | null;
  pair_key?: string | null;
  gate_result_id?: string | null;
  report_id?: string | null;
  artifact_refs: string[];
  recommended_action?: string | null;
  raw: Record<string, unknown>;
};

export type RunDiagnostics = {
  schema_version: number;
  run_id: string;
  run_attempt_id: string;
  input_hash: string;
  provenance: RunReport['provenance'];
  summary: {
    total: number;
    by_category: Record<string, number>;
    by_severity: Record<string, number>;
  };
  items: DiagnosticItem[];
};

export type ArtifactItem = {
  id: string;
  run_id: string;
  run_attempt_id: string | null;
  lane_attempt_id: string | null;
  episode_attempt_id: string | null;
  kind: string;
  relative_path: string;
  media_type: string | null;
  size_bytes: number | null;
  sha256: string | null;
  created_at: string;
};

export type Baseline = {
  id: string;
  report_id: string;
  run_id: string;
  project_id: string;
  workflow_version_id: string;
  run_plan_hash: string;
  task_source_digest: string;
  target_revision_ids: Record<string, string>;
  lane_key: string;
  target_revision_id: string;
  created_at: string;
};

export type CancelRunResponse = {
  run_id: string;
  cancel_requested: boolean;
  state: string;
};

export type TargetRevision = {
  id: string;
  metadata_hash: string;
  health_status: string;
  resolved_at: string;
  warnings: string[];
  metadata: {
    apps?: Array<{
      id: string;
      displayName: string;
      displayNameEn?: string;
      version: string;
      versionCode: number;
      packageName: string;
      type: string;
    }>;
    simulator?: {
      product?: string;
      version?: string;
      buildId?: string;
      sourceRevision?: string;
      bundleHash?: string;
    };
    data?: {
      revision?: string | null;
      bundleHash?: string | null;
    };
    device_profile?: TargetDeviceProfile;
  } & Record<string, unknown>;
};

export type TargetDeviceProfile = {
  name: string;
  viewport_width: number;
  viewport_height: number;
  physical_width: number;
  physical_height: number;
  device_scale_factor: number;
};

export type Target = {
  id: string;
  project_id: string;
  name: string;
  kind: 'simulator' | 'real_device';
  enabled: boolean;
  config: {
    kind: 'simulator' | 'real_device';
    connection: Record<string, unknown>;
    device_profile: TargetDeviceProfile;
    runtime: Record<string, unknown>;
    labels: Record<string, unknown>;
  };
  latest_revision: TargetRevision | null;
  created_at: string;
  updated_at: string;
};

export type TargetHealthResponse = {
  healthy: boolean;
  executable: boolean;
  revision: TargetRevision | null;
  warnings: string[];
  error: { code: string; message: string } | null;
};

export type TaskCatalogItem = {
  task_base_id: string;
  suite: string;
  class_name: string;
  apps: string[];
  templates: string[];
  parameters: Record<string, unknown>;
  difficulty: string;
  scope: string;
  objective: string;
  composition: string;
  capabilities: string[];
  max_steps: number | null;
  answer_fields: boolean;
  optimal_path_lengths: number[];
};

export type TaskCatalogResponse = CollectionResponse<TaskCatalogItem> & {
  digest: string;
};

export type WorkflowNode = {
  id: string;
  type: 'task_selection' | 'matrix' | 'execute' | 'compare' | 'gate' | 'report';
  depends_on: string[];
  config: Record<string, unknown>;
};

export type WorkflowDefinition = {
  schema_version: 1;
  name: string;
  nodes: WorkflowNode[];
};

export type WorkflowVersion = {
  id: string;
  workflow_id: string;
  version_no: number;
  status: 'published';
  definition: WorkflowDefinition;
  definition_hash: string;
  created_at: string;
  published_at: string;
};

export type WorkflowSummary = {
  id: string;
  project_id: string;
  name: string;
  draft_definition: WorkflowDefinition | null;
  latest_version: WorkflowVersion | null;
  created_at: string;
  updated_at: string;
};

/**
 * VS-10 Contract 3: a structured target-revision constraint violation. Surfaced
 * as advisory output in compile-preview (the authoritative gate is create-run).
 * Mirrors the backend `ConstraintViolation.to_dict()` shape.
 */
export type ConstraintViolation = {
  constraint: string;
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

export type WorkflowCompilePreview = {
  task_count: number;
  task_instance_count: number;
  trial_count: number;
  lane_count: number;
  total_episodes: number;
  lane_keys: string[];
  ordered_task_ids: string[];
  execution_strategy: string;
  /** VS-10: advisory target-revision constraint violations (Contract 3). */
  violations?: ConstraintViolation[];
};

export type WorkflowPublishResponse = {
  workflow_id: string;
  workflow_version_id: string;
  version: WorkflowVersion;
};

/**
 * VS-15A: replay DTO for one episode attempt. The backend loads
 * trajectory.json and maps each step's screenshot/prompt/response references
 * to registered artifact ids so the frontend never infers filesystem layout.
 */
export type EpisodeReplayStep = {
  step: number | null;
  route: Record<string, unknown>;
  action_type: string;
  action_data: Record<string, unknown>;
  thought: string;
  explain: string;
  summary: string;
  screenshot_artifact_id: string | null;
  screenshot_annotated_artifact_id: string | null;
  model_response_artifact_id: string | null;
  model_prompt_artifact_id: string | null;
};

export type EpisodeReplay = {
  run_id: string;
  episode_key: string;
  lane_key: string;
  attempt_no: number;
  episode_attempt_id: string;
  artifact_root: string;
  outcome: string | null;
  error_code: string | null;
  result: Record<string, unknown> | null;
  steps: EpisodeReplayStep[];
};
