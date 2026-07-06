from __future__ import annotations

import json

from fastapi.testclient import TestClient

from test_platform.api.app import create_app
from test_platform.persistence.database import Database
from test_platform.services.runs import FakeRunSupervisor, RunService
from test_platform.tests.integration.test_single_lane_materialization import (
    _catalog as _single_lane_catalog,
    _create_run,
    _settings,
)


def test_startup_recovery_marks_active_attempts_service_restarted_and_resume_eligible(tmp_path):
    settings = _settings(tmp_path)
    database = Database(settings)
    database.initialize()
    try:
        run = _create_run(database, settings, repeat_n=2)
        lane_attempt = database.connection.execute(
            """
            SELECT la.id, la.artifact_root
            FROM lane_attempts AS la
            JOIN lanes AS l ON l.id = la.lane_id
            WHERE l.run_id = ?
            """,
            (run.id,),
        ).fetchone()
        first_episode = database.connection.execute(
            "SELECT id FROM episodes WHERE run_id = ? ORDER BY trial_id LIMIT 1",
            (run.id,),
        ).fetchone()
        database.connection.execute(
            "UPDATE runs SET state = 'running' WHERE id = ?",
            (run.id,),
        )
        database.connection.execute(
            "UPDATE run_attempts SET state = 'running' WHERE run_id = ?",
            (run.id,),
        )
        database.connection.execute(
            "UPDATE lane_attempts SET state = 'running' WHERE id = ?",
            (lane_attempt["id"],),
        )
        database.connection.execute(
            """
            INSERT INTO episode_attempts (
              id, episode_id, lane_attempt_id, attempt_no, state, outcome,
              error_code, result_json, artifact_root, started_at, ended_at, created_at
            )
            VALUES (
              'completed-ea', ?, ?, 1, 'completed', 'PASS', NULL, ?,
              ?, '2026-07-06T00:00:00.000Z', '2026-07-06T00:00:01.000Z',
              '2026-07-06T00:00:00.000Z'
            )
            """,
            (
                first_episode["id"],
                lane_attempt["id"],
                json.dumps({"id": "fake.SampleTask", "is_success": True}, sort_keys=True),
                f"{lane_attempt['artifact_root']}/trajectory/completed",
            ),
        )
        database.connection.commit()
    finally:
        database.close()

    app = create_app(settings, supervisor=FakeRunSupervisor())
    with TestClient(app):
        live_database = app.state.database
        states = live_database.connection.execute(
            """
            SELECT r.state AS run_state, ra.state AS run_attempt_state, la.state AS lane_attempt_state
            FROM runs AS r
            JOIN run_attempts AS ra ON ra.run_id = r.id
            JOIN lane_attempts AS la ON la.run_attempt_id = ra.id
            WHERE r.id = ?
            """,
            (run.id,),
        ).fetchone()
        assert dict(states) == {
            "run_state": "failed",
            "run_attempt_state": "failed",
            "lane_attempt_state": "failed",
        }

        attempts = live_database.connection.execute(
            """
            SELECT e.trial_id, ea.outcome, ea.error_code
            FROM episode_attempts AS ea
            JOIN episodes AS e ON e.id = ea.episode_id
            JOIN lane_attempts AS la ON la.id = ea.lane_attempt_id
            WHERE la.run_attempt_id IN (SELECT id FROM run_attempts WHERE run_id = ?)
            ORDER BY e.trial_id
            """,
            (run.id,),
        ).fetchall()
        assert [(row["trial_id"], row["outcome"], row["error_code"]) for row in attempts] == [
            (0, "PASS", None),
            (1, "ERROR", "SERVICE_RESTARTED"),
        ]
        restarted_episode_key = live_database.connection.execute(
            "SELECT episode_key FROM episodes WHERE run_id = ? AND trial_id = 1",
            (run.id,),
        ).fetchone()["episode_key"]

        resume = RunService(
            live_database,
            settings,
            supervisor=FakeRunSupervisor(),
            catalog_builder=_single_lane_catalog,
        ).resume_run(run.id)
        assert resume["selected_lane_episodes"] == [
            {
                "episode_key": restarted_episode_key,
                "lane_key": "candidate",
                "reason": "resume_service_restarted",
            }
        ]
