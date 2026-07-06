"""Pin that create_app defaults to the production RunSupervisor wiring."""
from __future__ import annotations

from types import SimpleNamespace

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.services.execution import (
    MultiprocessRunExecutor,
    ParallelRunExecutor,
    SerialRunExecutor,
)
from test_platform.services.runs import FakeRunSupervisor, RunSupervisor


def test_create_app_defaults_to_real_supervisor(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        app = create_app(settings, database=database)
        assert isinstance(app.state.supervisor, RunSupervisor)
    finally:
        database.close()


def test_default_executor_resolver_wires_production_factories(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        app = create_app(settings, database=database)
        resolver = app.state.supervisor._executor_resolver

        serial = resolver(
            SimpleNamespace(runner_config={"env_url": "http://127.0.0.1:5173"})
        )
        assert isinstance(serial, SerialRunExecutor)
        assert serial.env_factory is not None
        assert serial.agent_factory is not None

        parallel = resolver(
            SimpleNamespace(
                runner_config={"env_url": "http://127.0.0.1:5173", "parallel": 2}
            )
        )
        assert isinstance(parallel, ParallelRunExecutor)
        assert parallel.env_factory is not None
        assert parallel.env_pool_factory is not None
        assert parallel.agent_factory is not None

        multiprocess = resolver(
            SimpleNamespace(
                runner_config={
                    "env_url": "http://127.0.0.1:5173",
                    "parallel": 2,
                    "processes": 2,
                }
            )
        )
        assert isinstance(multiprocess, MultiprocessRunExecutor)
        assert multiprocess.env_factory is not None
        assert multiprocess.env_pool_factory is not None
        assert multiprocess.agent_factory is not None
        assert multiprocess.child_runner_factory is not None
    finally:
        database.close()


def test_fake_supervisor_lacks_executor_resolver():
    fake = FakeRunSupervisor()
    assert not hasattr(fake, "_executor_resolver")
    assert hasattr(fake, "start")
    assert hasattr(fake, "stop")
    assert hasattr(fake, "snapshot")
