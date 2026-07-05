"""VS-08 Block D: RunSupervisor.submit uses an injected token_factory.

The supervisor's submit() must use the injected ``token_factory`` to create a
FRESH token per run (no cross-run sharing). For multiprocess runs the factory
creates a MultiprocCancelToken (wrapping an mp.Event) so cancel propagates to
children. This test verifies the factory is invoked and the produced token's
cancel() reaches the mp.Event.
"""
from __future__ import annotations

import multiprocessing as mp

import pytest

from bench_env.runner.cancellation import MultiprocCancelToken
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.services.runs import RunSupervisor
from test_platform.tests.integration.test_single_lane_materialization import (
    _create_run,
    _settings,
)


def test_supervisor_submit_uses_injected_token_factory(tmp_path):
    """submit() must call token_factory to create the per-run token."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=1)

        created_tokens: list[MultiprocCancelToken] = []
        shared_mp_event = mp.Event()

        def token_factory() -> MultiprocCancelToken:
            tok = MultiprocCancelToken(mp_event=shared_mp_event)
            created_tokens.append(tok)
            return tok

        supervisor = RunSupervisor(database, settings, token_factory=token_factory)
        # submit registers the token synchronously.
        supervisor.submit(run.id)

        assert len(created_tokens) == 1
        # The registered token is the factory's output and shares the mp.Event.
        registered = supervisor._tokens[run.id]
        assert registered is created_tokens[0]
        assert registered.mp_event is shared_mp_event
    finally:
        database.close()


def test_supervisor_cancel_propagates_to_mp_event(tmp_path):
    """request_cancel sets cancel_requested_at AND calls token.cancel(), which
    for a MultiprocCancelToken sets the shared mp.Event."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=1)
        shared_mp_event = mp.Event()

        def token_factory() -> MultiprocCancelToken:
            return MultiprocCancelToken(mp_event=shared_mp_event)

        supervisor = RunSupervisor(database, settings, token_factory=token_factory)
        supervisor.submit(run.id)

        assert shared_mp_event.is_set() is False
        result = supervisor.request_cancel(run.id)
        assert result is True
        # The mp.Event was set by token.cancel().
        assert shared_mp_event.is_set() is True
    finally:
        database.close()


def test_supervisor_default_token_factory_is_plain_cancellation_token(tmp_path):
    """When no token_factory is passed, submit() creates a plain CancellationToken
    (no mp.Event) — cooperative-only fallback."""
    from bench_env.runner.cancellation import CancellationToken

    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=1)
        supervisor = RunSupervisor(database, settings)
        supervisor.submit(run.id)
        registered = supervisor._tokens[run.id]
        assert isinstance(registered, CancellationToken)
        assert not isinstance(registered, MultiprocCancelToken)
    finally:
        database.close()


def test_supervisor_token_factory_creates_fresh_token_per_run(tmp_path):
    """Each submit() call creates a FRESH token (no cross-run sharing)."""
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run1 = _create_run(database, settings, repeat_n=1)
        # Second create_run with a different idempotency key
        from test_platform.tests.integration.test_single_lane_materialization import _catalog
        from test_platform.persistence.repositories import WorkflowRepository
        from test_platform.services.runs import RunService

        # Publish another version to allow a new run.
        wf_rows = database.connection.execute(
            "SELECT id FROM workflows LIMIT 1"
        ).fetchall()
        version = WorkflowRepository(database).publish(str(wf_rows[0]["id"]))
        run2 = RunService(
            database, settings, supervisor=type("F", (), {"submit": lambda self, rid: None})(),
            catalog_builder=_catalog,
        ).create_run(
            workflow_version_id=version.id,
            name="second run",
            seed=1111,
            idempotency_key="second-run-1",
        )

        created: list = []

        def token_factory():
            t = MultiprocCancelToken()
            created.append(t)
            return t

        supervisor = RunSupervisor(database, settings, token_factory=token_factory)
        supervisor.submit(run1.id)
        supervisor.submit(run2.id)

        assert len(created) == 2
        assert created[0] is not created[1]  # FRESH per run
        assert supervisor._tokens[run1.id] is created[0]
        assert supervisor._tokens[run2.id] is created[1]
    finally:
        database.close()
