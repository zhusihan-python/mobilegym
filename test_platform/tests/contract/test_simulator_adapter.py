import pytest

from test_platform.adapters.targets import SimulatorAdapter


def _config():
    return {
        "kind": "simulator",
        "connection": {"env_url": "http://127.0.0.1:5173"},
        "device_profile": {
            "name": "Pixel 7",
            "viewport_width": 393,
            "viewport_height": 852,
            "physical_width": 1080,
            "physical_height": 2400,
            "device_scale_factor": 2.75,
        },
        "runtime": {"locale": "en-US"},
        "labels": {},
    }


def _metadata():
    return {
        "schemaVersion": 1,
        "simulator": {
            "product": "mobile-gym",
            "version": "0.1.0",
            "buildId": "build-vs02",
        },
        "apps": [
            {
                "id": "settings",
                "packageName": "com.android.settings",
                "displayName": "Settings",
                "version": "14.0.0",
                "versionCode": 140000,
                "type": "system",
            }
        ],
        "data": {"revision": None, "bundleHash": None},
        "capabilities": ["sim.metadata.v1"],
    }


class StaticProbe:
    def __init__(self, metadata):
        self.metadata = metadata
        self.calls = []

    def read_metadata(self, env_url, timeout_seconds):
        self.calls.append((env_url, timeout_seconds))
        return self.metadata


def test_simulator_adapter_reads_metadata_through_injected_probe():
    probe = StaticProbe(_metadata())
    adapter = SimulatorAdapter(metadata_probe=probe, timeout_seconds=2.5)

    result = adapter.check_health(_config())

    assert result["healthy"] is True
    assert result["executable"] is True
    assert result["metadata"]["simulator"]["buildId"] == "build-vs02"
    assert result["warnings"] == ["Simulator data revision is not pinned."]
    assert probe.calls == [("http://127.0.0.1:5173", 2.5)]


@pytest.mark.parametrize("env_url", ["ftp://127.0.0.1/app", "not a url"])
def test_simulator_adapter_rejects_invalid_endpoint_urls(env_url):
    adapter = SimulatorAdapter(metadata_probe=StaticProbe(_metadata()))
    config = _config()
    config["connection"]["env_url"] = env_url

    result = adapter.check_health(config)

    assert result["healthy"] is False
    assert result["executable"] is False
    assert result["error"]["code"] == "TARGET_ENDPOINT_INVALID"
