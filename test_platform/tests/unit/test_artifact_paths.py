from __future__ import annotations

from pathlib import Path

import pytest

from test_platform.artifacts.paths import ArtifactPathError, resolve_artifact_path


def test_resolve_artifact_path_accepts_regular_file_under_run_root(tmp_path):
    run_root = tmp_path / "runs" / "run1"
    artifact = run_root / "lane" / "trace.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")

    assert resolve_artifact_path(run_root, "lane/trace.json") == artifact.resolve()


def test_resolve_artifact_path_rejects_absolute_and_parent_paths(tmp_path):
    run_root = tmp_path / "runs" / "run1"
    run_root.mkdir(parents=True)

    for relative_path in ("/etc/passwd", "../outside.txt", "lane/../../outside.txt"):
        with pytest.raises(ArtifactPathError):
            resolve_artifact_path(run_root, relative_path)


def test_resolve_artifact_path_rejects_symlink_escape(tmp_path):
    run_root = tmp_path / "runs" / "run1"
    outside = tmp_path / "outside"
    outside.mkdir(parents=True)
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    run_root.mkdir(parents=True)
    (run_root / "leak").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArtifactPathError):
        resolve_artifact_path(run_root, "leak/secret.txt")
