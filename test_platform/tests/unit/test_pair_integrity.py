"""VS-10 Block A: pair integrity + projection profiles (pure domain).

Contract 4 (path-level pair integrity): the integrity report carries a
path-level diff between baseline and candidate projected states, not just a
hash.

Contract 5 (task projection semantics, version-direction-aware):
- relevant paths = task apps full state + OS build/telephony/settings/hardware/
  permissions/providers/**time-mode/location-mode**;
- **version direction binding**: candidate-only path tolerated ONLY when the
  candidate's task app versionCode is HIGHER; versionCode lower or unknown →
  violation; baseline-only path → violation (feature removed);
- **NOT using expected_changes as primary rule**;
- **projection profile separation**: strict_snapshot and task_projection each
  have their own ignore set. strict uses minimal ignore (only volatile runtime
  like timestamps); task_projection uses task-app-aware ignore. time-mode
  different → violation; timestamp different → NOT violation.

Required failing behaviors (DEVELOPMENT_PLAN):
- strict snapshot detects initial-state mismatch;
- task projection tolerates unrelated version-added fields.
"""
from __future__ import annotations

import pytest

from test_platform.domain.pair_integrity import (
    INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
    INITIAL_STATE_POLICY_TASK_PROJECTION,
    IntegrityReport,
    compare_paired_states,
    projection_profile,
)
from test_platform.domain.state_projection import projection_hash_v1


def _task_app_state(choice: str = "initial", **extra) -> dict:
    state = {"apps": {"fake": {"choice": choice}}, "os": {"time": {"mode": "fixed"}}}
    state["apps"]["fake"].update(extra)
    return state


# ---------------------------------------------------------------------------
# projection profiles
# ---------------------------------------------------------------------------


def test_projection_profile_strict_keeps_app_state():
    profile = projection_profile(INITIAL_STATE_POLICY_STRICT_SNAPSHOT)
    projected = profile.project(_task_app_state(choice="x"))
    # Strict keeps the app state — it only strips volatile runtime.
    assert "apps.fake.choice" in projected


def test_projection_profile_task_projection_keeps_app_state():
    profile = projection_profile(INITIAL_STATE_POLICY_TASK_PROJECTION)
    projected = profile.project(_task_app_state(choice="x"))
    assert "apps.fake.choice" in projected


def test_strict_snapshot_detects_time_value_change_as_non_violation():
    """Strict snapshot ignores the time VALUE (volatile) but time-mode is a
    real configuration change."""
    baseline = _task_app_state()
    candidate = _task_app_state()
    candidate["os"]["time"]["value"] = "2026-07-05T00:00:00.000Z"  # timestamp drift

    report = compare_paired_states(
        baseline_state=baseline,
        candidate_state=candidate,
        policy=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
        baseline_apps=[],
        candidate_apps=[],
    )

    # timestamp difference is NOT a violation.
    assert report.status == "OK"


def test_strict_snapshot_detects_real_initial_state_mismatch():
    baseline = _task_app_state(choice="initial")
    candidate = _task_app_state(choice="tampered")  # the fixture was tampered

    report = compare_paired_states(
        baseline_state=baseline,
        candidate_state=candidate,
        policy=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
        baseline_apps=[],
        candidate_apps=[],
    )

    assert report.is_violation
    assert report.status == "projection_mismatch"
    # Path-level diff present (Contract 4).
    assert any("apps.fake.choice" in d.path for d in report.path_diffs)


# ---------------------------------------------------------------------------
# time-mode / location-mode always matter
# ---------------------------------------------------------------------------


def test_time_mode_difference_is_violation_in_strict():
    baseline = {"apps": {"fake": {}}, "os": {"time": {"mode": "fixed"}}}
    candidate = {"apps": {"fake": {}}, "os": {"time": {"mode": "real"}}}

    report = compare_paired_states(
        baseline_state=baseline,
        candidate_state=candidate,
        policy=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
        baseline_apps=[],
        candidate_apps=[],
    )

    assert report.is_violation


