import type {
  ApiErrorBody,
  ApiErrorDetail,
  ArtifactItem,
  Baseline,
  BaselineDetail,
  BaselineEligibility,
  CancelRunResponse,
  CollectionResponse,
  CompatibilityResult,
  Comparison,
  EpisodeReplay,
  ExecutionProfile,
  ExecutionProfileRevision,
  ExecutionProfileSpec,
  FollowupRunAttempt,
  FollowupRunPreview,
  Project,
  ReadinessResponse,
  RunDetail,
  RunEvent,
  RunDiagnostics,
  RunReport,
  RunSummary,
  TaskCatalogItem,
  TaskCatalogResponse,
  Target,
  TargetHealthResponse,
  WorkflowCompilePreview,
  WorkflowDefinition,
  WorkflowPublishResponse,
  WorkflowSummary,
} from './types';

const API_PREFIX = '/api/platform/v1';

export class ApiError extends Error {
  code: string;
  details: ApiErrorDetail[];
  requestId?: string;

  constructor(code: string, message: string, details: ApiErrorDetail[] = [], requestId?: string) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }

  const response = await fetch(`${API_PREFIX}${path}`, {
    ...init,
    headers,
  });
  return readResponse<T>(response);
}

export async function fetchReadiness(): Promise<ReadinessResponse> {
  const response = await fetch('/health/ready', {
    headers: { Accept: 'application/json' },
  });
  const body = await readJson(response);

  if (isReadinessResponse(body)) {
    return body;
  }

  if (!response.ok) {
    throw toApiError(body);
  }

  throw new ApiError('INVALID_RESPONSE', 'The readiness response was not valid.');
}

export function listProjects(): Promise<CollectionResponse<Project>> {
  return apiFetch<CollectionResponse<Project>>('/projects');
}

