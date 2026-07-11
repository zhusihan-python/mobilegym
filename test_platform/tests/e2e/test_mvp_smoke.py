from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings
from test_platform.persistence.database import Database
from test_platform.testing.deterministic import (
    build_deterministic_executor_resolver,
    build_deterministic_target_registry,
)


def test_deterministic_adapter_requires_explicit_test_composition(tmp_path):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    database.initialize()
    try:
        with pytest.raises(RuntimeError, match="explicit test composition"):
            build_deterministic_executor_resolver(
                database,
                settings,
                enabled=False,
            )
    finally:
        database.close()


def test_deterministic_manual_sequence_crosses_public_run_and_replay_interfaces(
    tmp_path,
):
    settings = PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )
    database = Database(settings)
    app = create_app(
        settings,
        database=database,
        adapter_registry=build_deterministic_target_registry(),
        executor_resolver=build_deterministic_executor_resolver(
            database,
            settings,
            enabled=True,
        ),
    )

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Deterministic smoke"},
        ).json()
        target = client.post(
            "/api/platform/v1/targets",
            json={
                "project_id": project["id"],
                "name": "Deterministic simulator",
                "config": _target_config(),
            },
        ).json()
        health = client.post(
            f"/api/platform/v1/targets/{target['id']}/health"
        )
        assert health.status_code == 200
        assert health.json()["executable"] is True

        task_ids = [item["task_base_id"] for item in client.get(
            "/api/platform/v1/tasks"
        ).json()["items"][:3]]
        assert len(task_ids) == 3
        workflow = client.post(
            f"/api/platform/v1/projects/{project['id']}/workflows",
            json={
                "name": "Ordered deterministic sequence",
                "definition": _manual_sequence_definition(task_ids, target["id"]),
            },
        ).json()
        published = client.post(
            f"/api/platform/v1/workflows/{workflow['id']}/publish"
        )
        assert published.status_code == 200, published.text

        created = client.post(
            "/api/platform/v1/runs",
            headers={"Idempotency-Key": "tp-h06-manual-sequence"},
            json={
                "workflow_version_id": published.json()["workflow_version_id"],
                "name": "TP-H06 Manual Sequence",
                "overrides": {
                    "seed": 606,
                    "execution": {
                        "model_base_url": "http://deterministic.invalid/v1",
                        "model_name": "deterministic",
                    },
                },
            },
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        detail = _wait_for_state(client, run_id, "completed")

        assert [item["sequence_index"] for item in detail["episode_identities"]] == [
            0,
            1,
            2,
        ]
        assert [item["outcome"] for item in detail["episode_attempts"]] == [
            "PASS",
            "FAIL",
            "PASS",
        ]
        report = client.get(f"/api/platform/v1/runs/{run_id}/report").json()
        sequence = report["sequence"]["groups"][0]
        assert [item["task_id"] for item in sequence["items"]] == task_ids
        assert [item["outcome"] for item in sequence["items"]] == [
            "PASS",
            "FAIL",
            "PASS",
        ]

        failed_episode = detail["episode_identities"][1]["episode_key"]
        replay = client.get(
            f"/api/platform/v1/runs/{run_id}/episodes/{quote(failed_episode, safe='')}/replay",
            params={"lane_key": "candidate", "attempt_no": "latest"},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["outcome"] == "FAIL"
        assert replay.json()["steps"][0]["screenshot_artifact_id"] is not None
        assert replay.json()["result"]["judge"]["issues"][0]["reason"] == (
            "deterministic failure at sequence step 2"
        )


def test_browser_observes_ordered_manual_sequence_replay_and_sse_reconnect(tmp_path):
    with _running_browser_stack(tmp_path) as stack:
        with httpx.Client(base_url=stack["api_url"], timeout=10.0) as client:
            workflow_version_id, task_ids = _seed_public_manual_sequence(client)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            try:
                page.goto(f"{stack['web_url']}/test-platform/runs")
                created = page.evaluate(
                    """
                    async ({ workflowVersionId }) => {
                      const response = await fetch('/api/platform/v1/runs', {
                        method: 'POST',
                        headers: {
                          'Content-Type': 'application/json',
                          'Idempotency-Key': 'tp-h06-browser-sequence',
                        },
                        body: JSON.stringify({
                          workflow_version_id: workflowVersionId,
                          name: 'TP-H06 Browser Manual Sequence',
                          overrides: {
                            seed: 606,
                            execution: {
                              model_base_url: 'http://deterministic.invalid/v1',
                              model_name: 'deterministic',
                            },
                          },
                        }),
                      });
                      return { status: response.status, body: await response.json() };
                    }
                    """,
                    {"workflowVersionId": workflow_version_id},
                )
                assert created["status"] == 201, created
                run_id = created["body"]["id"]
                page.goto(f"{stack['web_url']}/test-platform/runs/{run_id}")

                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "running",
                    timeout=5_000,
                )
                page.wait_for_timeout(700)
                page.reload()
                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "completed",
                    timeout=15_000,
                )

                page.reload()
                expect(page.get_by_test_id("tp-run-state")).to_have_text("completed")
                expect(page.locator('[data-count="pass"]')).to_have_text("2")
                expect(page.locator('[data-count="fail"]')).to_have_text("1")
                expect(page.get_by_test_id("tp-replay-screenshot")).to_be_visible(
                    timeout=10_000
                )
                expect(page.get_by_test_id("tp-judge-result-json")).to_contain_text(
                    "deterministic failure at sequence step 2"
                )

                sequence = page.get_by_test_id("tp-report-sequences")
                expect(sequence).to_be_visible(timeout=10_000)
                rows = sequence.locator("tbody tr")
                expect(rows).to_have_count(3)
                for index, task_id in enumerate(task_ids):
                    expect(rows.nth(index)).to_contain_text(task_id)
                    expect(rows.nth(index)).to_contain_text(f"Step {index + 1}")
                expect(
                    page.get_by_text(
                        "Every selected-lane episode must have outcome PASS."
                    )
                ).to_be_visible()
                assert page.get_by_role(
                    "button", name="Promote baseline"
                ).is_disabled()

                with httpx.Client(
                    base_url=stack["api_url"], timeout=10.0
                ) as client:
                    slow_workflow_version = _seed_public_single_workflow(
                        client,
                        agent="deterministic-slow",
                        suffix="cancel",
                    )
                cancelled_run_id = _create_run_in_browser(
                    page,
                    slow_workflow_version,
                    idempotency_key="tp-h06-browser-cancel",
                    name="TP-H06 Browser Cancellation",
                )
                page.goto(
                    f"{stack['web_url']}/test-platform/runs/{cancelled_run_id}"
                )
                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "running", timeout=5_000
                )
                page.get_by_role("button", name="Cancel run").click()
                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "cancelled", timeout=10_000
                )
                expect(page.get_by_test_id("tp-active-workers")).to_have_text("0")
                with httpx.Client(
                    base_url=stack["api_url"], timeout=10.0
                ) as client:
                    cancelled_detail = client.get(
                        f"/api/platform/v1/runs/{cancelled_run_id}"
                    ).json()
                    assert cancelled_detail["state"] == "cancelled"
                    assert cancelled_detail["episode_attempts"][0]["outcome"] == (
                        "CANCELLED"
                    )

                    recovery_workflow_version = _seed_public_single_workflow(
                        client,
                        agent="deterministic-slow",
                        suffix="recovery",
                    )
                recovery_run_id = _create_run_in_browser(
                    page,
                    recovery_workflow_version,
                    idempotency_key="tp-h06-browser-recovery",
                    name="TP-H06 Browser Recovery",
                )
                page.goto(
                    f"{stack['web_url']}/test-platform/runs/{recovery_run_id}"
                )
                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "running", timeout=5_000
                )

                stack["api_process"].kill()
                stack["api_process"].wait(timeout=5)
                stack["api_process"] = _start_api_process(stack)
                _wait_for_url(f"{stack['api_url']}/health/ready")
                _wait_for_api_state(
                    stack["api_url"], recovery_run_id, "failed", timeout=10.0
                )
                page.reload()
                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "failed", timeout=10_000
                )
                expect(page.locator('[data-count="error"]')).to_have_text("1")
                with httpx.Client(
                    base_url=stack["api_url"], timeout=10.0
                ) as client:
                    resumed = client.post(
                        f"/api/platform/v1/runs/{recovery_run_id}/resume"
                    )
                    assert resumed.status_code == 202, resumed.text
                    assert resumed.json()["selected_lane_episodes"][0]["reason"] == (
                        "resume_service_restarted"
                    )
                page.reload()
                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "running", timeout=5_000
                )
                page.get_by_role("button", name="Cancel run").click()
                expect(page.get_by_test_id("tp-run-state")).to_have_text(
                    "cancelled", timeout=10_000
                )
            finally:
                browser.close()


