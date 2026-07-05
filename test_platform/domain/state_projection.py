"""Comparison-grade state projection for paired runs.

The raw initial state includes volatile paths (clocks, task stacks, keyboard,
active app, etc.) that change between lanes even when the fixture is identical.
``project_state`` strips those paths so two lanes' projected states are
comparable; ``projection_hash_v1`` gives a stable digest for integrity checks.

The ignore-path semantics mirror ``StateComparator.filter_unexpected_changes``
(``bench_env/task/judge.py``): dotted paths with ``*`` (any segment) and ``[]``
(any array index) wildcards, plus prefix matching.
"""
from __future__ import annotations

import re
from typing import Any

from test_platform.domain.canonical_json import canonical_sha256

PROJECTION_SCHEMA_VERSION = 1

# Default volatile paths to strip. Mirrors BaseTask.always_ignore but lives in
# the platform domain so the projection is task-agnostic (the materializer can
# extend with task-specific always_ignore at call time).
DEFAULT_IGNORE_PATHS: list[str] = [
    "os.time",
    "os.isLauncherVisible",
    "os.runningApps",
    "os.activeAppId",
    "os.activeTaskId",
    "os.services.taskManager.activeTaskId",
    "apps.answer_sheet",
    "os.services.taskManager.isLauncherVisible",
    "os.tasks",
    "os.services.taskManager.tasks",
    "os.isRecentsVisible",
    "os.services.taskManager.isRecentsVisible",
    "os.services.keyboard",
    "os.services.alarm_manager",
    "os.services.media_session",
    "os.providers.contacts.contacts[].updatedAt",
    "os.providers.contacts.contacts[].lastContactedAt",
    "apps.*._temp",
]


def _path_is_ignored(path: str, ignore_paths: list[str]) -> bool:
    """Return True if *path* matches any ignore pattern (wildcard or prefix)."""
    for exp in ignore_paths:
        if "*" in exp or "[]" in exp:
            esc = re.escape(exp)
            esc = esc.replace(re.escape("[]"), r"\[\d+\]")
            esc = esc.replace(r"\*", r"[^.\[]+")
            if re.fullmatch(esc, path):
                return True
            if re.match(esc + r"(\.|\[)", path):
                return True
        else:
            if path == exp or path.startswith(exp + ".") or path.startswith(exp + "["):
                return True
    return False


def _flatten(state: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten a nested dict/list into (dotted_path, value) leaves."""
    leaves: list[tuple[str, Any]] = []
    if isinstance(state, dict):
        for key in sorted(state):
            path = f"{prefix}.{key}" if prefix else str(key)
            leaves.extend(_flatten(state[key], path))
    elif isinstance(state, list):
        for idx, item in enumerate(state):
            path = f"{prefix}[{idx}]"
            leaves.extend(_flatten(item, path))
    else:
        leaves.append((prefix, state))
    return leaves


def project_state(
    state: dict[str, Any],
    *,
    ignore_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Return a comparison-grade projection of *state* with volatile paths removed.

    The result is a flat ``{path: value}`` dict (sorted by path) suitable for
    hashing or direct diff. Only leaf values are retained; intermediate dict/list
    structure is implied by the dotted paths.
    """
    paths = ignore_paths if ignore_paths is not None else DEFAULT_IGNORE_PATHS
    leaves = _flatten(state)
    return {path: value for path, value in leaves if not _path_is_ignored(path, paths)}


def projection_hash_v1(
    state: dict[str, Any],
    *,
    ignore_paths: list[str] | None = None,
) -> str:
    """Stable sha256 digest of the projected state (volatile paths stripped)."""
    projected = project_state(state, ignore_paths=ignore_paths)
    return canonical_sha256(projected)
