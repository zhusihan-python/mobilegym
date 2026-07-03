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
