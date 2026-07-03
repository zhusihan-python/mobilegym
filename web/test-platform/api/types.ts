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
  name: string | null;
  state: string;
  progress: Record<string, unknown>;
  lanes: unknown[];
  gate_verdict: unknown | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
};
