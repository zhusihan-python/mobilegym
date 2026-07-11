from __future__ import annotations

import uvicorn

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.testing.deterministic import (
    build_deterministic_executor_resolver,
    build_deterministic_target_registry,
)


def main() -> None:
    settings = PlatformSettings.from_env()
    database = Database(settings)
    app = create_app(
        settings,
        database=database,
        adapter_registry=build_deterministic_target_registry(),
        executor_resolver=build_deterministic_executor_resolver(
            database,
            settings,
            enabled=True,
        ),
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
