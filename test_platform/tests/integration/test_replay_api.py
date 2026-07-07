from __future__ import annotations

import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database

NOW = "2026-07-06T00:00:00.000Z"


def _settings(tmp_path) -> PlatformSettings:
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _seed_replay_run(database: Database) -> str:
    """Seed a single-lane run with one episode attempt that has a trajectory.

    The episode_attempt.artifact_root points at the trajectory directory, so
    trajectory.json and step files are written under
    ``{runs_dir}/{run_id}/{artifact_root}/``.
    """
    connection = database.connection
    run_plan = {
        "schema_version": 1,
        "run_id": "run_replay",
        "workflow_version_id": "wv1",
        "task_source": {"registry_digest": "sha256:catalog-replay"},
        "lanes": [
            {
                "lane_id": "lane_cand",
                "lane_key": "candidate",
                "role": "candidate",
                "target_id": "target_cand",
                "target_revision_id": "trev_cand",
                "target_revision_hash": "sha256:cand",
                "runner_config": {},
            }
        ],
        "episodes": [
            {
                "episode_key": "alipay.CheckBalance::0",
                "materialization_key": "mat0",
                "pair_key": "pair0",
                "task_base_id": "alipay.CheckBalance",
                "task_id": "alipay.CheckBalance",
                "instance_id": 1,
                "instance_seed": 11,
                "template_index": 0,
                "trial_id": 0,
                "max_steps": 20,
            }
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
    connection.execute(
        "INSERT INTO targets "
        "(id, project_id, name, kind, enabled, config_json, created_at, updated_at) "
        "VALUES ('target_cand', 'proj1', 'target_cand', 'simulator', 1, '{}', ?, ?)",
        (NOW, NOW),
    )
    connection.execute(
        "INSERT INTO target_revisions "
        "(id, target_id, metadata_json, metadata_hash, health_status, warnings_json, resolved_at) "
        "VALUES ('trev_cand', 'target_cand', '{}', 'sha256:trev_cand', 'healthy', '[]', ?)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO runs "
        "(id, project_id, workflow_version_id, name, state, run_plan_json, run_plan_hash, "
        " artifact_root, next_event_sequence, cancel_requested_at, created_at, updated_at, started_at, ended_at) "
        "VALUES ('run_replay', 'proj1', 'wv1', 'Replay run', 'completed', ?, 'sha256:plan', "
        " 'runs/run_replay', 1, NULL, ?, ?, ?, ?)",
        (json.dumps(run_plan, sort_keys=True), NOW, NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO lanes "
        "(id, run_id, lane_key, role, target_id, target_revision_id, runner_config_json, "
        " reproducibility_fingerprint, created_at) "
        "VALUES ('lane_cand', 'run_replay', 'candidate', 'candidate', 'target_cand', 'trev_cand', "
        " '{}', 'sha256:candidate', ?)",
        (NOW,),
    )
    connection.execute(
        "INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, started_at, ended_at, created_at) "
        "VALUES ('attempt1', 'run_replay', 1, 'test', 'completed', ?, ?, ?)",
        (NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO lane_attempts "
        "(id, lane_id, run_attempt_id, state, artifact_root, created_at, started_at, ended_at) "
        "VALUES ('attempt1_candidate', 'lane_cand', 'attempt1', 'completed', 'lanes/candidate', "
        " ?, ?, ?)",
        (NOW, NOW, NOW),
    )
    connection.execute(
        "INSERT INTO episodes "
        "(id, run_id, episode_key, materialization_key, pair_key, task_base_id, task_id, "
        " instance_id, instance_seed, template_index, trial_id, max_steps, created_at) "
        "VALUES ('ep0', 'run_replay', 'alipay.CheckBalance::0', 'mat0', 'pair0', "
        " 'alipay.CheckBalance', 'alipay.CheckBalance', 1, 11, 0, 0, 20, ?)",
        (NOW,),
    )
    # task_id_safe = alipay.CheckBalance -> alipay_CheckBalance. The episode
    # artifact_root is the full trajectory directory (matches execution.py's
    # _episode_artifact_root output for new runs).
    episode_artifact_root = "lanes/candidate/trajectory/alipay_CheckBalance"
    result_json = json.dumps(
        {
            "execution": {"stop_reason": "MAX_STEPS", "runtime_s": 10.5},
            "is_success": False,
            "answer_completion_accepted": True,
            "judge": {"passed": False},
        },
        sort_keys=True,
    )
    connection.execute(
        "INSERT INTO episode_attempts "
        "(id, episode_id, lane_attempt_id, attempt_no, state, outcome, error_code, result_json, "
        " artifact_root, started_at, ended_at, created_at) "
        "VALUES ('ea_cand_ep0', 'ep0', 'attempt1_candidate', 1, 'completed', 'FAIL', "
        " 'ASSERTION_FAILURE', ?, ?, ?, ?, ?)",
        (result_json, episode_artifact_root, NOW, NOW, NOW),
    )
    connection.commit()
    return "run_replay"


def _write_trajectory(settings: PlatformSettings, run_id: str) -> str:
    """Write a fake trajectory.json + step artifacts under the episode root.

    Returns the episode artifact_root (the trajectory directory).
    """
    artifact_root = "lanes/candidate/trajectory/alipay_CheckBalance"
    episode_dir = settings.runs_dir / run_id / artifact_root
    episode_dir.mkdir(parents=True, exist_ok=True)
    trajectory = [
        {
            "step": 1,
            "route": {"app": "alipay", "path": "/"},
            "action_type": "CLICK",
            "action_data": {"point": [0.5, 0.5]},
            "thought": "user wants to check balance",
            "explain": "",
            "summary": "",
            "screenshot": "step_001.jpg",
            "screenshot_annotated": "step_001_annot.jpg",
            "model_response_path": "step_001_response.txt",
            "model_prompt_path": "step_001_prompt.json",
        },
        {
            "step": 2,
            "route": {"app": "alipay", "path": "/balance"},
            "action_type": "DONE",
            "action_data": {"answer": "100"},
            "thought": "balance shown",
            "explain": "",
            "summary": "",
            "screenshot": "step_002.jpg",
            "screenshot_annotated": "",
            "model_response_path": "step_002_response.txt",
            "model_prompt_path": "",
        },
    ]
    (episode_dir / "trajectory.json").write_text(
        json.dumps(trajectory), encoding="utf-8"
    )
    for name in (
        "step_001.jpg",
        "step_001_annot.jpg",
        "step_001_response.txt",
        "step_001_prompt.json",
        "step_002.jpg",
        "step_002_response.txt",
    ):
        (episode_dir / name).write_text(f"contents of {name}", encoding="utf-8")
    return artifact_root


def test_replay_returns_steps_with_artifact_ids(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_replay_run(client.app.state.database)
        _write_trajectory(settings, run_id)

        response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/alipay.CheckBalance::0/replay"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["run_id"] == run_id
        assert body["episode_key"] == "alipay.CheckBalance::0"
        assert body["lane_key"] == "candidate"
        assert body["attempt_no"] == 1
        assert body["episode_attempt_id"] == "ea_cand_ep0"
        assert body["artifact_root"] == "lanes/candidate/trajectory/alipay_CheckBalance"
        assert body["outcome"] == "FAIL"
        assert body["error_code"] == "ASSERTION_FAILURE"
        assert body["result"]["execution"]["stop_reason"] == "MAX_STEPS"
        assert body["result"]["answer_completion_accepted"] is True

        steps = body["steps"]
        assert len(steps) == 2

        step1 = steps[0]
        assert step1["step"] == 1
        assert step1["route"] == {"app": "alipay", "path": "/"}
        assert step1["action_type"] == "CLICK"
        assert step1["action_data"] == {"point": [0.5, 0.5]}
        assert step1["thought"] == "user wants to check balance"
        # All four artifact references on step 1 must resolve to artifact ids.
        assert step1["screenshot_artifact_id"] is not None
        assert step1["screenshot_annotated_artifact_id"] is not None
        assert step1["model_response_artifact_id"] is not None
        assert step1["model_prompt_artifact_id"] is not None

        step2 = steps[1]
        assert step2["step"] == 2
        assert step2["action_type"] == "DONE"
        # step 2 has no annotated/prompt references in trajectory.json.
        assert step2["screenshot_artifact_id"] is not None
        assert step2["model_response_artifact_id"] is not None
        assert step2["screenshot_annotated_artifact_id"] is None
        assert step2["model_prompt_artifact_id"] is None

        # The mapped artifact ids must be fetchable via the artifact content
        # endpoint, proving the round-trip is correct.
        artifact_id = step1["screenshot_artifact_id"]
        content = client.get(
            f"/api/platform/v1/runs/{run_id}/artifacts/{artifact_id}/content"
        )
        assert content.status_code == 200
        assert content.text == "contents of step_001.jpg"


def test_replay_explicit_attempt_no_and_lane_key(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_replay_run(client.app.state.database)
        _write_trajectory(settings, run_id)

        response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/alipay.CheckBalance::0/replay"
            "?lane_key=candidate&attempt_no=1"
        )

        assert response.status_code == 200
        body = response.json()
        assert body["attempt_no"] == 1
        assert body["lane_key"] == "candidate"


def test_replay_missing_run_returns_run_not_found(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        _seed_replay_run(client.app.state.database)
        response = client.get(
            "/api/platform/v1/runs/does-not-exist/episodes/whatever::0/replay"
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_replay_missing_episode_returns_episode_not_found(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_replay_run(client.app.state.database)
        response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/missing.Episode::0/replay"
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "EPISODE_NOT_FOUND"


def test_replay_missing_trajectory_returns_replay_artifact_missing(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_replay_run(client.app.state.database)
        # Note: deliberately do NOT write trajectory.json.

        response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/alipay.CheckBalance::0/replay"
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "REPLAY_ARTIFACT_MISSING"


def test_replay_wrong_lane_returns_episode_attempt_not_found(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_replay_run(client.app.state.database)
        _write_trajectory(settings, run_id)

        response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/alipay.CheckBalance::0/replay"
            "?lane_key=baseline"
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "EPISODE_ATTEMPT_NOT_FOUND"


def test_replay_path_traversal_is_impossible(tmp_path):
    """The endpoint only reads trajectory.json under a contained artifact_root;
    user-supplied inputs (episode_key, lane_key, attempt_no) can never steer it
    at an arbitrary filesystem path. attempt_no is validated as int-or-'latest'
    and episode_key/lane_key are matched against DB rows only.
    """
    settings = _settings(tmp_path)
    app = create_app(settings)

    with TestClient(app) as client:
        run_id = _seed_replay_run(client.app.state.database)
        _write_trajectory(settings, run_id)

        # attempt_no that is neither 'latest' nor a valid attempt number never
        # touches the filesystem — it 404s at the attempt-resolution step.
        traversal_response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/alipay.CheckBalance::0/replay"
            "?attempt_no=../../etc/passwd"
        )
        assert traversal_response.status_code == 404
        assert traversal_response.json()["error"]["code"] == "EPISODE_ATTEMPT_NOT_FOUND"

        # An episode_key containing path segments cannot match a stored episode
        # row (episode keys never contain raw slashes), and FastAPI's router
        # additionally rejects encoded slashes in a single path segment. Either
        # way the response is a 404 and the filesystem is never read.
        bad_episode_response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/..%2F..%2Fetc%2Fpasswd/replay"
        )
        assert bad_episode_response.status_code == 404


def test_replay_paired_run_defaults_to_candidate_lane(tmp_path):
    """P2: when lane_key is omitted on a paired run (2+ lanes), the default
    must be 'candidate' — not an arbitrary mix of baseline/candidate attempts."""
    settings = _settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        database: Database = client.app.state.database
        run_id = _seed_replay_run(database)
        _write_trajectory(settings, run_id)  # candidate lane trajectory

        # Add a second lane (baseline) with its own episode_attempt for the
        # same episode_key, so this becomes a "paired" run.
        import sqlite3 as _sql
        with database._lock:  # noqa: SLF100
            conn = database.connection
            # Add a baseline lane
            conn.execute(
                "INSERT INTO lanes (id, run_id, lane_key, role, target_id, "
                "target_revision_id, runner_config_json, reproducibility_fingerprint, created_at) "
                "VALUES ('lane_bl', ?, 'baseline', 'baseline', "
                "(SELECT target_id FROM lanes WHERE run_id=? LIMIT 1), "
                "(SELECT target_revision_id FROM lanes WHERE run_id=? LIMIT 1), "
                "'{}', 'fp_bl', ?)",
                (run_id, run_id, run_id, NOW),
            )
            # Add a lane_attempt for the baseline lane
            ra_id = conn.execute(
                "INSERT INTO run_attempts (id, run_id, attempt_no, reason, state, created_at) "
                "VALUES ('ra2', ?, 2, 'retry', 'completed', ?)",
                (run_id, NOW),
            )
            conn.execute(
                "INSERT INTO lane_attempts (id, lane_id, run_attempt_id, state, artifact_root, created_at) "
                "VALUES ('la_bl', 'lane_bl', 'ra2', 'completed', 'lanes/baseline', ?)",
                (NOW,),
            )
            # Get the episode id
            ep_row = conn.execute(
                "SELECT id FROM episodes WHERE run_id = ? AND episode_key = ?",
                (run_id, "alipay.CheckBalance::0"),
            ).fetchone()
            if ep_row:
                conn.execute(
                    "INSERT INTO episode_attempts "
                    "(id, episode_id, lane_attempt_id, attempt_no, state, outcome, "
                    "error_code, result_json, artifact_root, started_at, ended_at, created_at) "
                    "VALUES ('ea_bl', ?, 'la_bl', 1, 'completed', 'PASS', NULL, "
                    "'{\"is_success\": true}', 'lanes/baseline/trajectory/alipay_CheckBalance', "
                    "?, ?, ?)",
                    (str(ep_row["id"]), NOW, NOW, NOW),
                )
            conn.commit()

        # Write trajectory for baseline lane too
        baseline_traj_dir = settings.runs_dir / run_id / "lanes/baseline/trajectory/alipay_CheckBalance"
        baseline_traj_dir.mkdir(parents=True, exist_ok=True)
        (baseline_traj_dir / "trajectory.json").write_text(
            json.dumps([{"step": 1, "action_type": "COMPLETE", "action_data": {},
                         "thought": "", "explain": "", "summary": "",
                         "route": {}, "screenshot": None, "screenshot_annotated": None,
                         "model_response_path": None, "model_prompt_path": None}])
        )

        # Without lane_key → should default to candidate, NOT baseline
        response = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/alipay.CheckBalance::0/replay"
        )
        assert response.status_code == 200
        replay = response.json()
        assert replay["lane_key"] == "candidate", (
            f"paired run default lane should be 'candidate', got '{replay['lane_key']}'"
        )

        # Explicit baseline works
        response_bl = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/alipay.CheckBalance::0/replay?lane_key=baseline"
        )
        assert response_bl.status_code == 200
        assert response_bl.json()["lane_key"] == "baseline"
