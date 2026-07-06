from __future__ import annotations

from pathlib import Path


class ArtifactPathError(ValueError):
    """Raised when a requested artifact path escapes its run root."""


def resolve_artifact_path(run_root: Path, relative_path: str) -> Path:
    candidate = _contained_path(run_root, relative_path)
    if not candidate.is_file():
        raise ArtifactPathError("Artifact path does not point to a regular file.")
    return candidate


def resolve_artifact_directory(run_root: Path, relative_path: str) -> Path:
    candidate = _contained_path(run_root, relative_path)
    if not candidate.is_dir():
        raise ArtifactPathError("Artifact path does not point to a directory.")
    return candidate


def _contained_path(run_root: Path, relative_path: str) -> Path:
    if not relative_path:
        raise ArtifactPathError("Artifact path is required.")
    requested = Path(relative_path)
    if requested.is_absolute():
        raise ArtifactPathError("Artifact path must be relative.")
    if any(part == ".." for part in requested.parts):
        raise ArtifactPathError("Artifact path must not contain parent segments.")

    root = run_root.resolve()
    candidate = (root / requested).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ArtifactPathError("Artifact path escapes the run root.") from exc
    return candidate
