"""Shared monitor source discovery for report ingestion and artifact indexing.

Used by both ``ReportInputRepository._get_for_run`` (to read monitor data into
report input) and ``ArtifactRepository._index_run`` (to register monitor files
as artifacts), so both consumers see the exact same set of sources.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from test_platform.artifacts.paths import ArtifactPathError, _contained_path

_MAX_SOURCES = 32
_MAX_SUBDIRS_PER_LANE = 64  # bound on per-lane subdirectory enumeration


@dataclass(frozen=True)
class MonitorSource:
    """A discovered monitor CSV file."""

    run_attempt_id: str
    lane_attempt_id: str
    lane_key: str
    relative_path: str  # relative to the run root
    resolved_path: Path  # resolved + path-safe verified


@dataclass(frozen=True)
class DiscoveryResult:
    """Result of monitor source discovery, including bounds metadata."""

    sources: list[MonitorSource]
    discovered_count: int
    accepted_count: int
    excluded_source_count: int
    scan_truncated_lanes: list[str]  # lane_keys where subdir scan overflowed


def discover_monitor_sources(
    run_root: Path,
    lane_attempts: list[dict],
) -> DiscoveryResult:
    """Discover monitor.csv files for the given lane attempts.

    Discovery rules:
    - Serial/Parallel: ``<artifact_root>/monitor.csv`` (directly in lane root).
    - Multiprocess: ``<artifact_root>/*/monitor.csv`` (one-level timestamp
      subdirectory; not recursive, not shards/p*).
    - Path-safe: each candidate is verified via ``_contained_path``.
    - Deterministic order: sorted by ``(lane_key, relative_path)`` before
      applying the source limit.
    - Bounded: at most ``_MAX_SOURCES`` sources; excess is recorded.
    """
    run_root = run_root.resolve()
    candidates: list[tuple[str, str, str, str, str, Path]] = []
    scan_truncated_lanes: list[str] = []

    for la in lane_attempts:
        lane_key = str(la.get("lane_key") or "")
        artifact_root_rel = str(la.get("artifact_root") or "")
        lane_attempt_id = str(la.get("id") or "")
        run_attempt_id = str(la.get("run_attempt_id") or "")
        if not artifact_root_rel:
            continue

        # Verify containment BEFORE resolving or traversing.
        try:
            from test_platform.artifacts.paths import resolve_artifact_directory
            lane_root = resolve_artifact_directory(run_root, artifact_root_rel)
        except Exception:
            continue

        # Serial/Parallel: monitor.csv directly in lane root.
        _try_add(candidates, run_root, lane_root, lane_key, lane_attempt_id, run_attempt_id)

        # Multiprocess: one-level timestamp subdirectory (not recursive, not shards/).
        # Bounded: count ALL entries (files + dirs) during os.scandir iteration.
        # If total entries exceed _MAX_SUBDIRS_PER_LANE + 1, the lane has too many
        # items — fail-closed: skip subdirectory discovery for this lane to avoid
        # non-deterministic selection between report ingestion and artifact indexing.
        try:
            discovered_dirs: list[Path] = []
            total_entries = 0
            overflow = False
            with os.scandir(lane_root) as it:
                for entry in it:
                    total_entries += 1
                    if total_entries > _MAX_SUBDIRS_PER_LANE + 1:
                        overflow = True
                        break
                    if entry.is_dir():
                        discovered_dirs.append(Path(entry.path))
            if not overflow:
                for child in sorted(discovered_dirs, key=lambda p: p.name):
                    _try_add(candidates, run_root, child, lane_key, lane_attempt_id, run_attempt_id)
            else:
                scan_truncated_lanes.append(lane_key)
        except (PermissionError, OSError):
            pass

    # Deduplicate by relative_path.
    seen: set[str] = set()
    unique: list[tuple[str, str, str, str, str, Path]] = []
    for item in candidates:
        rel = item[1]
        if rel in seen:
            continue
        seen.add(rel)
        unique.append(item)

    # Deterministic sort by (lane_key, relative_path).
    unique.sort(key=lambda x: x[0])

    discovered = len(unique)
    accepted = unique[:_MAX_SOURCES]
    excluded = discovered - len(accepted)

    sources = [
        MonitorSource(
            run_attempt_id=item[4],
            lane_attempt_id=item[3],
            lane_key=item[2],
            relative_path=item[1],
            resolved_path=item[5],
        )
        for item in accepted
    ]

    return DiscoveryResult(
        sources=sources,
        discovered_count=discovered,
        accepted_count=len(sources),
        excluded_source_count=excluded,
        scan_truncated_lanes=sorted(set(scan_truncated_lanes)),
    )


def _try_add(
    out: list[tuple[str, str, str, str, str, Path]],
    run_root: Path,
    parent_dir: Path,
    lane_key: str,
    lane_attempt_id: str,
    run_attempt_id: str,
) -> None:
    """Try to add monitor.csv from parent_dir if it exists and is path-safe."""
    candidate = parent_dir / "monitor.csv"
    if not candidate.is_file():
        return
    try:
        resolved = candidate.resolve()
        rel = str(resolved.relative_to(run_root))
    except ValueError:
        return  # escapes run root after resolve
    # Path-safe verification.
    try:
        _contained_path(run_root, rel)
    except ArtifactPathError:
        return
    out.append((f"{lane_key}|{rel}", rel, lane_key, lane_attempt_id, run_attempt_id, resolved))
