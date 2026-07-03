from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def main() -> None:
    settings = PlatformSettings.from_env()

    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