def _target_config():
    return {
        "kind": "simulator",
        "connection": {"env_url": "http://deterministic.invalid"},
        "device_profile": {
            "name": "Deterministic Pixel",
            "viewport_width": 393,
            "viewport_height": 852,
            "physical_width": 1080,
            "physical_height": 2400,
            "device_scale_factor": 2.75,
        },
        "runtime": {},
        "labels": {"test_only": "true"},
    }


def _manual_sequence_definition(task_ids, target_id):
    return {
        "schema_version": 1,
        "name": "Ordered deterministic sequence",
        "nodes": [
            {
                "id": "tasks",
                "type": "task_selection",
                "depends_on": [],
                "config": {
                    "task_ids": task_ids,
                    "order_policy": "manual",
                    "sample_n": 1,
                },
            },
            {
                "id": "matrix",
                "type": "matrix",
                "depends_on": ["tasks"],
                "config": {
                    "lanes": {
                        "candidate": {
                            "target_id": target_id,
                            "role": "candidate",
                        }
                    },
                    "repeat_n": 1,
                },
            },
            {
                "id": "execute",
                "type": "execute",
                "depends_on": ["matrix"],
                "config": {
                    "agent": "deterministic",
                    "parallel": 1,
                    "processes": 1,
                    "execution_strategy": "linear_sequence",
                    "state_policy": "isolated",
                    "failure_policy": "continue",
                },
            },
        ],
    }


