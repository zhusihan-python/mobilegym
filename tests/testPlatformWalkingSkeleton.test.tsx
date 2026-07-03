import React from 'react';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import App from '../web/test-platform/App';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === 'string') {
    return new URL(input, window.location.origin).pathname;
  }
  if (input instanceof URL) {
    return input.pathname;
  }
  return new URL(input.url).pathname;
}

describe('Test Platform walking skeleton', () => {
  beforeEach(() => {
    window.history.pushState({}, '', '/test-platform/runs');
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders Runs navigation and an API-backed empty state', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/health/ready') {
        return jsonResponse({
          ready: true,
          checks: {
            database: { ready: true, message: 'SQLite database is ready.' },
            migrations: { ready: true, message: 'All migrations applied.' },
            runs_dir: { ready: true, message: 'Runs directory is writable.' },
          },
        });
      }

      if (path === '/api/platform/v1/projects') {
        return jsonResponse({
          items: [
            {
              id: '11111111111111111111111111111111',
              name: 'Mobile App Regression',
              slug: 'mobile-app-regression',
              archived_at: null,
              created_at: '2026-07-03T00:00:00.000Z',
              updated_at: '2026-07-03T00:00:00.000Z',
            },
          ],
          next_cursor: null,
        });
      }

      if (path === '/api/platform/v1/runs') {
        return jsonResponse({ items: [], next_cursor: null });
      }

      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByRole('heading', { name: 'Runs' })).toBeTruthy();
    expect(screen.getByRole('navigation', { name: 'Test Platform' })).toBeTruthy();
    expect(screen.getByRole('link', { name: 'Runs' })).toBeTruthy();
    expect(await screen.findByText('Service ready')).toBeTruthy();
    expect(await screen.findByText('No runs yet')).toBeTruthy();
    expect(screen.getByText('The API returned zero runs for Mobile App Regression.')).toBeTruthy();
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(3);
    });
  });

  it('renders an actionable readiness error when the API is not ready', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = requestPath(input);

      if (path === '/health/ready') {
        return jsonResponse(
          {
            ready: false,
            checks: {
              database: {
                ready: false,
                message: 'SQLite database has not been initialized.',
              },
              migrations: {
                ready: false,
                message: 'Migrations have not run.',
              },
              runs_dir: {
                ready: true,
                message: 'Runs directory is writable.',
              },
            },
          },
          503,
        );
      }

      throw new Error(`Unexpected request: ${path}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('SQLite database has not been initialized.');
    expect(alert.textContent).toContain('Start the API or initialize the SQLite database, then retry.');
    expect(screen.getByRole('button', { name: 'Retry readiness' })).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
