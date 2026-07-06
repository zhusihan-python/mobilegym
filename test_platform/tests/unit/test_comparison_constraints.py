"""VS-10 Block A: target-revision constraint engine (pure domain).

Contract 1 (three-axis separation): the compare node config carries
``target_constraints: ["same_app","same_device","same_data"]``. This engine
evaluates those constraints against FROZEN TargetRevision.metadata (Contract 6),
NOT live target config.

Contract 7 (same-App complete coverage): missing app / versionCode different /
packageName different → violation.

Required failing behaviors (DEVELOPMENT_PLAN):
- same-App policy rejects mismatched App revisions;
- same-device policy rejects mismatched profile hashes;
- same-data policy rejects missing or unequal revisions.
"""
from __future__ import annotations

import pytest

from test_platform.domain.comparison_constraints import (
    ConstraintViolation,
    evaluate_target_constraints,
)


def _profile(name: str = "Pixel 7") -> dict:
    return {
        "name": name,
        "viewport_width": 393,
        "viewport_height": 852,
        "physical_width": 1080,
        "physical_height": 2400,
        "device_scale_factor": 2.75,
    }


def _app(
    app_id: str = "wechat",
    *,
    version: str = "8.0.46",
    version_code: int = 80046,
    package: str = "com.tencent.mm",
) -> dict:
    return {
        "id": app_id,
        "packageName": package,
        "displayName": app_id,
        "version": version,
        "versionCode": version_code,
        "type": "plugin",
    }


def _metadata(
    *,
    apps: list[dict],
    data_revision: str | None = "seed-v1",
    device_profile: dict | None = None,
) -> dict:
    return {
        "schema_version": 1,
        "apps": apps,
        "data": {"revision": data_revision},
        "device_profile": device_profile or _profile(),
    }


# ---------------------------------------------------------------------------
# same_app
# ---------------------------------------------------------------------------


def test_same_app_passes_when_app_revisions_match():
    baseline = _metadata(apps=[_app()])
    candidate = _metadata(apps=[_app()])

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_app"],
    )

    assert violations == []


def test_same_app_rejects_versionCode_mismatch():
    baseline = _metadata(apps=[_app(version_code=80046)])
    candidate = _metadata(apps=[_app(version_code=80047)])

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_app"],
    )

    assert len(violations) == 1
    assert violations[0].constraint == "same_app"
    assert violations[0].code  # non-empty specific code


def test_same_app_rejects_packageName_mismatch():
    baseline = _metadata(apps=[_app(package="com.tencent.mm")])
    candidate = _metadata(apps=[_app(package="com.tencent.mm.beta")])

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_app"],
    )

    assert len(violations) == 1
    assert violations[0].constraint == "same_app"


def test_same_app_rejects_missing_app_in_candidate():
    # Contract 7: missing app → violation.
    baseline = _metadata(apps=[_app(app_id="wechat"), _app(app_id="contacts")])
    candidate = _metadata(apps=[_app(app_id="wechat")])  # contacts removed

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_app"],
    )

    assert len(violations) == 1
    assert violations[0].constraint == "same_app"


def test_same_app_reports_per_app_violations():
    baseline = _metadata(
        apps=[_app(app_id="wechat", version_code=1), _app(app_id="contacts", version_code=5)]
    )
    candidate = _metadata(
        apps=[_app(app_id="wechat", version_code=2), _app(app_id="contacts", version_code=6)]
    )

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_app"],
    )

    # One violation per mismatched app.
    assert len(violations) == 2


# ---------------------------------------------------------------------------
# same_device
# ---------------------------------------------------------------------------


def test_same_device_passes_when_profiles_match():
    profile = _profile()
    baseline = _metadata(apps=[_app()], device_profile=profile)
    candidate = _metadata(apps=[_app()], device_profile=dict(profile))

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_device"],
    )

    assert violations == []


def test_same_device_rejects_mismatched_profile():
    baseline = _metadata(apps=[_app()], device_profile=_profile("Pixel 7"))
    candidate = _metadata(apps=[_app()], device_profile=_profile("Pixel 8"))

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_device"],
    )

    assert len(violations) == 1
    assert violations[0].constraint == "same_device"


def test_same_device_rejects_mismatched_viewport():
    profile_b = _profile()
    profile_c = _profile()
    profile_c["viewport_height"] = 999
    baseline = _metadata(apps=[_app()], device_profile=profile_b)
    candidate = _metadata(apps=[_app()], device_profile=profile_c)

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_device"],
    )

    assert len(violations) == 1


# ---------------------------------------------------------------------------
# same_data
# ---------------------------------------------------------------------------


def test_same_data_passes_when_revisions_match():
    baseline = _metadata(apps=[_app()], data_revision="seed-v1")
    candidate = _metadata(apps=[_app()], data_revision="seed-v1")

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_data"],
    )

    assert violations == []


def test_same_data_rejects_unequal_revision():
    baseline = _metadata(apps=[_app()], data_revision="seed-v1")
    candidate = _metadata(apps=[_app()], data_revision="seed-v2")

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_data"],
    )

    assert len(violations) == 1
    assert violations[0].constraint == "same_data"


def test_same_data_rejects_missing_revision_in_candidate():
    baseline = _metadata(apps=[_app()], data_revision="seed-v1")
    candidate = _metadata(apps=[_app()], data_revision=None)

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_data"],
    )

    assert len(violations) == 1


def test_same_data_rejects_missing_revision_in_baseline():
    baseline = _metadata(apps=[_app()], data_revision=None)
    candidate = _metadata(apps=[_app()], data_revision="seed-v1")

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_data"],
    )

    assert len(violations) == 1


# ---------------------------------------------------------------------------
# constraint selection + defaults
# ---------------------------------------------------------------------------


def test_no_constraints_returns_no_violations_even_for_very_different_metadata():
    baseline = _metadata(apps=[_app(version_code=1)], data_revision="v1")
    candidate = _metadata(apps=[_app(version_code=999)], data_revision="v2")

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=[],
    )

    assert violations == []


def test_default_constraints_evaluates_all_three_axes():
    baseline = _metadata(
        apps=[_app(version_code=1)], data_revision="v1", device_profile=_profile("A")
    )
    candidate = _metadata(
        apps=[_app(version_code=2)], data_revision="v2", device_profile=_profile("B")
    )

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
    )

    axes = {v.constraint for v in violations}
    assert axes == {"same_app", "same_device", "same_data"}


def test_violation_serializes_to_dict():
    baseline = _metadata(apps=[_app(version_code=1)])
    candidate = _metadata(apps=[_app(version_code=2)])

    violations = evaluate_target_constraints(
        baseline_metadata=baseline,
        candidate_metadata=candidate,
        constraints=["same_app"],
    )

    payload = violations[0].to_dict()
    assert set(payload.keys()) >= {"constraint", "code", "message", "details"}
