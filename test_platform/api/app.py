from contextlib import asynccontextmanager

from fastapi import FastAPI

from test_platform.api.errors import install_error_handlers
from test_platform.api.middleware import install_request_id_middleware
from test_platform.api.routes.health import router as health_router
from test_platform.api.routes.runs import router as runs_router
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database


def create_app(
    settings: PlatformSettings,
    *,
    database: Database | None = None,
    adapter_registry: object | None = None,
    supervisor: object | None = None,
) -> FastAPI:
    platform_database = database or Database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.database = platform_database
        app.state.adapter_registry = adapter_registry
        app.state.supervisor = supervisor
        platform_database.initialize()
        try:
            yield
        finally:
            platform_database.close()

    app = FastAPI(title="MobileGym Test Platform", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = platform_database
    app.state.adapter_registry = adapter_registry
    app.state.supervisor = supervisor

    install_request_id_middleware(app)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(runs_router)
    return app
