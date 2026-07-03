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

const tasks = [
  {
    task_base_id: 'wechat.BlacklistContact',
    suite: 'wechat',
    class_name: 'BlacklistContact',
    apps: ['wechat'],
    templates: ['Blacklist a contact'],
    parameters: {},
    difficulty: 'L3',
    scope: 'S1',
    objective: 'operate',
    composition: 'sequential',
    capabilities: ['nav', 'settings'],
    max_steps: null,
    answer_fields: false,
    optimal_path_lengths: [4],
  },
  {
    task_base_id: 'alipay.PayMerchant',
    suite: 'alipay',
    class_name: 'PayMerchant',
    apps: ['alipay'],
    templates: ['Pay merchant'],
    parameters: {},
    difficulty: 'L2',
    scope: 'S1',
    objective: 'operate',
    composition: 'atomic',
    capabilities: ['payment'],
    max_steps: 30,
    answer_fields: false,
    optimal_path_lengths: [],
  },
];

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestUrl(input: RequestInfo | URL): URL {
  if (typeof input === 'string') return new URL(input, window.location.origin);
  if (input instanceof URL) return input;
  return new URL(input.url);
}

describe('Test Platform task catalog', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/tasks');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('filters task catalog rows and opens task details', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
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
      if (url.pathname === '/api/platform/v1/tasks') {
        const suite = url.searchParams.get('suite');
        const filtered = suite ? tasks.filter((task) => task.suite === suite) : tasks;
        return jsonResponse({ items: filtered, next_cursor: null, digest: 'sha256:test' });
      }
      if (url.pathname === '/api/platform/v1/tasks/wechat.BlacklistContact') {
        return jsonResponse(tasks[0]);
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Tasks' })).toBeTruthy();
    expect(await screen.findByText('wechat.BlacklistContact')).toBeTruthy();
    expect(screen.getByText('alipay.PayMerchant')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Suite'), { target: { value: 'wechat' } });

    await waitFor(() => {
      expect(screen.queryByText('alipay.PayMerchant')).toBeNull();
    });
    fireEvent.click(screen.getByRole('button', { name: 'Open details for wechat.BlacklistContact' }));

    expect(await screen.findByText('BlacklistContact')).toBeTruthy();
    expect(screen.getByText('nav, settings')).toBeTruthy();
  });
});
