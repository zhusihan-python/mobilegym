"""P2 regression: _mark_run_failed must transition lane_attempts to 'failed'.

Without this, a run whose executor raises (e.g. VS-07 unknown/duplicate
reconciliation) leaves lane_attempts stuck in 'running' while runs/run_attempts
are 'failed' — an inconsistent terminal state.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from test_platform.persistence.database import Database
from test_platform.services.runs import RunSupervisor
from test_platform.tests.integration.test_single_lane_materialization import (
    _create_run,
    _settings,
)


class _CrashingExecutor:
    """Executor that always raises, forcing the supervisor's failed path."""

    async def execute_run(self, run_id: str, *, token=None, events=None, run_event_writer=None) -> Any:
        raise RuntimeError("simulated executor crash")


@pytest.mark.asyncio
async def test_failed_run_transitions_lane_attempts_to_failed(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=1)

        supervisor = RunSupervisor(database, settings, executor=_CrashingExecutor())
        await supervisor.start()
        subscription = await supervisor._broker.subscribe(run.id)
        supervisor.submit(run.id)

        # Drain until run.failed.
        import asyncio as _a

        try:
            while True:
                event = await _a.wait_for(subscription.queue.get(), timeout=2.0)
                if event is None:
                    break
                if event.type == "run.failed":
                    break
        except _a.TimeoutError:
            pass
        await supervisor.stop()

        runs_row = database.connection.execute(
            "SELECT state FROM runs WHERE id = ?", (run.id,)
        ).fetchone()
        assert runs_row["state"] == "failed"

        run_attempts_row = database.connection.execute(
            "SELECT state FROM run_attempts WHERE run_id = ?", (run.id,)
        ).fetchone()
        assert run_attempts_row["state"] == "failed"

        # P2: lane_attempts must ALSO be 'failed', not left 'running'.
        lane_attempts_row = database.connection.execute(
            """
            SELECT la.state
            FROM lane_attempts la
            JOIN lanes l ON l.id = la.lane_id
            WHERE l.run_id = ?
            """,
            (run.id,),
        ).fetchone()
        assert lane_attempts_row is not None
        assert lane_attempts_row["state"] == "failed"
    finally:
        database.close()