def _wait_for_state(client, run_id, expected, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/platform/v1/runs/{run_id}")
        assert response.status_code == 200
        detail = response.json()
        if detail["state"] == expected:
            return detail
        if detail["state"] in {"failed", "cancelled"}:
            pytest.fail(f"run reached unexpected state {detail['state']}: {detail}")
        time.sleep(0.05)
    pytest.fail(f"run did not reach {expected}")


def _seed_public_manual_sequence(client: httpx.Client):
    project_response = client.post(
        "/api/platform/v1/projects",
        json={"name": "Deterministic browser smoke"},
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    target_response = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": "Deterministic browser simulator",
            "config": _target_config(),
        },
    )
    assert target_response.status_code == 201, target_response.text
    target = target_response.json()
    health = client.post(f"/api/platform/v1/targets/{target['id']}/health")
    assert health.status_code == 200, health.text
    assert health.json()["executable"] is True

    tasks_response = client.get("/api/platform/v1/tasks")
    assert tasks_response.status_code == 200, tasks_response.text
    task_ids = [
        item["task_base_id"] for item in tasks_response.json()["items"][:3]
    ]
    assert len(task_ids) == 3
    workflow_response = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "Browser ordered deterministic sequence",
            "definition": _manual_sequence_definition(task_ids, target["id"]),
        },
    )
    assert workflow_response.status_code == 201, workflow_response.text
    published = client.post(
        f"/api/platform/v1/workflows/{workflow_response.json()['id']}/publish"
    )
    assert published.status_code == 200, published.text
    return published.json()["workflow_version_id"], task_ids


