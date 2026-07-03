import { useCallback, useEffect, useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';

import { fetchReadiness } from '../api/client';
import type { ReadinessResponse } from '../api/types';

type ReadinessState =
  | { status: 'loading' }
  | { status: 'ready'; data: ReadinessResponse }
  | { status: 'not-ready'; data: ReadinessResponse }
  | { status: 'error'; message: string };

export function PlatformShell() {
  const [readiness, setReadiness] = useState<ReadinessState>({ status: 'loading' });

  const loadReadiness = useCallback(() => {
    setReadiness({ status: 'loading' });
    fetchReadiness()
      .then((data) => {
        setReadiness(data.ready ? { status: 'ready', data } : { status: 'not-ready', data });
      })
      .catch((error) => {
        setReadiness({
          status: 'error',
          message: error instanceof Error ? error.message : 'Unable to reach the Test Platform API.',
        });
      });
  }, []);

  useEffect(() => {
    loadReadiness();
  }, [loadReadiness]);

  const ready = readiness.status === 'ready';

  return (
    <div className="tp-root">
      <aside className="tp-sidebar">
        <div>
          <div className="tp-brand">MobileGym</div>
          <div className="tp-subtitle">Test Platform</div>
        </div>
        <nav className="tp-nav" aria-label="Test Platform">
          <NavLink to="/runs">Runs</NavLink>
        </nav>
      </aside>

      <div className="tp-main">
        <header className="tp-topbar">
          <div>
            <div className="tp-kicker">Local console</div>
            <h1>Runs</h1>
          </div>
          <ReadinessIndicator readiness={readiness} />
        </header>

        {ready ? (
          <Outlet />
        ) : (
          <ReadinessPanel readiness={readiness} onRetry={loadReadiness} />
        )}
      </div>
    </div>
  );
}

function ReadinessIndicator({ readiness }: { readiness: ReadinessState }) {
  if (readiness.status === 'ready') {
    return <div className="tp-status tp-status-ready">Service ready</div>;
  }
  if (readiness.status === 'loading') {
    return <div className="tp-status">Checking service</div>;
  }
  return <div className="tp-status tp-status-error">Service not ready</div>;
}

function ReadinessPanel({
  readiness,
  onRetry,
}: {
  readiness: ReadinessState;
  onRetry: () => void;
}) {
  if (readiness.status === 'loading') {
    return <section className="tp-panel">Checking the local API and SQLite database...</section>;
  }

  if (readiness.status === 'ready') {
    return null;
  }

  const message =
    readiness.status === 'not-ready'
      ? firstFailedCheck(readiness.data)?.message ?? 'The service is not ready.'
      : readiness.message;

  return (
    <section className="tp-alert" role="alert">
      <h2>Service setup needs attention</h2>
      <p>{message}</p>
      <p>Start the API or initialize the SQLite database, then retry.</p>
      <button type="button" onClick={onRetry}>
        Retry readiness
      </button>
    </section>
  );
}

function firstFailedCheck(readiness: ReadinessResponse) {
  return Object.values(readiness.checks).find((check) => !check.ready);
}
