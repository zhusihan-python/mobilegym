import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from test_platform.adapters.targets import TargetAdapterRegistry
from test_platform.api.errors import install_error_handlers
from test_platform.api.middleware import (
    install_mutation_origin_middleware,
    install_request_id_middleware,
)
from test_platform.api.routes.artifacts import router as artifacts_router
from test_platform.api.routes.compatibility import router as compatibility_router
from test_platform.api.routes.health import router as health_router
from test_platform.api.routes.diagnostics import router as diagnostics_router
from test_platform.api.routes.projects import router as projects_router
from test_platform.api.routes.replay import router as replay_router
from test_platform.api.routes.reports import router as reports_router
from test_platform.api.routes.runs import router as runs_router
from test_platform.api.routes.tasks import router as tasks_router
from test_platform.api.routes.targets import router as targets_router
from test_platform.api.routes.workflows import router as workflows_router
from test_platform.api.routes.events import router as events_router
from test_platform.api.routes.execution_profiles import router as execution_profiles_router
from test_platform.config import PlatformSettings
from test_platform.execution.sse_broker import SSEBroker
from test_platform.persistence.database import Database
from test_platform.services.runs import RunService, RunSupervisor


def create_app(
    settings: PlatformSettings,
    *,
    database: Database | None = None,
    adapter_registry: object | None = None,
    supervisor: object | None = None,
    executor_resolver: Callable[[Any], Any] | None = None,
    token_factory: Callable[[], Any] | None = None,
    compatibility_probe: object | None = None,
) -> FastAPI:
    platform_database = database or Database(settings)
    platform_adapter_registry = adapter_registry or TargetAdapterRegistry()
    platform_broker = SSEBroker()
    # Production defaults: construct a real OpenAI compatibility probe unless
    # a test explicitly injects one (or None to disable).
    if compatibility_probe is None:
        from test_platform.adapters.model_compatibility import OpenAICompatibilityProbe

        compatibility_probe = OpenAICompatibilityProbe()

    # Process-scoped preflight (shared across all RunService instances so the
    # TTL cache survives across HTTP requests). Thread-safe via internal lock.
    from test_platform.services.compatibility_preflight import CompatibilityPreflight

    platform_preflight = CompatibilityPreflight(compatibility_probe)
    platform_supervisor = supervisor or RunSupervisor(
        platform_database,
        settings,
        executor_resolver=executor_resolver,
        broker=platform_broker,
        token_factory=token_factory,
    )
    # If the supervisor owns its own broker (real RunSupervisor), share it so
    # published events reach the SSE route's subscribers.
    if hasattr(platform_supervisor, "bind_broker"):
        platform_supervisor.bind_broker(platform_broker)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.database = platform_database
        app.state.adapter_registry = platform_adapter_registry
        app.state.supervisor = platform_supervisor
        app.state.sse_broker = platform_broker
        platform_database.initialize()
        recovery = RunService(
            platform_database,
            settings,
            supervisor=platform_supervisor,
        ).reconcile_startup()
        if hasattr(platform_supervisor, "emit_recovery_failures"):
            platform_supervisor.emit_recovery_failures(
                list(recovery.get("recovered_run_ids") or [])
            )
        platform_broker.bind_loop(asyncio.get_running_loop())
        # Bind the broker to the running loop and start the supervisor if it
        # exposes the async lifecycle (the real RunSupervisor does; the fake one
        # used in tests is a no-op).
        if hasattr(platform_supervisor, "start"):
            await platform_supervisor.start()
        try:
            yield
        finally:
            if hasattr(platform_supervisor, "stop"):
                await platform_supervisor.stop()
            platform_database.close()

    app = FastAPI(title="MobileGym Test Platform", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = platform_database
    app.state.adapter_registry = platform_adapter_registry
    app.state.supervisor = platform_supervisor
    app.state.sse_broker = platform_broker
    app.state.compatibility_probe = compatibility_probe
    app.state.compatibility_preflight = platform_preflight

    install_request_id_middleware(app)
    install_mutation_origin_middleware(app)
    install_error_handlers(app)
    app.include_router(artifacts_router)
    app.include_router(compatibility_router)
    app.include_router(diagnostics_router)
    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(replay_router)
    app.include_router(reports_router)
    app.include_router(runs_router)
    app.include_router(tasks_router)
    app.include_router(targets_router)
    app.include_router(workflows_router)
    app.include_router(events_router)
    app.include_router(execution_profiles_router)
    return app
