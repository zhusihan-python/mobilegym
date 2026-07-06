"""VS-10: target-revision constraint engine for paired comparison policies.

Contract 1 (three-axis separation): the compare node config carries
``target_constraints: ["same_app","same_device","same_data"]``. This engine
evaluates those constraints against FROZEN ``TargetRevision.metadata`` (Contract
6) — NOT live target config. It is a pure function over two metadata dicts and a
list of constraint names, so it is reused by:

- compile-preview (advisory — Contract 3),
- create-run (authoritative gate — Contract 3).

The constraint names are deliberately an enumeration (not free-form predicates)
so the API surface is small and the violations carry structured codes the UI can
render (Contract 3 / Contract 4).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Constraint names (Contract 1).
CONSTRAINT_SAME_APP = "same_app"
CONSTRAINT_SAME_DEVICE = "same_device"
CONSTRAINT_SAME_DATA = "same_data"

DEFAULT_TARGET_CONSTRAINTS: tuple[str, ...] = (
    CONSTRAINT_SAME_APP,
    CONSTRAINT_SAME_DEVICE,
    CONSTRAINT_SAME_DATA,
)

_KNOWN_CONSTRAINTS = frozenset(DEFAULT_TARGET_CONSTRAINTS)


@dataclass(frozen=True)
class ConstraintViolation:
    """One structured constraint violation (Contract 3).

    ``code`` is a stable machine code (e.g. ``APP_VERSION_CODE_MISMATCH``);
    ``message`` is human-readable; ``details`` carries structured context
    (e.g. ``{"app_id": "wechat", "baseline": 80046, "candidate": 80047}``).
    """

    constraint: str
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint": self.constraint,
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


def evaluate_target_constraints(
    *,
    baseline_metadata: dict[str, Any] | None,
    candidate_metadata: dict[str, Any] | None,
    constraints: list[str] | tuple[str, ...] | None = None,
) -> list[ConstraintViolation]:
    """Evaluate the target constraints against two frozen metadata dicts.

    Args:
        baseline_metadata: the baseline lane's frozen TargetRevision.metadata.
        candidate_metadata: the candidate lane's frozen TargetRevision.metadata.
        constraints: the constraint axes to evaluate (Contract 1). ``None`` or
            empty falls back to all three axes (DEFAULT_TARGET_CONSTRAINTS).
            Unknown constraints are ignored (forward-compat).

    Returns:
        A list of ``ConstraintViolation``. An empty list means the lane pair
        satisfies every requested constraint. The order is deterministic:
        same_app violations (per app, in app-id order), then same_device, then
        same_data.
    """
    chosen = list(constraints) if constraints is not None else list(DEFAULT_TARGET_CONSTRAINTS)
    violations: list[ConstraintViolation] = []

    if CONSTRAINT_SAME_APP in chosen:
        violations.extend(_evaluate_same_app(baseline_metadata, candidate_metadata))
    if CONSTRAINT_SAME_DEVICE in chosen:
        violations.extend(_evaluate_same_device(baseline_metadata, candidate_metadata))
    if CONSTRAINT_SAME_DATA in chosen:
        violations.extend(_evaluate_same_data(baseline_metadata, candidate_metadata))

    return violations


# ---------------------------------------------------------------------------
# same_app (Contract 7: complete coverage)
# ---------------------------------------------------------------------------


def _evaluate_same_app(
    baseline_metadata: dict[str, Any] | None,
    candidate_metadata: dict[str, Any] | None,
) -> list[ConstraintViolation]:
    baseline_apps = _app_index(baseline_metadata)
    candidate_apps = _app_index(candidate_metadata)
    violations: list[ConstraintViolation] = []

    all_ids = sorted(set(baseline_apps) | set(candidate_apps))
    for app_id in all_ids:
        b = baseline_apps.get(app_id)
        c = candidate_apps.get(app_id)

        if b is None:
            violations.append(
                ConstraintViolation(
                    constraint=CONSTRAINT_SAME_APP,
                    code="APP_PRESENT_IN_CANDIDATE_ONLY",
                    message=f"App '{app_id}' is present in the candidate but missing from the baseline.",
                    details={"app_id": app_id, "side": "candidate"},
                )
            )
            continue
        if c is None:
            # Contract 7: missing app → violation.
            violations.append(
                ConstraintViolation(
                    constraint=CONSTRAINT_SAME_APP,
                    code="APP_MISSING_IN_CANDIDATE",
                    message=f"App '{app_id}' is present in the baseline but missing from the candidate.",
                    details={"app_id": app_id, "side": "candidate"},
                )
            )
            continue

        if b.get("packageName") != c.get("packageName"):
            violations.append(
                ConstraintViolation(
                    constraint=CONSTRAINT_SAME_APP,
                    code="APP_PACKAGE_NAME_MISMATCH",
                    message=f"App '{app_id}' packageName differs between baseline and candidate.",
                    details={
                        "app_id": app_id,
                        "baseline": b.get("packageName"),
                        "candidate": c.get("packageName"),
                    },
                )
            )

        if b.get("versionCode") != c.get("versionCode"):
            violations.append(
                ConstraintViolation(
                    constraint=CONSTRAINT_SAME_APP,
                    code="APP_VERSION_CODE_MISMATCH",
                    message=f"App '{app_id}' versionCode differs between baseline and candidate.",
                    details={
                        "app_id": app_id,
                        "baseline": b.get("versionCode"),
                        "candidate": c.get("versionCode"),
                    },
                )
            )

    return violations


def _app_index(metadata: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not metadata:
        return {}
    apps = metadata.get("apps")
    if not isinstance(apps, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for app in apps:
        if isinstance(app, dict) and app.get("id") is not None:
            index[str(app["id"])] = app
    return index


# ---------------------------------------------------------------------------
# same_device (Contract 6: device_profile)
# ---------------------------------------------------------------------------


def _evaluate_same_device(
    baseline_metadata: dict[str, Any] | None,
    candidate_metadata: dict[str, Any] | None,
) -> list[ConstraintViolation]:
    baseline_profile = _device_profile(baseline_metadata)
    candidate_profile = _device_profile(candidate_metadata)
    if baseline_profile == candidate_profile:
        return []
    return [
        ConstraintViolation(
            constraint=CONSTRAINT_SAME_DEVICE,
            code="DEVICE_PROFILE_MISMATCH",
            message="The baseline and candidate device profiles differ.",
            details={
                "baseline": baseline_profile,
                "candidate": candidate_profile,
            },
        )
    ]


def _device_profile(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    profile = metadata.get("device_profile")
    if not isinstance(profile, dict):
        return {}
    # Normalize: a profile is comparable on its declared fields.
    return {
        key: value
        for key, value in profile.items()
        if key
        in (
            "name",
            "viewport_width",
            "viewport_height",
            "physical_width",
            "physical_height",
            "device_scale_factor",
        )
    }


# ---------------------------------------------------------------------------
# same_data (Contract 6: data.revision)
# ---------------------------------------------------------------------------


def _evaluate_same_data(
    baseline_metadata: dict[str, Any] | None,
    candidate_metadata: dict[str, Any] | None,
) -> list[ConstraintViolation]:
    baseline_revision = _data_revision(baseline_metadata)
    candidate_revision = _data_revision(candidate_metadata)

    if baseline_revision is None or candidate_revision is None:
        return [
            ConstraintViolation(
                constraint=CONSTRAINT_SAME_DATA,
                code="DATA_REVISION_MISSING",
                message="The data revision is missing on one or both lanes.",
                details={
                    "baseline": baseline_revision,
                    "candidate": candidate_revision,
                },
            )
        ]
    if baseline_revision != candidate_revision:
        return [
            ConstraintViolation(
                constraint=CONSTRAINT_SAME_DATA,
                code="DATA_REVISION_MISMATCH",
                message="The baseline and candidate data revisions differ.",
                details={
                    "baseline": baseline_revision,
                    "candidate": candidate_revision,
                },
            )
        ]
    return []


def _data_revision(metadata: dict[str, Any] | None) -> str | None:
    if not metadata:
        return None
    data = metadata.get("data")
    if not isinstance(data, dict):
        return None
    revision = data.get("revision")
    return str(revision) if revision is not None else None
