from __future__ import annotations

import json

from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.persistence.repositories import ReportInputRepository


NOW = "2026-07-06T00:00:00.000Z"


def _database(tmp_path) -> Database:
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    return database


def _seed_reportable_paired_run(database: Database) -> str:
    connection = database.connection
    run_plan = {
        "schema_version": 1,
        "run_id": "run1",
        "workflow_version_id": "wv1",
        "task_source": {
            "repository_revision": "git-vs11",
            "registry_digest": "sha256:catalog-vs11",
            "selection": {"task_ids": ["fake.Task"], "sample_n": 1, "seed": 7},
        },
        "lanes": [
            {
                "lane_id": "lane_base",
                "lane_key": "baseline",
                "role": "baseline",
                "target_id": "target_base",
                "target_revision_id": "trev_base",
                "target_revision_hash": "sha256:base",
                "runner_config": {},
            },
            {
                "lane_id": "lane_cand",
                "lane_key": "candidate",
                "role": "candidate",
                "target_id": "target_cand",
                "target_revision_id": "trev_cand",
                "target_revision_hash": "sha256:cand",
                "runner_config": {},
            },
        ],
        "episodes": [
            {
                "episode_key": "fake.Task::0",
                "materialization_key": "mat0",
                "pair_key": "pair0",
                "task_base_id": "fake.Task",
                "task_id": "fake.Task",
                "instance_id": 1,
                "instance_seed": 11,
                "template_index": 0,
                "trial_id": 0,
                "max_steps": 20,
            },
            {
                "episode_key": "fake.Task::1",
                "materialization_key": "mat1",
                "pair_key": "pair1",
                "task_base_id": "fake.Task",
                "task_id": "fake.Task",
                "instance_id": 2,
                "instance_seed": 12,
                "template_index": 0,
                "trial_id": 0,
                "max_steps": 20,
            },
        ],
        "materialization": {},
        "comparison": {},
        "agent": {},
        "judge": {},
        "artifacts": {},
        "created_at": NOW,
        "fingerprint": "sha256:plan",
    }
    connection.execute(
        "INSERT INTO projects (id, name, slug, name_key, archived_at, created_at, updated_at) "
        "VALUES ('proj1', 'Project', 'project', 'project', NULL, ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO workflows (id, project_id, name, draft_definition_json, created_at, updated_at) "
        "VALUES ('wf1', 'proj1', 'Workflow', '{}', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO workflow_versions "
        "(id, workflow_id, version_no, status, definition_json, definition_hash, created_at, published_at) "
        "VALUES ('wv1', 'wf1', 1, 'published', '{}', 'sha256:wf', ?, ?)",
        (NOW, NOW),
    )
    for target_id, revision_id in (
        ("target_base", "trev_base"),
        ("target_cand", "trev_cand"),
    ):
        connection.execute(
            "INSERT INTO targets "
            "(id, project_id, name, kind, enabled, config_json, created_at, updated_at) "
            "VALUES (?, 'proj1', ?, 'simulator', 1, '{}', ?, ?)",
            (target_id, target_id, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO target_revisions "
            "(id, target_id, metadata_json, metadata_hash, health_status, warnings_json, resolved_at) "
            "VALUES (?, ?, '{}', ?, 'healthy', '[]', ?)",
            (revision_id, target_id, f"sha256:{revision_id}", NOW),
        )
    connection.execute(
        "INSERT INTO runs "
        "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
        " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at, started_at, ended_at) "
        "VALUES ('run1', 'proj1', 'wv1', 'VS-11 run', 'failed', ?, 'sha256:plan', "
        " 'runs/run1', 1, NULL, ?, ?, ?, ?)",
        (json.dumps(run_plan, sort_keys=True), NOW, NOW, NOW, NOW),
    )
    for lane_id, lane_key, target_id, revision_id in (
        ("lane_base", "baseline", "target_base", "trev_base"),
        ("lane_cand", "candidate", "target_cand", "trev_cand"),
    ):
        connection.execute(
            "INSERT INTO lanes "
            "(id, run_id, lane_key, role, target_id, target_revision_id, runner_config_json, "
            " reproducibility_fingerprint, created_at) "
            "VALUES (?, 'run1', ?, ?, ?, ?, '{}', ?, ?)",
            (lane_id, lane_key, lane_key, target_id, revision_id, f"sha256:{lane_key}", NOW),
        )
    for episode_id, episode_key, pair_key in (
        ("ep0", "fake.Task::0", "pair0"),
        ("ep1", "fake.Task::1", "pair1"),
    ):
        connection.execute(
            "INSERT INTO episodes "
            "(id, run_id, episode_key, materialization_key, pair_key, task_base_id, task_id, "
            " instance_id, instance_seed, template_index, trial_id, max_steps, created_at) "
            "VALUES (?, 'run1', ?, ?, ?, 'fake.Task', 'fake.Task', 1, 11, 0, 0, 20, ?)",
            (episode_id, episode_key, f"mat-{episode_id}", pair_key, NOW),
        )

    # attempt2 is newer than attempt1 but cancelled. attempt3 is the latest
    # terminal non-cancelled run attempt and must be selected.
    for attempt_id, attempt_no, state in (
        ("attempt1", 1, "completed"),
        ("attempt2", 2, "cancelled"),
        ("attempt3", 3, "failed"),
    ):
        connection.execute(
            "INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, started_at, ended_at, created_at) "
            "VALUES (?, 'run1', ?, 'test', ?, ?, ?, ?)",
            (attempt_id, attempt_no, state, NOW, NOW, NOW),
        )
        for lane_id, lane_key in (("lane_base", "baseline"), ("lane_cand", "candidate")):
            connection.execute(
                "INSERT INTO lane_attempts "
                "(id, lane_id, run_attempt_id, state, artifact_root, created_at, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"{attempt_id}_{lane_key}",
                    lane_id,
                    attempt_id,
                    state,
                    f"runs/run1/{attempt_id}/{lane_key}",
                    NOW,
                    NOW,
                    NOW,
                ),
            )

    connection.execute(
        "INSERT INTO episode_attempts "
        "(id, episode_id, lane_attempt_id, attempt_no, state, outcome, error_code, result_json, "
        " artifact_root, started_at, ended_at, created_at) "
        "VALUES ('ea_base_ep0', 'ep0', 'attempt3_baseline', 1, 'completed', 'SUCCESS', NULL, ?, "
        " 'artifacts/base0', ?, ?, ?)",
        (
            json.dumps(
                {
                    "execution": {
                        "runtime_s": 10.5,
                        "stopwatch_flat": {"load": 1.25},
                    },
                    "is_success": True,
                },
                sort_keys=True,
            ),
            NOW,
            NOW,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO episode_attempts "
        "(id, episode_id, lane_attempt_id, attempt_no, state, outcome, error_code, result_json, "
        " artifact_root, started_at, ended_at, created_at) "
        "VALUES ('ea_cand_ep0', 'ep0', 'attempt3_candidate', 1, 'completed', 'ERROR', 'AGENT_ERROR', ?, "
        " 'artifacts/cand0', ?, ?, ?)",
        (
            json.dumps(
                {
                    "execution": {
                        "runtime_s": 12.0,
                        "stopwatch_flat": {"load": 2.0},
                    },
                    "is_error": True,
                },
                sort_keys=True,
            ),
            NOW,
            NOW,
            NOW,
        ),
    )
    connection.execute(
        "INSERT INTO comparisons "
        "(id, run_id, run_attempt_id, baseline_lane_id, candidate_lane_id, policy_json, summary_json, created_at) "
        "VALUES ('cmp1', 'run1', 'attempt3', 'lane_base', 'lane_cand', '{}', '{}', ?)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO comparison_pairs "
        "(id, comparison_id, pair_key, baseline_episode_attempt_id, candidate_episode_attempt_id, "
        " classification, integrity_json, delta_json) "
        "VALUES ('pair_row_1', 'cmp1', 'pair0', 'ea_base_ep0', 'ea_cand_ep0', "
        " 'candidate_error', '{}', '{\"outcome\":\"candidate_error\"}')"
    )
    connection.commit()
    return "run1"


def test_report_input_selects_latest_terminal_raw_rows_and_planned_incompletes(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_reportable_paired_run(database)

        report_input = ReportInputRepository(database).get_for_run(run_id)
        same_input = ReportInputRepository(database).get_for_run(run_id)

        assert report_input.run_attempt_id == "attempt3"
        assert report_input.input_hash == same_input.input_hash
        assert report_input.provenance == {
            "project_id": "proj1",
            "run_id": "run1",
            "run_attempt_id": "attempt3",
            "workflow_version_id": "wv1",
            "run_plan_hash": "sha256:plan",
            "task_source_digest": "sha256:catalog-vs11",
            "target_revision_ids": {
                "baseline": "trev_base",
                "candidate": "trev_cand",
            },
        }

        selected_attempts = {attempt["id"]: attempt for attempt in report_input.episode_attempts}
        assert sorted(selected_attempts) == ["ea_base_ep0", "ea_cand_ep0"]
        assert selected_attempts["ea_base_ep0"]["episode_id"] == "ep0"
        assert selected_attempts["ea_base_ep0"]["lane_attempt_id"] == "attempt3_baseline"
        assert selected_attempts["ea_base_ep0"]["attempt_no"] == 1
        assert selected_attempts["ea_base_ep0"]["outcome"] == "SUCCESS"
        assert selected_attempts["ea_base_ep0"]["error_code"] is None
        assert selected_attempts["ea_base_ep0"]["result_json"]["execution"]["runtime_s"] == 10.5

        planned = {
            (item["episode_key"], item["lane_key"]): item
            for item in report_input.planned_lane_episodes
        }
        assert len(planned) == 4
        assert planned[("fake.Task::0", "baseline")]["episode_attempt_id"] == "ea_base_ep0"
        assert planned[("fake.Task::0", "candidate")]["episode_attempt_id"] == "ea_cand_ep0"
        assert planned[("fake.Task::1", "baseline")]["status"] == "incomplete"
        assert planned[("fake.Task::1", "candidate")]["status"] == "incomplete"

        assert report_input.comparison is not None
        pair = report_input.comparison["pairs"][0]
        assert pair["classification"] == "candidate_error"
        assert pair["baseline_attempt"]["id"] == "ea_base_ep0"
        assert pair["candidate_attempt"]["id"] == "ea_cand_ep0"
        assert pair["candidate_attempt"]["result_json"]["is_error"] is True
    finally:
        database.close()


def test_report_input_carries_manual_sequence_metadata_to_planned_rows(tmp_path):
    database = _database(tmp_path)
    try:
        run_id = _seed_reportable_paired_run(database)
        database.connection.execute(
            """
            UPDATE episodes
            SET sequence_index = CASE id WHEN 'ep0' THEN 0 WHEN 'ep1' THEN 1 END,
                sequence_group_id = 'manual_sequence'
            WHERE run_id = ?
            """,
            (run_id,),
        )
        database.connection.commit()

        report_input = ReportInputRepository(database).get_for_run(run_id)
        planned = [
            item
            for item in report_input.planned_lane_episodes
            if item["lane_key"] == "candidate"
        ]

        assert [item["task_id"] for item in planned] == ["fake.Task", "fake.Task"]
        assert [item["sequence_index"] for item in planned] == [0, 1]
        assert [item["sequence_group_id"] for item in planned] == [
            "manual_sequence",
            "manual_sequence",
        ]
    finally:
        database.close()
