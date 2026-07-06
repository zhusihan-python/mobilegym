from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def validate_runtime_settings(settings: PlatformSettings) -> None:
    if settings.allow_non_loopback:
        return
    if _is_loopback_host(settings.host):
        return
    raise RuntimeError(
        "Binding the Test Platform to a non-loopback interface requires "
        "TEST_PLATFORM_ALLOW_NON_LOOPBACK=true."
    )


def main() -> None:
    settings = PlatformSettings.from_env()
    validate_runtime_settings(settings)

    import uvicorn

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="info",
    )


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip("[]").lower()
    if normalized == "localhost":
        return True
    if normalized == "::1":
        return True
    if normalized.startswith("127."):
        return True
    return False


if __name__ == "__main__":
    main()
