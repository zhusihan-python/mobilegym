import type {
  ApiErrorBody,
  ApiErrorDetail,
  CollectionResponse,
  Project,
  ReadinessResponse,
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
