"""VS-09 Block A: state projection strips volatile paths + stable hash."""
from __future__ import annotations

from test_platform.domain.state_projection import (
    DEFAULT_IGNORE_PATHS,
    projection_hash_v1,
    project_state,
)


def _sample_state() -> dict:
    return {
        "os": {
            "time": {"mode": "fixed", "value": 12345},
            "activeAppId": "wechat",
            "runningApps": ["wechat"],
            "tasks": [{"id": "t1"}],
            "services": {"keyboard": {"visible": True}},
        },
        "apps": {
            "wechat": {"contacts": [{"name": "Alice"}], "_temp": {"cache": "x"}},
            "answer_sheet": {"question": "q"},
        },
    }


def test_project_state_strips_default_volatile_paths():
    projected = project_state(_sample_state())
    # os.time, os.activeAppId, os.runningApps, os.tasks, keyboard, answer_sheet, _temp stripped.
    paths = set(projected.keys())
    assert not any(p.startswith("os.time") for p in paths)
    assert not any(p.startswith("os.activeAppId") for p in paths)
    assert not any(p.startswith("os.runningApps") for p in paths)
    assert not any(p.startswith("os.tasks") for p in paths)
    assert not any(p.startswith("os.services.keyboard") for p in paths)
    assert not any(p.startswith("apps.answer_sheet") for p in paths)
    assert not any("._temp" in p for p in paths)
    # Stable app data retained.
    assert "apps.wechat.contacts[0].name" in paths


def test_projection_hash_is_stable_for_same_state():
    h1 = projection_hash_v1(_sample_state())
    h2 = projection_hash_v1(_sample_state())
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_projection_hash_ignores_volatile_changes():
    """Changing only volatile paths (time, activeApp, keyboard) does NOT change hash."""
    base = _sample_state()
    changed = _sample_state()
    changed["os"]["time"]["value"] = 99999
    changed["os"]["activeAppId"] = "contacts"
    changed["os"]["runningApps"] = ["contacts", "wechat"]
    changed["os"]["services"]["keyboard"]["visible"] = False

    assert projection_hash_v1(base) == projection_hash_v1(changed)


def test_projection_hash_detects_real_state_changes():
    """Changing stable app data DOES change hash."""
    base = _sample_state()
    changed = _sample_state()
    changed["apps"]["wechat"]["contacts"][0]["name"] = "Bob"
    assert projection_hash_v1(base) != projection_hash_v1(changed)


def test_projection_hash_with_custom_ignore_paths():
    """Caller can extend ignore paths (e.g. task-specific always_ignore)."""
    state = {"apps": {"foo": {"bar": 1, "baz": 2}}}
    full = projection_hash_v1(state, ignore_paths=[])
    stripped = projection_hash_v1(state, ignore_paths=["apps.foo.bar"])
    assert full != stripped


def test_two_lanes_with_identical_fixture_have_equal_projection_hash():
    """The core VS-09 invariant: baseline and candidate lanes that honored the
    same prepared fixture must produce equal projection hashes."""
    lane_a = _sample_state()
    lane_b = _sample_state()
    # Lane B has different volatile state (its own clock, active app, etc.).
    lane_b["os"]["time"]["value"] = 67890
    lane_b["os"]["activeAppId"] = "contacts"
    assert projection_hash_v1(lane_a) == projection_hash_v1(lane_b)
