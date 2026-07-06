"""VS-10: pair integrity + projection profiles (path-level diff).

Contract 4 (path-level pair integrity): the integrity report carries a
path-level diff between the baseline and candidate projected states — not just a
hash. ``IntegrityReport.path_diffs`` is a list of ``PathDiff`` records
(``path``, ``baseline``, ``candidate``) so the UI can render exactly which paths
diverged.

Contract 5 (task projection semantics, version-direction-aware):
- relevant paths = task apps full state + OS build/telephony/settings/hardware/
  permissions/providers/**time-mode/location-mode**;
- **version direction binding**: a candidate-only path is tolerated ONLY when
  the candidate's task app versionCode is HIGHER than the baseline's; versionCode
  lower or unknown → violation; baseline-only path → violation (feature removed);
- **NOT using expected_changes as primary rule**;
- **projection profile separation**: ``strict_snapshot`` and ``task_projection``
  each have their own ignore set. ``strict`` uses a minimal ignore set (only
  volatile runtime like timestamps); ``task_projection`` uses the task-app-aware
  ignore set from ``state_projection``. ``time-mode`` different → violation;
  timestamp different → NOT violation.

Profiles are objects (``ProjectionProfile``) so the executor can capture the
projected dict once, hash it, and reuse the same projected dict for the diff.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from test_platform.domain.state_projection import (
    DEFAULT_IGNORE_PATHS,
    project_state,
)

# Policy names (Contract 1).
INITIAL_STATE_POLICY_STRICT_SNAPSHOT = "strict_snapshot"
INITIAL_STATE_POLICY_TASK_PROJECTION = "task_projection"

DEFAULT_INITIAL_STATE_POLICY = INITIAL_STATE_POLICY_TASK_PROJECTION

# Integrity statuses (mirrors pair_classification.IntegrityStatus but kept
# independent of that module so this domain layer has no upward dependency).
INTEGRITY_OK = "OK"
INTEGRITY_PROJECTION_MISMATCH = "projection_mismatch"


# Volatile timestamp / runtime-cache paths ignored in BOTH profiles (Contract 5).
# The time VALUE is volatile; the time MODE is configuration (kept). Contacts
# updatedAt/lastContactedAt are volatile timestamps. apps.*._temp is a volatile
# runtime cache. These are stripped in strict_snapshot (minimal ignore) AND in
# task_projection (which layers the task-app-aware set on top).
_VOLATILE_TIMESTAMP_PATHS: list[str] = [
    # The time VALUE is volatile; time MODE is configuration (kept).
    "os.time.value",
    "os.providers.contacts.contacts[].updatedAt",
    "os.providers.contacts.contacts[].lastContactedAt",
    "apps.*._temp",
]


@dataclass(frozen=True)
class PathDiff:
    """One divergent leaf path between baseline and candidate projections."""

    path: str
    baseline: Any
    candidate: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "baseline": self.baseline, "candidate": self.candidate}


@dataclass(frozen=True)
class IntegrityReport:
    """The complete integrity report for a paired lane comparison.

    ``status`` is ``"OK"`` or ``"projection_mismatch"``. ``path_diffs`` carries
    the path-level diff (Contract 4). ``baseline_projection_hash`` /
    ``candidate_projection_hash`` are the per-lane projected-state digests.
    """

    status: str
    reason: str
    path_diffs: list[PathDiff] = field(default_factory=list)
    baseline_projection_hash: str | None = None
    candidate_projection_hash: str | None = None

    @property
    def is_violation(self) -> bool:
        return self.status != INTEGRITY_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "path_diffs": [d.to_dict() for d in self.path_diffs],
            "baseline_projection_hash": self.baseline_projection_hash,
            "candidate_projection_hash": self.candidate_projection_hash,
        }


@dataclass(frozen=True)
class ProjectionProfile:
    """One projection profile (Contract 5): a name + an ignore-path set + the
    projection function. Profiles are frozen so they can be reused safely across
    lanes (the projected dict is a fresh value each call)."""

    name: str
    ignore_paths: list[str]

    def project(self, state: dict[str, Any]) -> dict[str, Any]:
        """Return a flat ``{path: value}`` projection of *state*."""
        return project_state(state, ignore_paths=self.ignore_paths)


def projection_profile(policy: str) -> ProjectionProfile:
    """Return the ``ProjectionProfile`` for *policy* (Contract 5).

    ``strict_snapshot`` uses a MINIMAL ignore set: only truly volatile runtime
    paths (timestamp values) are stripped. Everything else (app state, OS
    settings, time-mode, location-mode) is retained and compared.

    ``task_projection`` uses the DEFAULT_IGNORE_PATHS from
    ``state_projection`` (the task-app-aware volatile set) PLUS the strict
    extras, so it tolerates more per-lane drift while still flagging real config
    changes via the version-direction-aware diff.
    """
    if policy == INITIAL_STATE_POLICY_STRICT_SNAPSHOT:
        # Minimal ignore: only volatile timestamp/runtime-cache paths (the time
        # MODE, location MODE, app state, OS settings all stay and are compared).
        return ProjectionProfile(
            name=INITIAL_STATE_POLICY_STRICT_SNAPSHOT,
            ignore_paths=list(_VOLATILE_TIMESTAMP_PATHS),
        )
    # task_projection (default): task-app-aware ignore set (DEFAULT_IGNORE_PATHS
    # strips volatile services, running apps, keyboard, alarm manager, etc.) but
    # with ``os.time`` NARROWED to ``os.time.value`` so the time MODE is still
    # compared (Contract 5: time-mode different → violation). Layer the volatile
    # timestamp/cache paths on top.
    narrowed = [p for p in DEFAULT_IGNORE_PATHS if p != "os.time"]
    ignore_paths = narrowed + list(_VOLATILE_TIMESTAMP_PATHS)
    return ProjectionProfile(
        name=INITIAL_STATE_POLICY_TASK_PROJECTION,
        ignore_paths=ignore_paths,
    )


def compare_paired_states(
    *,
    baseline_state: dict[str, Any] | None,
    candidate_state: dict[str, Any] | None,
    policy: str,
    baseline_apps: list[dict[str, Any]],
    candidate_apps: list[dict[str, Any]],
) -> IntegrityReport:
    """Compare two lane states under *policy* and return an IntegrityReport.

    The comparison is path-level (Contract 4): both states are projected with
    the profile's ignore set, then diffed leaf-by-leaf. The version-direction
    binding (Contract 5) is then applied to the resulting diffs:

    - a candidate-only path (an addition) is tolerated ONLY when the candidate's
      task-app versionCode is HIGHER than the baseline's matching app's;
    - a baseline-only path (a removal) is ALWAYS a violation (feature removed);
    - a value difference on a path present in BOTH is always a violation.

    Args:
        baseline_state: the baseline lane's captured initial state.
        candidate_state: the candidate lane's captured initial state.
        policy: one of the INITIAL_STATE_POLICY_* constants.
        baseline_apps: the baseline metadata apps list (for versionCode binding).
        candidate_apps: the candidate metadata apps list (for versionCode
            binding). Empty/unknown → candidate additions are NOT tolerated.
    """
    profile = projection_profile(policy)
    baseline_projected = profile.project(baseline_state or {})
    candidate_projected = profile.project(candidate_state or {})

    raw_diffs = _diff_projected(baseline_projected, candidate_projected)
    # Apply version-direction-aware tolerance (Contract 5).
    candidate_higher_by_app = _candidate_higher_by_app(baseline_apps, candidate_apps)
    task_app_ids = {str(app.get("id")) for app in baseline_apps if isinstance(app, dict)}
    task_app_ids |= {str(app.get("id")) for app in candidate_apps if isinstance(app, dict)}

    real_diffs: list[PathDiff] = []
    for diff in raw_diffs:
        if _is_tolerated_addition(
            diff, task_app_ids=task_app_ids, candidate_higher=candidate_higher_by_app
        ):
            continue
        real_diffs.append(diff)

    if not real_diffs:
        return IntegrityReport(
            status=INTEGRITY_OK,
            reason=INTEGRITY_OK,
            baseline_projection_hash=_hash_projected(baseline_projected),
            candidate_projection_hash=_hash_projected(candidate_projected),
        )

    return IntegrityReport(
        status=INTEGRITY_PROJECTION_MISMATCH,
        reason=INTEGRITY_PROJECTION_MISMATCH,
        path_diffs=real_diffs,
        baseline_projection_hash=_hash_projected(baseline_projected),
        candidate_projection_hash=_hash_projected(candidate_projected),
    )


# ---------------------------------------------------------------------------
# diff helpers
# ---------------------------------------------------------------------------


def _diff_projected(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> list[PathDiff]:
    """Path-level diff of two flat ``{path: value}`` projections.

    A path present in only one side is reported with the other side as ``None``
    (the ``PathDiff.baseline``/``candidate`` semantics: ``None`` means "absent").
    """
    diffs: list[PathDiff] = []
    all_paths = sorted(set(baseline) | set(candidate))
    for path in all_paths:
        b = baseline.get(path, _ABSENT)
        c = candidate.get(path, _ABSENT)
        if b is _ABSENT or c is _ABSENT:
            diffs.append(
                PathDiff(
                    path=path,
                    baseline=None if b is _ABSENT else b,
                    candidate=None if c is _ABSENT else c,
                )
            )
            continue
        if b != c:
            diffs.append(PathDiff(path=path, baseline=b, candidate=c))
    return diffs


_ABSENT = object()


def _candidate_higher_by_app(
    baseline_apps: list[dict[str, Any]],
    candidate_apps: list[dict[str, Any]],
) -> dict[str, bool]:
    """Map app_id → True iff candidate's versionCode is strictly higher.

    An app missing from the candidate maps to ``False`` (unknown → cannot prove
    upgrade). This drives Contract 5's version-direction binding.
    """
    baseline_codes = {
        str(app.get("id")): app.get("versionCode")
        for app in baseline_apps
        if isinstance(app, dict) and app.get("id") is not None
    }
    out: dict[str, bool] = {}
    for app in candidate_apps:
        if not isinstance(app, dict) or app.get("id") is None:
            continue
        app_id = str(app["id"])
        baseline_code = baseline_codes.get(app_id)
        candidate_code = app.get("versionCode")
        if (
            isinstance(baseline_code, int)
            and isinstance(candidate_code, int)
            and candidate_code > baseline_code
        ):
            out[app_id] = True
        else:
            out[app_id] = False
    return out


def _is_tolerated_addition(
    diff: PathDiff,
    *,
    task_app_ids: set[str],
    candidate_higher: dict[str, bool],
) -> bool:
    """Return True iff *diff* is a candidate-only path inside a task app that
    is allowed because the candidate's versionCode is strictly higher
    (Contract 5).

    A "candidate-only path" is one where the baseline value is None (absent) and
    the candidate value is present. The path must be under a task app
    (``apps.<app_id>.``). Removals (baseline-only) are NEVER tolerated.
    """
    if diff.baseline is not None:
        return False  # value change or removal — not a tolerated addition
    if diff.candidate is None:
        return False  # both absent — shouldn't happen
    # The path must be under apps.<app_id>...
    for app_id in task_app_ids:
        prefix = f"apps.{app_id}."
        if diff.path.startswith(prefix) and candidate_higher.get(app_id, False):
            return True
    return False


def _hash_projected(projected: dict[str, Any]) -> str:
    """Stable digest of a projected dict (deferred import to avoid cycle)."""
    from test_platform.domain.canonical_json import canonical_sha256

    return canonical_sha256(projected)