export function createProject(name: string): Promise<Project> {
  return apiFetch<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export function archiveProject(projectId: string): Promise<Project> {
  return apiFetch<Project>(`/projects/${encodeURIComponent(projectId)}/archive`, {
    method: 'POST',
  });
}

export function listRuns(projectId?: string): Promise<CollectionResponse<RunSummary>> {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return apiFetch<CollectionResponse<RunSummary>>(`/runs${query}`);
}

export function createRun(input: {
  workflowVersionId: string;
  name?: string;
  seed: number;
  idempotencyKey: string;
  execution?: {
    agent: string;
    modelName: string;
    modelBaseUrl: string;
    modelApiKey?: string;
    imageUrlFormat?: string;
  };
}): Promise<RunDetail> {
  return apiFetch<RunDetail>('/runs', {
    method: 'POST',
    headers: { 'Idempotency-Key': input.idempotencyKey },
    body: JSON.stringify({
      workflow_version_id: input.workflowVersionId,
      name: input.name,
      overrides: {
        seed: input.seed,
        execution: input.execution
          ? {
              agent: input.execution.agent,
              model_name: input.execution.modelName,
              model_base_url: input.execution.modelBaseUrl,
              model_api_key: input.execution.modelApiKey || undefined,
              image_url_format: input.execution.imageUrlFormat || undefined,
            }
          : undefined,
      },
    }),
  });
}

export function importLegacyRun(input: {
  projectId: string;
  sourcePath: string;
  name?: string;
}): Promise<RunDetail> {
  return apiFetch<RunDetail>('/runs/import', {
    method: 'POST',
    body: JSON.stringify({
      project_id: input.projectId,
      source_path: input.sourcePath,
      name: input.name ?? null,
    }),
  });
}

export function getRun(runId: string): Promise<RunDetail> {
  return apiFetch<RunDetail>(`/runs/${encodeURIComponent(runId)}`);
}

/**
 * VS-15A: fetch the replay DTO (trajectory steps + artifact ids) for one
 * episode attempt. Defaults to the latest terminal attempt; pass a specific
 * run attempt number to scrub an older attempt.
 */
export function getEpisodeReplay(input: {
  runId: string;
  episodeKey: string;
  laneKey?: string;
  attemptNo?: number | 'latest';
}): Promise<EpisodeReplay> {
  const params = new URLSearchParams();
  if (input.laneKey) {
    params.set('lane_key', input.laneKey);
  }
  if (input.attemptNo !== undefined) {
    params.set('attempt_no', String(input.attemptNo));
  }
  const query = params.toString();
  return apiFetch<EpisodeReplay>(
    `/runs/${encodeURIComponent(input.runId)}/episodes/${encodeURIComponent(
      input.episodeKey,
    )}/replay${query ? `?${query}` : ''}`,
  );
}

/**
 * VS-09: fetch the comparison (pairs + classifications + prepared DTOs) for a
 * paired run. Returns the comparison graph; the UI renders it side-by-side.
 */
export function getComparison(runId: string): Promise<Comparison> {
  return apiFetch<Comparison>(`/runs/${encodeURIComponent(runId)}/comparison`);
}

export function getReport(runId: string): Promise<RunReport> {
  return apiFetch<RunReport>(`/runs/${encodeURIComponent(runId)}/report`);
}

export type DiagnosticFilters = {
  category?: string;
  severity?: string;
  targetId?: string;
  appId?: string;
  taskId?: string;
  retryable?: boolean;
  laneKey?: string;
  episodeKey?: string;
  attemptNo?: number;
  cursor?: string;
  limit?: number;
};

export function getDiagnostics(
  runId: string,
  filters: DiagnosticFilters = {},
): Promise<RunDiagnostics> {
  const params = new URLSearchParams();
  if (filters.category) params.set('category', filters.category);
  if (filters.severity) params.set('severity', filters.severity);
  if (filters.targetId) params.set('target_id', filters.targetId);
  if (filters.appId) params.set('app_id', filters.appId);
  if (filters.taskId) params.set('task_id', filters.taskId);
  if (filters.retryable !== undefined) params.set('retryable', String(filters.retryable));
  if (filters.laneKey) params.set('lane_key', filters.laneKey);
  if (filters.episodeKey) params.set('episode_key', filters.episodeKey);
  if (filters.attemptNo !== undefined) params.set('attempt_no', String(filters.attemptNo));
  if (filters.cursor) params.set('cursor', filters.cursor);
  if (filters.limit !== undefined) params.set('limit', String(filters.limit));
  const query = params.toString();
  return apiFetch<RunDiagnostics>(
    `/runs/${encodeURIComponent(runId)}/diagnostics${query ? `?${query}` : ''}`,
  );
}

export function listExecutionProfiles(
  projectId: string,
): Promise<CollectionResponse<ExecutionProfile>> {
  return apiFetch<CollectionResponse<ExecutionProfile>>(
    `/projects/${encodeURIComponent(projectId)}/execution-profiles`,
  );
}

export function createExecutionProfile(input: {
  projectId: string;
  name: string;
  draftSpec: ExecutionProfileSpec;
}): Promise<ExecutionProfile> {
  return apiFetch<ExecutionProfile>(
    `/projects/${encodeURIComponent(input.projectId)}/execution-profiles`,
    {
      method: 'POST',
      body: JSON.stringify({
        name: input.name,
        draft_spec: input.draftSpec,
      }),
    },
  );
}

export function publishExecutionProfile(input: {
  projectId: string;
  executionProfileId: string;
}): Promise<ExecutionProfileRevision> {
  return apiFetch<ExecutionProfileRevision>(
    `/projects/${encodeURIComponent(input.projectId)}/execution-profiles/${encodeURIComponent(
      input.executionProfileId,
    )}/publish`,
    { method: 'POST' },
  );
}

export async function getAllDiagnostics(runId: string): Promise<RunDiagnostics> {
  let page = await getDiagnostics(runId, { limit: 200 });
  const items = [...page.items];
  const visitedCursors = new Set<string>();
  while (page.next_cursor && !visitedCursors.has(page.next_cursor)) {
    visitedCursors.add(page.next_cursor);
    page = await getDiagnostics(runId, { cursor: page.next_cursor, limit: 200 });
    items.push(...page.items);
  }
  return { ...page, items, next_cursor: null };
}

export function listArtifacts(runId: string): Promise<{ items: ArtifactItem[] }> {
  return apiFetch<{ items: ArtifactItem[] }>(`/runs/${encodeURIComponent(runId)}/artifacts`);
}

export async function getReportExport(
  runId: string,
  format: 'json' | 'html' = 'json',
): Promise<string> {
  const response = await fetch(
    `${API_PREFIX}/runs/${encodeURIComponent(runId)}/report/export?format=${format}`,
    { headers: { Accept: format === 'html' ? 'text/html' : 'application/json' } },
  );
  if (!response.ok) {
    throw toApiError(await readJson(response));
  }
  return response.text();
}

export function promoteBaseline(
  runId: string,
  displayName: string,
  laneKey?: string,
): Promise<Baseline> {
  return apiFetch<Baseline>(`/runs/${encodeURIComponent(runId)}/baseline`, {
    method: 'POST',
    body: JSON.stringify({ display_name: displayName, lane_key: laneKey ?? null }),
  });
}

export function listBaselines(projectId: string): Promise<CollectionResponse<Baseline>> {
  return apiFetch<CollectionResponse<Baseline>>(
    `/projects/${encodeURIComponent(projectId)}/baselines`,
  );
}

export function getBaseline(baselineId: string): Promise<BaselineDetail> {
  return apiFetch<BaselineDetail>(`/baselines/${encodeURIComponent(baselineId)}`);
}

export function archiveBaseline(baselineId: string): Promise<Baseline> {
  return apiFetch<Baseline>(`/baselines/${encodeURIComponent(baselineId)}/archive`, {
    method: 'POST',
  });
}

export function getBaselineEligibility(
  runId: string,
  laneKey?: string,
): Promise<BaselineEligibility> {
  const query = laneKey ? `?lane_key=${encodeURIComponent(laneKey)}` : '';
  return apiFetch<BaselineEligibility>(
    `/runs/${encodeURIComponent(runId)}/baseline/eligibility${query}`,
  );
}

export function cancelRun(runId: string, idempotencyKey?: string): Promise<CancelRunResponse> {
  const headers: Record<string, string> = {};
  if (idempotencyKey) {
    headers['Idempotency-Key'] = idempotencyKey;
  }
  return apiFetch<CancelRunResponse>(`/runs/${encodeURIComponent(runId)}/cancel`, {
    method: 'POST',
    headers,
  });
}

export function checkModelCompatibility(input: {
  modelBaseUrl: string;
  modelName: string;
  modelApiKey?: string;
  imageUrlFormat: string;
  timeoutSeconds?: number;
}): Promise<CompatibilityResult> {
  return apiFetch<CompatibilityResult>('/model-compatibility/check', {
    method: 'POST',
    body: JSON.stringify({
      model_base_url: input.modelBaseUrl,
      model_name: input.modelName,
      model_api_key: input.modelApiKey || '',
      image_url_format: input.imageUrlFormat,
      timeout_seconds: input.timeoutSeconds ?? 15,
    }),
  });
}

type FollowupRunInput = {
  previewToken?: string;
  execution?: {
    modelApiKey?: string;
  };
};

function followupRunBody(input?: FollowupRunInput): BodyInit | undefined {
  const modelApiKey = input?.execution?.modelApiKey?.trim();
  const payload: Record<string, unknown> = {};
  if (input?.previewToken) {
    payload.preview_token = input.previewToken;
  }
  if (modelApiKey) {
    payload.execution = {
      model_api_key: modelApiKey,
    };
  }
  return Object.keys(payload).length > 0 ? JSON.stringify(payload) : undefined;
}

export function getFollowupPreview(
  runId: string,
  kind: 'retry' | 'resume',
): Promise<FollowupRunPreview> {
  return apiFetch<FollowupRunPreview>(
    `/runs/${encodeURIComponent(runId)}/${kind}/preview`,
  );
}

export function retryRun(runId: string, input?: FollowupRunInput): Promise<FollowupRunAttempt> {
  const body = followupRunBody(input);
  return apiFetch<FollowupRunAttempt>(`/runs/${encodeURIComponent(runId)}/retry`, {
    method: 'POST',
    body,
  });
}

export function resumeRun(runId: string, input?: FollowupRunInput): Promise<FollowupRunAttempt> {
  const body = followupRunBody(input);
  return apiFetch<FollowupRunAttempt>(`/runs/${encodeURIComponent(runId)}/resume`, {
    method: 'POST',
    body,
  });
}

/**
 * Subscribe to a run's live event stream (SSE). `onEvent` is called for each
 * committed event; `onReset` is called when the server signals
 * `stream.reset_required` (slow subscriber), at which point the caller must
 * refetch the run snapshot via REST and reconnect.
 *
 * Returns a disposer that closes the EventSource. Reconnect with exponential
 * backoff is handled by the browser's native EventSource implementation; the
 * `after` query seeds the Last-Event-ID-equivalent cursor on reconnect.
 */
export function streamRunEvents(
  runId: string,
  onEvent: (event: RunEvent) => void,
  onReset: () => void,
  initialAfter = 0,
): () => void {
  // EventSource is absent in some test environments (jsdom) and during static
  // builds without an API proxy. Degrade to a no-op subscription rather than
  // crashing the run detail page — the snapshot is still fetched via REST.
  if (typeof EventSource === 'undefined') {
    return () => undefined;
  }
  let after = initialAfter;
  const path = `/api/platform/v1/runs/${encodeURIComponent(runId)}/events/stream?after=${after}`;
  const source = new EventSource(path);

  source.onmessage = () => {
    /* generic messages (heartbeats) are ignored */
  };
  source.addEventListener('stream.reset_required', () => {
    onReset();
  });
  source.addEventListener('open', () => {
    /* connected */
  });
  source.onerror = () => {
    // Native EventSource auto-reconnects. The `after` cursor is fixed at open
    // time, so on reconnect the server replays events after that sequence.
  };

  // Dispatch typed events. EventSource exposes named events via addEventListener
  // for every `event:` line; we route them all through a single handler that
  // parses the `data` payload and bumps the cursor.
  const generic = (messageEvent: MessageEvent) => {
    try {
      const data = JSON.parse(messageEvent.data) as RunEvent;
      if (typeof data.sequence === 'number') {
        after = Math.max(after, data.sequence);
        onEvent(data);
      }
    } catch {
      /* malformed payload — ignore, the snapshot remains authoritative */
    }
  };
  // The server sends `event: <type>` lines; EventSource dispatches a listener
  // registered for each type. We register a catch-all by listening to the
  // default message channel for untyped frames and named types individually.
  source.addEventListener('run.started', generic as EventListener);
  source.addEventListener('run.cancel_requested', generic as EventListener);
  source.addEventListener('run.cancelled', generic as EventListener);
  source.addEventListener('run.completed', generic as EventListener);
  source.addEventListener('run.failed', generic as EventListener);
  source.addEventListener('episode.started', generic as EventListener);
  source.addEventListener('episode.step_recorded', generic as EventListener);
  source.addEventListener('episode.completed', generic as EventListener);
  source.addEventListener('episode.cancelled', generic as EventListener);
  // VS-07: parallel worker lifecycle + episode error events.
  source.addEventListener('episode.error', generic as EventListener);
  source.addEventListener('worker.started', generic as EventListener);
  source.addEventListener('worker.stopped', generic as EventListener);
  // VS-08: multiprocess shard lifecycle/fatal + coalesced stream events.
  source.addEventListener('shard.started', generic as EventListener);
  source.addEventListener('shard.stopped', generic as EventListener);
  source.addEventListener('shard.fatal', generic as EventListener);
  source.addEventListener('stream.events_coalesced', generic as EventListener);

  return () => source.close();
}

export function listTargets(projectId: string): Promise<CollectionResponse<Target>> {
  return apiFetch<CollectionResponse<Target>>(
    `/targets?project_id=${encodeURIComponent(projectId)}`,
  );
}

export function createSimulatorTarget(input: {
  projectId: string;
  name: string;
  envUrl: string;
  deviceProfile: {
    name: string;
    viewportWidth: number;
    viewportHeight: number;
    physicalWidth: number;
    physicalHeight: number;
    deviceScaleFactor: number;
  };
}): Promise<Target> {
  return apiFetch<Target>('/targets', {
    method: 'POST',
    body: JSON.stringify({
      project_id: input.projectId,
      name: input.name,
      config: {
        kind: 'simulator',
        connection: { env_url: input.envUrl },
        device_profile: {
          name: input.deviceProfile.name,
          viewport_width: input.deviceProfile.viewportWidth,
          viewport_height: input.deviceProfile.viewportHeight,
          physical_width: input.deviceProfile.physicalWidth,
          physical_height: input.deviceProfile.physicalHeight,
          device_scale_factor: input.deviceProfile.deviceScaleFactor,
        },
        runtime: {},
        labels: {},
      },
    }),
  });
}

export function checkTargetHealth(targetId: string): Promise<TargetHealthResponse> {
  return apiFetch<TargetHealthResponse>(`/targets/${encodeURIComponent(targetId)}/health`, {
    method: 'POST',
  });
}

export function listTasks(filters: { suite?: string } = {}): Promise<TaskCatalogResponse> {
  const params = new URLSearchParams();
  if (filters.suite) {
    params.set('suite', filters.suite);
  }
  const query = params.toString();
  return apiFetch<TaskCatalogResponse>(`/tasks${query ? `?${query}` : ''}`);
}

export function getTask(taskId: string): Promise<TaskCatalogItem> {
  return apiFetch<TaskCatalogItem>(`/tasks/${encodeURIComponent(taskId)}`);
}

export function listWorkflows(projectId: string): Promise<CollectionResponse<WorkflowSummary>> {
  return apiFetch<CollectionResponse<WorkflowSummary>>(
    `/projects/${encodeURIComponent(projectId)}/workflows`,
  );
}

export function createWorkflow(input: {
  projectId: string;
  name: string;
  definition: WorkflowDefinition;
}): Promise<WorkflowSummary> {
  return apiFetch<WorkflowSummary>(`/projects/${encodeURIComponent(input.projectId)}/workflows`, {
    method: 'POST',
    body: JSON.stringify({
      name: input.name,
      definition: input.definition,
    }),
  });
}

export function updateWorkflowDraft(input: {
  workflowId: string;
  name?: string;
  definition: WorkflowDefinition;
}): Promise<WorkflowSummary> {
  return apiFetch<WorkflowSummary>(`/workflows/${encodeURIComponent(input.workflowId)}/draft`, {
    method: 'PATCH',
    body: JSON.stringify({
      name: input.name,
      definition: input.definition,
    }),
  });
}

export function compileWorkflowPreview(workflowId: string): Promise<WorkflowCompilePreview> {
  return apiFetch<WorkflowCompilePreview>(
    `/workflows/${encodeURIComponent(workflowId)}/compile-preview`,
    { method: 'POST' },
  );
}

export function publishWorkflow(workflowId: string): Promise<WorkflowPublishResponse> {
  return apiFetch<WorkflowPublishResponse>(`/workflows/${encodeURIComponent(workflowId)}/publish`, {
    method: 'POST',
  });
}

async function readResponse<T>(response: Response): Promise<T> {
  const body = await readJson(response);
  if (!response.ok) {
    throw toApiError(body);
  }
  return body as T;
}

async function readJson(response: Response): Promise<unknown> {
  if (response.status === 204) {
    return null;
  }

  try {
    return await response.json();
  } catch {
    return null;
  }
}

function toApiError(body: unknown): ApiError {
  if (isApiErrorBody(body)) {
    return new ApiError(
      body.error.code,
      body.error.message,
      body.error.details,
      body.error.request_id,
    );
  }
  return new ApiError('API_UNREACHABLE', 'Unable to reach the Test Platform API.');
}

function isApiErrorBody(body: unknown): body is ApiErrorBody {
  if (!body || typeof body !== 'object' || !('error' in body)) {
    return false;
  }
  const error = (body as { error?: unknown }).error;
  return Boolean(error && typeof error === 'object' && 'code' in error && 'message' in error);
}

function isReadinessResponse(body: unknown): body is ReadinessResponse {
  if (!body || typeof body !== 'object') {
    return false;
  }
  const candidate = body as Partial<ReadinessResponse>;
  return typeof candidate.ready === 'boolean' && Boolean(candidate.checks);
}
