import json

import pytest

from test_platform.domain.targets import (
    TargetDomainError,
    canonical_metadata_hash,
    redact_target_config,
    validate_sim_metadata,
)


def _valid_metadata():
    return {
        "schemaVersion": 1,
        "simulator": {
            "product": "mobile-gym",
            "version": "0.1.0",
            "buildId": "build-vs02",
        },
        "apps": [
            {
                "id": "wechat",
                "packageName": "com.tencent.mm",
                "displayName": "WeChat",
                "version": "8.0.46",
                "versionCode": 80046,
                "type": "plugin",
            }
        ],
        "data": {"revision": "seed-v1", "bundleHash": "data-sha"},
        "capabilities": ["sim.metadata.v1"],
    }


def test_target_public_config_redacts_secret_references():
    config = {
        "kind": "simulator",
        "connection": {
            "env_url": "http://127.0.0.1:5173",
            "proxy_secret_ref": "secret://mobilegym/proxy-token",
        },
        "device_profile": {
            "name": "Pixel 7",
            "viewport_width": 393,
            "viewport_height": 852,
            "physical_width": 1080,
            "physical_height": 2400,
            "device_scale_factor": 2.75,
        },
        "runtime": {"locale": "en-US"},
        "labels": {"lane": "local"},
    }

    public_config = redact_target_config(config)

    assert public_config["connection"] == {
        "env_url": "http://127.0.0.1:5173",
        "proxy_configured": True,
    }
    assert "proxy_secret_ref" not in json.dumps(public_config)
    assert "secret://mobilegym/proxy-token" not in json.dumps(public_config)


def test_invalid_sim_metadata_raises_structured_target_error():
    metadata = _valid_metadata()
    del metadata["apps"][0]["packageName"]

    with pytest.raises(TargetDomainError) as exc_info:
        validate_sim_metadata(metadata)

    assert exc_info.value.code == "TARGET_METADATA_INVALID"


def test_metadata_hash_ignores_resolution_time():
    left = _valid_metadata()
    right = _valid_metadata()
    left["resolved_at"] = "2026-07-03T00:00:00.000Z"
    right["resolved_at"] = "2026-07-03T00:01:00.000Z"

    assert canonical_metadata_hash(left) == canonical_metadata_hash(right)