def test_time_mode_difference_is_violation_in_task_projection():
    baseline = {"apps": {"fake": {}}, "os": {"time": {"mode": "fixed"}}}
    candidate = {"apps": {"fake": {}}, "os": {"time": {"mode": "real"}}}

    report = compare_paired_states(
        baseline_state=baseline,
        candidate_state=candidate,
        policy=INITIAL_STATE_POLICY_TASK_PROJECTION,
        baseline_apps=[],
        candidate_apps=[],
    )

    assert report.is_violation


# ---------------------------------------------------------------------------
# version-direction-aware tolerance (task projection)
# ---------------------------------------------------------------------------


def _app(app_id: str = "wechat", *, version_code: int = 80046) -> dict:
    return {
        "id": app_id,
        "packageName": "com.tencent.mm",
        "displayName": app_id,
        "version": "8.0.46",
        "versionCode": version_code,
        "type": "plugin",
    }


def test_task_projection_tolerates_candidate_only_path_when_version_higher():
    """Contract 5: candidate adds a field that the baseline does not have.
    Tolerated ONLY when the candidate's app versionCode is HIGHER than the
    baseline's."""
    baseline_state = {"apps": {"wechat": {"version": "8.0.46"}}, "os": {}}
    candidate_state = {"apps": {"wechat": {"version": "8.0.46", "new_field": 1}}, "os": {}}

    report = compare_paired_states(
        baseline_state=baseline_state,
        candidate_state=candidate_state,
        policy=INITIAL_STATE_POLICY_TASK_PROJECTION,
        baseline_apps=[_app(version_code=80046)],
        candidate_apps=[_app(version_code=80047)],  # higher
    )

    assert not report.is_violation


def test_task_projection_rejects_candidate_only_path_when_version_lower():
    baseline_state = {"apps": {"wechat": {"version": "8.0.46"}}, "os": {}}
    candidate_state = {"apps": {"wechat": {"version": "8.0.46", "new_field": 1}}, "os": {}}

    report = compare_paired_states(
        baseline_state=baseline_state,
        candidate_state=candidate_state,
        policy=INITIAL_STATE_POLICY_TASK_PROJECTION,
        baseline_apps=[_app(version_code=80047)],  # baseline higher
        candidate_apps=[_app(version_code=80046)],
    )

    assert report.is_violation


def test_task_projection_rejects_candidate_only_path_when_version_unknown():
    baseline_state = {"apps": {"wechat": {"version": "8.0.46"}}, "os": {}}
    candidate_state = {"apps": {"wechat": {"version": "8.0.46", "new_field": 1}}, "os": {}}

    report = compare_paired_states(
        baseline_state=baseline_state,
        candidate_state=candidate_state,
        policy=INITIAL_STATE_POLICY_TASK_PROJECTION,
        baseline_apps=[_app(version_code=80046)],
        candidate_apps=[],  # unknown → cannot prove upgrade
    )

    assert report.is_violation


def test_task_projection_rejects_baseline_only_path_feature_removed():
    """Contract 5: baseline-only path → violation (a feature was removed)."""
    baseline_state = {"apps": {"wechat": {"version": "8.0.46", "feature": 1}}, "os": {}}
    candidate_state = {"apps": {"wechat": {"version": "8.0.46"}}, "os": {}}

    report = compare_paired_states(
        baseline_state=baseline_state,
        candidate_state=candidate_state,
        policy=INITIAL_STATE_POLICY_TASK_PROJECTION,
        baseline_apps=[_app(version_code=80046)],
        candidate_apps=[_app(version_code=80047)],  # higher — but removal is still a violation
    )

    assert report.is_violation


def test_task_projection_rejects_value_difference_between_versions():
    """Even when candidate version is higher, a changed VALUE (not just an
    added key) in an existing path is a violation — the fixture diverged."""
    baseline_state = {"apps": {"wechat": {"choice": "initial"}}, "os": {}}
    candidate_state = {"apps": {"wechat": {"choice": "tampered"}}, "os": {}}

    report = compare_paired_states(
        baseline_state=baseline_state,
        candidate_state=candidate_state,
        policy=INITIAL_STATE_POLICY_TASK_PROJECTION,
        baseline_apps=[_app(version_code=80046)],
        candidate_apps=[_app(version_code=80047)],
    )

    assert report.is_violation


