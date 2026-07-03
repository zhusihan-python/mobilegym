import type {
  ApiErrorBody,
  ApiErrorDetail,
  CollectionResponse,
  ReadinessResponse,
  RunSummary,
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
  const response = await fetch(`${API_PREFIX}${path}`, {
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    ...init,
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

export function listRuns(): Promise<CollectionResponse<RunSummary>> {
  return apiFetch<CollectionResponse<RunSummary>>('/runs');
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