def _seed_public_single_workflow(
    client: httpx.Client,
    *,
    agent: str,
    suffix: str,
) -> str:
    project = client.post(
        "/api/platform/v1/projects",
        json={"name": f"Deterministic {suffix}"},
    ).json()
    target = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": f"Deterministic {suffix} simulator",
            "config": _target_config(),
        },
    ).json()
    health = client.post(f"/api/platform/v1/targets/{target['id']}/health")
    assert health.status_code == 200, health.text
    task_id = client.get("/api/platform/v1/tasks").json()["items"][0][
        "task_base_id"
    ]
    definition = {
        "schema_version": 1,
        "name": f"Deterministic {suffix}",
        "nodes": [
            {
                "id": "tasks",
                "type": "task_selection",
                "depends_on": [],
                "config": {"task_ids": [task_id], "sample_n": 1},
            },
            {
                "id": "matrix",
                "type": "matrix",
                "depends_on": ["tasks"],
                "config": {
                    "lanes": {
                        "candidate": {
                            "target_id": target["id"],
                            "role": "candidate",
                        }
                    },
                    "repeat_n": 1,
                },
            },
            {
                "id": "execute",
                "type": "execute",
                "depends_on": ["matrix"],
                "config": {"agent": agent, "parallel": 1, "processes": 1},
            },
        ],
    }
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={"name": f"Deterministic {suffix}", "definition": definition},
    )
    assert workflow.status_code == 201, workflow.text
    published = client.post(
        f"/api/platform/v1/workflows/{workflow.json()['id']}/publish"
    )
    assert published.status_code == 200, published.text
    return str(published.json()["workflow_version_id"])


def _create_run_in_browser(
    page,
    workflow_version_id: str,
    *,
    idempotency_key: str,
    name: str,
) -> str:
    created = page.evaluate(
        """
        async ({ workflowVersionId, idempotencyKey, name }) => {
          const response = await fetch('/api/platform/v1/runs', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Idempotency-Key': idempotencyKey,
            },
            body: JSON.stringify({
              workflow_version_id: workflowVersionId,
              name,
              overrides: {
                seed: 606,
                execution: {
                  model_base_url: 'http://deterministic.invalid/v1',
                  model_name: 'deterministic',
                },
              },
            }),
          });
          return { status: response.status, body: await response.json() };
        }
        """,
        {
            "workflowVersionId": workflow_version_id,
            "idempotencyKey": idempotency_key,
            "name": name,
        },
    )
    assert created["status"] == 201, created
    return str(created["body"]["id"])


@contextmanager
def _running_browser_stack(tmp_path):
    repo_root = Path(__file__).resolve().parents[3]
    api_port = _free_port()
    web_port = _free_port()
    api_url = f"http://127.0.0.1:{api_port}"
    web_url = f"http://127.0.0.1:{web_port}"
    env = os.environ.copy()
    env.update(
        {
            "TEST_PLATFORM_HOST": "127.0.0.1",
            "TEST_PLATFORM_PORT": str(api_port),
            "TEST_PLATFORM_DATABASE_PATH": str(tmp_path / "platform.sqlite3"),
            "TEST_PLATFORM_RUNS_DIR": str(tmp_path / "runs"),
        }
    )
    api_process = _spawn_api_process(repo_root, env)
    web_env = env | {"TEST_PLATFORM_API_URL": api_url}
    web_process = subprocess.Popen(
        ["npx", "vite", "--host", "127.0.0.1", "--port", str(web_port), "--strictPort"],
        cwd=repo_root,
        env=web_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    stack = {
        "api_url": api_url,
        "web_url": web_url,
        "api_process": api_process,
        "web_process": web_process,
        "api_env": env,
        "repo_root": repo_root,
    }
    try:
        _wait_for_url(f"{api_url}/health/ready")
        _wait_for_url(f"{web_url}/test-platform/runs")
        yield stack
    finally:
        _stop_process(web_process)
        _stop_process(stack["api_process"])


def _spawn_api_process(repo_root: Path, env: dict[str, str]):
    return subprocess.Popen(
        [sys.executable, "-m", "test_platform.testing.server"],
        cwd=repo_root,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _start_api_process(stack):
    return _spawn_api_process(stack["repo_root"], stack["api_env"])


def _wait_for_url(url: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=1.0)
            if response.status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    pytest.fail(f"service did not become ready: {url}")


def _wait_for_api_state(
    api_url: str,
    run_id: str,
    expected: str,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    last_state = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"{api_url}/api/platform/v1/runs/{run_id}", timeout=1.0
            )
            if response.status_code == 200:
                last_state = response.json()["state"]
                if last_state == expected:
                    return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    pytest.fail(f"run remained {last_state!r}; expected {expected!r}")


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