# ---------------------------------------------------------------------------
# IntegrityReport serialization
# ---------------------------------------------------------------------------


def test_integrity_report_to_dict_round_trip():
    baseline = _task_app_state(choice="initial")
    candidate = _task_app_state(choice="tampered")

    report = compare_paired_states(
        baseline_state=baseline,
        candidate_state=candidate,
        policy=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
        baseline_apps=[],
        candidate_apps=[],
    )

    payload = report.to_dict()
    assert payload["status"] == "projection_mismatch"
    assert "path_diffs" in payload
    assert "baseline_projection_hash" in payload
    assert "candidate_projection_hash" in payload


def test_ok_report_has_no_path_diffs():
    report = compare_paired_states(
        baseline_state=_task_app_state(),
        candidate_state=_task_app_state(),
        policy=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
        baseline_apps=[],
        candidate_apps=[],
    )

    assert not report.is_violation
    assert report.path_diffs == []
    assert report.to_dict()["status"] == "OK"


# ---------------------------------------------------------------------------
# OS configuration paths matter in both profiles (telephony/settings/etc.)
# ---------------------------------------------------------------------------


def test_os_settings_difference_is_violation_in_strict():
    baseline = {"apps": {"fake": {}}, "os": {"settings": {"airplane_mode": False}}}
    candidate = {"apps": {"fake": {}}, "os": {"settings": {"airplane_mode": True}}}

    report = compare_paired_states(
        baseline_state=baseline,
        candidate_state=candidate,
        policy=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
        baseline_apps=[],
        candidate_apps=[],
    )

    assert report.is_violation


def test_os_providers_contacts_volatile_path_ignored():
    """DEFAULT_IGNORE_PATHS strips contacts updatedAt/lastContactedAt — those
    must not surface as a violation even in strict mode."""
    baseline = {
        "apps": {"fake": {}},
        "os": {
            "providers": {"contacts": {"contacts": [{"id": 1, "updatedAt": 100}]}}
        },
    }
    candidate = {
        "apps": {"fake": {}},
        "os": {
            "providers": {"contacts": {"contacts": [{"id": 1, "updatedAt": 200}]}}
        },
    }

    report = compare_paired_states(
        baseline_state=baseline,
        candidate_state=candidate,
        policy=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
        baseline_apps=[],
        candidate_apps=[],
    )

    assert not report.is_violation


def test_none_value_in_baseline_not_treated_as_absent():
    """P1 fix: a real ``None`` value in baseline must NOT be treated as an absent
    key (which would be misjudged as a candidate-only addition). ``None → value``
    is a VALUE CHANGE, not an addition, and must NOT be tolerated even if
    candidate versionCode is higher."""
    baseline_state = {"apps": {"fake": {"choice": None}}}
    candidate_state = {"apps": {"fake": {"choice": "tampered"}}}
    baseline_apps = [{"id": "fake", "versionCode": 1}]
    candidate_apps = [{"id": "fake", "versionCode": 2}]  # higher

    report = compare_paired_states(
        baseline_state=baseline_state,
        candidate_state=candidate_state,
        policy=INITIAL_STATE_POLICY_TASK_PROJECTION,
        baseline_apps=baseline_apps,
        candidate_apps=candidate_apps,
        task_app_ids={"fake"},
    )
    # Must be a mismatch — None→"tampered" is NOT a tolerated addition.
    assert report.is_violation, (
        "None→value change was misjudged as tolerated candidate-only addition"
    )
    # The diff should be a value change, not an addition.
    assert len(report.path_diffs) == 1
    diff = report.path_diffs[0]
    assert not diff.baseline_absent, "real None value mis-flagged as absent"
    assert diff.baseline is None  # the actual value
    assert diff.candidate == "tampered"
