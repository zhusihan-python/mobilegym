from __future__ import annotations

import os
from typing import Any, Protocol
from urllib.parse import urlparse

from test_platform.domain.targets import TargetDomainError, validate_sim_metadata


class MetadataProbe(Protocol):
    def read_metadata(self, env_url: str, timeout_seconds: float) -> dict[str, Any]:
        ...


class BrowserMetadataProbe:
    def read_metadata(self, env_url: str, timeout_seconds: float) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise TargetDomainError(
                "TARGET_METADATA_PROBE_UNAVAILABLE",
                "Playwright is required to probe simulator metadata.",
                status_code=503,
            ) from exc

        with sync_playwright() as playwright:
            executable_path = os.environ.get("TEST_PLATFORM_CHROMIUM_EXECUTABLE")
            launch_options = {"executable_path": executable_path} if executable_path else {}
            browser = playwright.chromium.launch(**launch_options)
            try:
                page = browser.new_page()
                page.goto(
                    env_url,
                    wait_until="domcontentloaded",
                    timeout=timeout_seconds * 1000,
                )
                page.wait_for_function(
                    "() => Boolean(window.__SIM__ && window.__SIM__.getMetadata)",
                    timeout=timeout_seconds * 1000,
                )
                metadata = page.evaluate("() => window.__SIM__.getMetadata()")
            finally:
                browser.close()

        if not isinstance(metadata, dict):
            raise TargetDomainError(
                "TARGET_METADATA_INVALID",
                "Simulator metadata probe returned a non-object payload.",
            )
        return metadata


class SimulatorAdapter:
    def __init__(
        self,
        *,
        metadata_probe: MetadataProbe | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.metadata_probe = metadata_probe or BrowserMetadataProbe()
        self.timeout_seconds = timeout_seconds

    def check_health(self, config: dict[str, Any]) -> dict[str, Any]:
        env_url = str(config.get("connection", {}).get("env_url", ""))
        if not _valid_endpoint_url(env_url):
            return _adapter_error(
                "TARGET_ENDPOINT_INVALID",
                "Simulator endpoint URL must be an http(s) URL.",
            )

        try:
            metadata = self.metadata_probe.read_metadata(env_url, self.timeout_seconds)
            validate_sim_metadata(metadata)
        except TargetDomainError as exc:
            if exc.code == "TARGET_METADATA_INVALID":
                raise
            return _adapter_error(exc.code, exc.message, status_code=exc.status_code)
        except Exception as exc:
            return _adapter_error(
                "TARGET_METADATA_UNREACHABLE",
                f"Simulator metadata could not be read: {exc}",
            )

        return {
            "healthy": True,
            "executable": True,
            "metadata": metadata,
            "warnings": _metadata_warnings(metadata),
            "error": None,
        }


class RealDeviceAdapter:
    def check_health(self, config: dict[str, Any]) -> dict[str, Any]:
        return {
            "healthy": False,
            "executable": False,
            "metadata": None,
            "warnings": [],
            "error": {
                "code": "TARGET_KIND_NOT_EXECUTABLE",
                "message": "Real-device targets can be stored but are not executable in VS-02.",
            },
        }


class TargetAdapterRegistry:
    def __init__(
        self,
        *,
        simulator_adapter: SimulatorAdapter | None = None,
        real_device_adapter: RealDeviceAdapter | None = None,
    ) -> None:
        self.simulator_adapter = simulator_adapter or SimulatorAdapter()
        self.real_device_adapter = real_device_adapter or RealDeviceAdapter()

    def check_health(self, config: dict[str, Any]) -> dict[str, Any]:
        if config.get("kind") == "real_device":
            return self.real_device_adapter.check_health(config)
        return self.simulator_adapter.check_health(config)


def _metadata_warnings(metadata: dict[str, Any]) -> list[str]:
    data = metadata.get("data")
    if isinstance(data, dict) and data.get("revision") is None:
        return ["Simulator data revision is not pinned."]
    return []


def _valid_endpoint_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _adapter_error(code: str, message: str, *, status_code: int = 400) -> dict[str, Any]:
    return {
        "healthy": False,
        "executable": False,
        "metadata": None,
        "warnings": [],
        "error": {
            "code": code,
            "message": message,
            "status_code": status_code,
        },
    }
