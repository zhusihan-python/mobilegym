from __future__ import annotations

import json

import pytest

from test_platform.domain.legacy_import import LegacyImportError, load_legacy_run


def _write_legacy_run(root):
    root.mkdir(parents=True)
    (root / "meta.json").write_text(
        json.dumps(
            {
                "start_time": "2026-07-06T00:00:00.000Z",
                "agent": "legacy-agent",
                "model_name": "legacy-model",
                "repeat_n": 2,
                "sample_seed": 123,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    rows = [
        {
            "id": "wechat.OpenBlacklist",
            "task_name": "Open blacklist",
            "suite": "wechat",
            "apps": ["wechat"],
            "trial_id": 0,
            "max_steps": 30,
            "execution": {"steps": 3, "runtime_s": 1.25, "stop_reason": "COMPLETE"},
            "judge": {"success": True, "clean": True, "progress": 1.0},
            "is_success": True,
            "is_error": False,
            "progress": 1.0,
        },
        {
            "id": "wechat.OpenBlacklist",
            "task_name": "Open blacklist",
            "suite": "wechat",
            "apps": ["wechat"],
            "trial_id": 1,
            "max_steps": 30,
            "execution": {"steps": 2, "runtime_s": 0.5, "error": "browser closed"},
            "judge": None,
            "is_success": False,
            "is_error": True,
            "progress": 0.0,
        },
    ]
    (root / "results.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    (root / "summary.json").write_text(
        json.dumps({"end_time": "2026-07-06T00:00:10.000Z"}, sort_keys=True),
        encoding="utf-8",
    )
    trajectory = root / "trajectory" / "wechat_OpenBlacklist_t0"
    trajectory.mkdir(parents=True)
    (trajectory / "trace.json").write_text('{"ok": true}', encoding="utf-8")
    return rows


def test_legacy_loader_maps_results_and_marks_missing_provenance(tmp_path):
    root = tmp_path / "legacy-run"
    _write_legacy_run(root)
    before = (root / "results.jsonl").read_bytes()

    loaded = load_legacy_run(root)

    assert loaded.source_root == root.resolve()
    assert loaded.source_name == "legacy-run"
    assert loaded.meta["agent"] == "legacy-agent"
    assert loaded.summary["end_time"] == "2026-07-06T00:00:10.000Z"
    assert loaded.provenance_missing == ["workflow", "target_revision", "task_source"]
    assert [episode.episode_key for episode in loaded.episodes] == [
        "wechat.OpenBlacklist|t0",
        "wechat.OpenBlacklist|t1",
    ]
    assert [episode.outcome for episode in loaded.episodes] == ["PASS", "ERROR"]
    assert loaded.episodes[0].artifact_root == "trajectory/wechat_OpenBlacklist_t0"
    assert (root / "results.jsonl").read_bytes() == before


def test_legacy_loader_rejects_missing_results(tmp_path):
    root = tmp_path / "not-a-run"
    root.mkdir()
    (root / "meta.json").write_text("{}", encoding="utf-8")

    with pytest.raises(LegacyImportError, match="results.jsonl"):
        load_legacy_run(root)
