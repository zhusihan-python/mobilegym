import React from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

const projects = [
  {
    id: '11111111111111111111111111111111',
    name: 'Mobile App Regression',
    slug: 'mobile-app-regression',
    archived_at: null,
    created_at: '2026-07-03T00:00:00.000Z',
    updated_at: '2026-07-03T00:00:00.000Z',
  },
  {
    id: '22222222222222222222222222222222',
    name: 'Checkout Smoke',
    slug: 'checkout-smoke',
    archived_at: null,
    created_at: '2026-07-03T00:00:01.000Z',
    updated_at: '2026-07-03T00:00:01.000Z',
  },
];

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

describe('Test Platform project workspace', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/runs');
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('switches projects, reloads Runs for the selected project, and persists selection', async () => {
    const requestedRunProjectIds: string[] = [];
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
        return jsonResponse({ items: projects, next_cursor: null });
      }

      if (url.pathname === '/api/platform/v1/runs') {
        requestedRunProjectIds.push(url.searchParams.get('project_id') ?? '');
        return jsonResponse({ items: [], next_cursor: null });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const switcher = await screen.findByLabelText('Project');
    expect((switcher as HTMLSelectElement).value).toBe(projects[0].id);
    await screen.findByText('No runs yet');
    expect(requestedRunProjectIds).toEqual([projects[0].id]);

    fireEvent.change(switcher, { target: { value: projects[1].id } });

    await waitFor(() => {
      expect(requestedRunProjectIds).toEqual([projects[0].id, projects[1].id]);
    });
    expect(window.localStorage.getItem('test-platform.selected-project-id')).toBe(projects[1].id);

    cleanup();
    render(<App />);

    const persistedSwitcher = await screen.findByLabelText('Project');
    expect((persistedSwitcher as HTMLSelectElement).value).toBe(projects[1].id);
  });

  it('validates duplicate project names before creating', async () => {
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
        return jsonResponse({ items: projects, next_cursor: null });
      }

      if (url.pathname === '/api/platform/v1/runs') {
        return jsonResponse({ items: [], next_cursor: null });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole('button', { name: 'New project' }));
    fireEvent.change(screen.getByLabelText('Project name'), {
      target: { value: ' mobile app regression ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }));

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('A project with this name already exists.');
    expect(
      fetchMock.mock.calls.some((call) => {
        const [input, init] = call;
        return requestUrl(input).pathname === '/api/platform/v1/projects' && init?.method === 'POST';
      }),
    ).toBe(false);
  });

  it('archives the selected project and keeps the remaining project active', async () => {
    const archivedProject = { ...projects[1], archived_at: '2026-07-03T00:00:02.000Z' };
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
        return jsonResponse({ items: projects, next_cursor: null });
      }

      if (url.pathname === `/api/platform/v1/projects/${projects[1].id}/archive` && init?.method === 'POST') {
        return jsonResponse(archivedProject);
      }

      if (url.pathname === '/api/platform/v1/runs') {
        return jsonResponse({ items: [], next_cursor: null });
      }

      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const switcher = await screen.findByLabelText('Project');
    fireEvent.change(switcher, { target: { value: projects[1].id } });
    await waitFor(() => {
      expect((switcher as HTMLSelectElement).value).toBe(projects[1].id);
    });

    fireEvent.click(screen.getByRole('button', { name: 'Archive project' }));

    await waitFor(() => {
      expect((screen.getByLabelText('Project') as HTMLSelectElement).value).toBe(projects[0].id);
    });
    expect(screen.queryByText('Checkout Smoke')).toBeNull();
    expect(window.localStorage.getItem('test-platform.selected-project-id')).toBe(projects[0].id);
  });
});
