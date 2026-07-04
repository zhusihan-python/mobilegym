"""VS-07 Block G: pin that the default supervisor is a FakeRunSupervisor.

Production wiring (a real RunSupervisor with executor_resolver that builds
Serial/ParallelRunExecutor with real env/agent factories) is deferred. This test
pins that ``create_app`` defaults to the no-op FakeRunSupervisor so an accidental
flip to real execution (which has no factories yet) is caught here, not in prod.
"""
from __future__ import annotations

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.services.runs import FakeRunSupervisor


def test_create_app_defaults_to_fake_supervisor(tmp_path):
    """create_app() with no supervisor arg must wire a FakeRunSupervisor.

    The FakeRunSupervisor never executes runs (it just queues ids), which is the
    correct VS-07 default: real execution requires env/agent factories that are
    not yet wired into production. If this assertion fails, production may have
    been flipped to a real RunSupervisor without factories — re-introduce the
    pin or wire the factories.
    """
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        app = create_app(settings, database=database)
        assert isinstance(app.state.supervisor, FakeRunSupervisor)
    finally:
        database.close()


def test_fake_supervisor_lacks_executor_resolver():
    """The FakeRunSupervisor has no executor_resolver attribute (it never executes).

    This complements the type pin above: the fake is a pure queue stub, so it
    must not expose the lane-aware resolver surface that only the real
    RunSupervisor carries.
    """
    fake = FakeRunSupervisor()
    assert not hasattr(fake, "_executor_resolver")
    # It still exposes the lifecycle stubs create_app probes via hasattr.
    assert hasattr(fake, "start")
    assert hasattr(fake, "stop")
    assert hasattr(fake, "snapshot")
