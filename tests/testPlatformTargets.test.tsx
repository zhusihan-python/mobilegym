import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

const project = {
  id: '11111111111111111111111111111111',
  name: 'Mobile App Regression',
  slug: 'mobile-app-regression',
  archived_at: null,
  created_at: '2026-07-03T00:00:00.000Z',
  updated_at: '2026-07-03T00:00:00.000Z',
};

const target = {
  id: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  project_id: project.id,
  name: 'Local simulator',
  kind: 'simulator',
  enabled: true,
  config: {
    kind: 'simulator',
    connection: {
      env_url: 'http://127.0.0.1:5173',
      proxy_configured: false,
    },
    device_profile: {
      name: 'Pixel 7',
      viewport_width: 393,
      viewport_height: 852,
      physical_width: 1080,
      physical_height: 2400,
      device_scale_factor: 2.75,
    },
    runtime: { locale: 'en-US' },
    labels: { lane: 'local' },
  },
  latest_revision: {
    id: 'rev-1',
    metadata_hash: 'hash-1',
    health_status: 'healthy',
    resolved_at: '2026-07-03T00:00:02.000Z',
    warnings: [],
    metadata: {
      schemaVersion: 1,
      simulator: {
        product: 'mobile-gym',
        version: '0.1.0',
        buildId: 'build-vs02',
      },
      apps: [
        {
          id: 'wechat',
          packageName: 'com.tencent.mm',
          displayName: 'WeChat',
          displayNameEn: 'WeChat',
          version: '8.0.46',
          versionCode: 80046,
          type: 'plugin',
        },
      ],
      data: { revision: 'seed-v1', bundleHash: 'data-sha' },
      capabilities: ['sim.metadata.v1'],
    },
  },
  created_at: '2026-07-03T00:00:01.000Z',
  updated_at: '2026-07-03T00:00:01.000Z',
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestUrl(input: RequestInfo | URL): URL {
  if (typeof input === 'string') {
    return new URL(input, window.location.origin);
  }
  if (input instanceof URL) {
    return input;
  }
  return new URL(input.url);
}

describe('Test Platform targets workspace', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/targets');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('shows simulator app versions and health warnings', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);

      if (url.pathname === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'SQLite database is ready.' },
            migrations: { ready: true, message: 'All migrations applied.' },
            runs_dir: { ready: true, message: 'Runs directory is writable.' },
          },
        });
      }

      if (url.pathname === '/api/platform/v1/projects') {
        return jsonResponse({ items: [project], next_cursor: null });
      }

      if (url.pathname === '/api/platform/v1/targets' && init?.method !== 'POST') {
        return jsonResponse({ items: [target], next_cursor: null });
      }

      if (url.pathname === `/api/platform/v1/targets/${target.id}/health` && init?.method === 'POST') {
        return jsonResponse({
          healthy: true,
          executable: true,
          revision: {
            ...target.latest_revision,
            warnings: ['Data revision seed-v1 is not pinned.'],
          },
          warnings: ['Data revision seed-v1 is not pinned.'],
          error: null,
        });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Targets' })).toBeTruthy();
    expect(await screen.findByText('Local simulator')).toBeTruthy();
    expect(screen.getByText('Pixel 7')).toBeTruthy();
    expect(screen.getByText('WeChat 8.0.46 (80046)')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Check health for Local simulator' }));

    await waitFor(() => {
      expect(screen.getByText('Data revision seed-v1 is not pinned.')).toBeTruthy();
    });
  });
});
