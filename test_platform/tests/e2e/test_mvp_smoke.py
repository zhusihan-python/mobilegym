from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, quote, urlparse

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


def test_deterministic_paired_comparison_classifies_regression_and_stable(tmp_path):
    """TP-H07 REST tracer: a paired deterministic run yields one stable_pass
    and one regression, with shared identity, ok integrity, and a failed gate."""
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
        workflow_version_id, task_ids = _seed_public_paired_workflow(client)
        created = client.post(
            "/api/platform/v1/runs",
            headers={"Idempotency-Key": "tp-h07-paired-rest"},
            json={
                "workflow_version_id": workflow_version_id,
                "name": "TP-H07 Paired REST",
                "overrides": {
                    "seed": 707,
                    "execution": {
                        "model_base_url": "http://deterministic.invalid/v1",
                        "model_name": "deterministic",
                    },
                },
            },
        )
        assert created.status_code == 201, created.text
        run_id = created.json()["id"]
        detail = _wait_for_state(client, run_id, "completed", timeout=30.0)

        # Exactly two prepared episode identities.
        identities = detail["episode_identities"]
        assert len(identities) == 2

        # Baseline + candidate attempts pair by episode_key.
        attempts = detail["episode_attempts"]
        by_key: dict[str, dict[str, str]] = {}
        for attempt in attempts:
            by_key.setdefault(attempt["episode_key"], {})[attempt["lane_key"]] = attempt
        assert len(by_key) == 2
        for episode_key, lanes in by_key.items():
            assert set(lanes.keys()) == {"baseline", "candidate"}, (
                f"episode {episode_key} missing a lane"
            )

        comparison = client.get(
            f"/api/platform/v1/runs/{run_id}/comparison"
        ).json()
        pairs = comparison["pairs"]
        assert len(pairs) == 2

        # No duplicate or missing pair_keys.
        pair_keys = [pair["pair_key"] for pair in pairs]
        assert len(set(pair_keys)) == 2

        # Classification multiset is exactly {stable_pass, regression}.
        classifications = sorted(pair["classification"] for pair in pairs)
        assert classifications == ["regression", "stable_pass"], classifications

        # Every pair has OK integrity (API returns the enum value; the browser
        # lowercases it for display).
        for pair in pairs:
            assert pair["integrity"]["status"].lower() == "ok", pair

        # Each pair references the same episode_key for both lanes, and the
        # three projection hashes (prepared + baseline actual + candidate actual)
        # are equal — proving shared prepared identity.
        identity_by_key = {ident["episode_key"]: ident for ident in identities}
        for pair in pairs:
            baseline_attempt_id = pair["baseline_episode_attempt_id"]
            candidate_attempt_id = pair["candidate_episode_attempt_id"]
            baseline_key = next(
                a["episode_key"]
                for a in attempts
                if a.get("episode_attempt_id") == baseline_attempt_id
            )
            candidate_key = next(
                a["episode_key"]
                for a in attempts
                if a.get("episode_attempt_id") == candidate_attempt_id
            )
            assert baseline_key == candidate_key, pair

            integrity = pair["integrity"]
            prepared_hash = pair["prepared"]["projection_hash"]
            assert integrity["prepared_projection_hash"] == prepared_hash, pair
            assert integrity["baseline_actual_projection_hash"] == prepared_hash, pair
            assert integrity["candidate_actual_projection_hash"] == prepared_hash, pair

            # The pair maps uniquely to an identity with an instance_seed.
            shared_key = baseline_key
            assert shared_key in identity_by_key, pair
            assert identity_by_key[shared_key]["instance_seed"] is not None

        report = client.get(f"/api/platform/v1/runs/{run_id}/report").json()
        # Gate is failed because max_regressions=0 and there is 1 regression.
        assert report["gate"]["verdict"] == "failed", report["gate"]
        gate_reasons = report["gate"]["reasons"]
        assert any(r["metric"] == "max_regressions" for r in gate_reasons), gate_reasons

        # Coverage: every pair paired, none unpaired.
        coverage = report["comparison"]["coverage"]
        assert coverage["total_pairs"] == 2
        assert coverage["paired_pairs"] == 2
        assert coverage["unpaired_pairs"] == 0
        assert coverage["coverage_rate"] == 1.0

        # Regression count is 1 (note the plural key in classification_counts).
        classification_counts = report["comparison"]["classification_counts"]
        assert classification_counts.get("regressions", 0) == 1
        assert classification_counts.get("stable_pass", 0) == 1


def test_browser_observes_paired_baseline_candidate_comparison_and_replay(tmp_path):
    with _running_browser_stack(tmp_path) as stack:
        with httpx.Client(base_url=stack["api_url"], timeout=10.0) as client:
            workflow_version_id, task_ids = _seed_public_paired_workflow(client)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1440, "height": 1000},
                permissions=["clipboard-read", "clipboard-write"],
            )
            page = context.new_page()
            try:
                page.goto(f"{stack['web_url']}/test-platform/runs")
                created = page.evaluate(
                    """
                    async ({ workflowVersionId }) => {
                      const response = await fetch('/api/platform/v1/runs', {
                        method: 'POST',
                        headers: {
                          'Content-Type': 'application/json',
                          'Idempotency-Key': 'tp-h07-paired-browser',
                        },
                        body: JSON.stringify({
                          workflow_version_id: workflowVersionId,
                          name: 'TP-H07 Paired Browser',
                          overrides: {
                            seed: 707,
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
                    "completed", timeout=30_000
                )

                # Comparison panel renders both pairs with the expected
                # classifications (regression + stable_pass). The comparison
                # data loads asynchronously after the run completes.
                comparison = page.get_by_test_id("tp-comparison")
                expect(comparison).to_be_visible(timeout=10_000)
                pair_rows = comparison.locator("[data-testid^='tp-comparison-pair-']")
                expect(pair_rows).to_have_count(2, timeout=15_000)
                classification_texts = sorted(
                    row.locator("[data-testid='tp-pair-classification']").inner_text()
                    for row in [pair_rows.nth(i) for i in range(2)]
                )
                assert classification_texts == ["regression", "stable_pass"], (
                    classification_texts
                )

                # Every pair integrity reads OK (the API returns the enum value
                # "OK"; the UI renders "OK (OK)" — status + reason).
                for i in range(2):
                    integrity_text = pair_rows.nth(i).locator(
                        "[data-testid='tp-pair-integrity']"
                    ).inner_text()
                    assert "ok" in integrity_text.lower(), integrity_text

                # Gate is failed because max_regressions=0 with one regression.
                expect(page.get_by_test_id("tp-gate-verdict")).to_have_text("failed")
                expect(page.get_by_test_id("tp-report-regressions")).to_have_text("1")

                # Pair coverage is rendered in the comparison panel.
                coverage_text = page.get_by_test_id(
                    "tp-comparison-coverage"
                ).inner_text(timeout=10_000)
                assert "total_pairs: 2" in coverage_text, coverage_text
                assert "paired_pairs: 2" in coverage_text, coverage_text
                assert "unpaired_pairs: 0" in coverage_text, coverage_text
                assert "coverage_rate: 1" in coverage_text, coverage_text

                # Runtime delta is rendered (exact % varies with timing jitter).
                runtime_delta = page.get_by_test_id("tp-report-runtime-delta")
                expect(runtime_delta).to_be_visible()
                assert runtime_delta.inner_text().strip() != "—"

                # Replay defaults to candidate lane. Switch to baseline for the
                # regression pair's episode, then back, without identity drift.
                # Reload once the run is terminal so the snapshot carries the
                # complete set of episode attempts (4 = 2 episodes x 2 lanes).
                page.reload()
                picker = page.get_by_label("Replay episode")
                expect(picker.locator("option")).to_have_count(4, timeout=15_000)

                options = picker.locator("option")
                option_count = options.count()

                def _parse_option(value):
                    """Return (lane_key, episode_key) from a 'lane::episode::attempt' value."""
                    parts = (value or "").split("::")
                    if len(parts) < 3:
                        return None, None
                    return parts[0], parts[1]

                # Find the candidate option whose outcome is FAIL (the regression
                # pair's candidate attempt).
                candidate_regression_value = None
                regression_episode_key = None
                for i in range(option_count):
                    text = options.nth(i).inner_text()
                    if "candidate" in text and "FAIL" in text:
                        candidate_regression_value = options.nth(i).get_attribute("value")
                        _, regression_episode_key = _parse_option(candidate_regression_value)
                        break
                assert candidate_regression_value is not None, "no candidate FAIL option"
                assert regression_episode_key is not None

                # Candidate is selected by default for the regression episode.
                expect(picker).to_have_value(candidate_regression_value)

                # Agent console shows candidate lane + the regression episode.
                console = page.get_by_label("Replay console")
                expect(console.locator("dd").filter(has_text="candidate")).to_be_visible()
                expect(
                    console.locator("dd").filter(has_text=regression_episode_key)
                ).to_be_visible()

                # Screenshot loads a candidate artifact.
                def _screenshot_src():
                    return page.get_by_test_id("tp-replay-screenshot").get_attribute("src")

                expect(page.get_by_test_id("tp-replay-screenshot")).to_be_visible(timeout=10_000)
                candidate_src = _screenshot_src()
                assert candidate_src and "/artifacts/" in candidate_src

                # Find the baseline option with the EXACT same episode_key.
                baseline_regression_value = None
                for i in range(option_count):
                    value = options.nth(i).get_attribute("value") or ""
                    lane_key, ep_key = _parse_option(value)
                    if lane_key == "baseline" and ep_key == regression_episode_key:
                        baseline_regression_value = value
                        break
                assert baseline_regression_value is not None, (
                    "no baseline option for regression episode"
                )

                # Switch to baseline.
                picker.select_option(baseline_regression_value)
                expect(picker).to_have_value(baseline_regression_value)
                # Agent console lane updated to baseline, episode unchanged.
                expect(console.locator("dd").filter(has_text="baseline")).to_be_visible()
                expect(
                    console.locator("dd").filter(has_text=regression_episode_key)
                ).to_be_visible()
                # Screenshot loaded a different (baseline) artifact.
                expect(page.get_by_test_id("tp-replay-screenshot")).to_be_visible(timeout=10_000)
                baseline_src = _screenshot_src()
                assert baseline_src and "/artifacts/" in baseline_src
                assert baseline_src != candidate_src, (
                    "screenshot src did not change after switching lanes"
                )

                # Switch back to candidate — no identity drift (same episode).
                picker.select_option(candidate_regression_value)
                expect(picker).to_have_value(candidate_regression_value)
                expect(console.locator("dd").filter(has_text="candidate")).to_be_visible()
                expect(
                    console.locator("dd").filter(has_text=regression_episode_key)
                ).to_be_visible()
                expect(page.get_by_test_id("tp-replay-screenshot")).to_be_visible(timeout=10_000)
                assert _screenshot_src() == candidate_src, "screenshot drifted after switching back"

                # TP-H15: the server-authoritative Retry preview identifies the
                # exact failed lane episode before the mutation is confirmed.
                retry_preview = page.get_by_test_id("tp-retry-preview")
                expect(retry_preview).to_be_visible(timeout=10_000)
                preview_items = retry_preview.locator("li")
                expect(preview_items).to_have_count(1)
                expect(preview_items.first).to_contain_text("candidate")
                expect(preview_items.first).to_contain_text("retry_failed")
                expect(preview_items.first).to_contain_text(regression_episode_key)

                # Select a stable incident location, copy it, and assert the
                # clipboard URL contains only the public observatory contract.
                page.get_by_label("Step timeline").locator("button").first.click()
                page.get_by_label("Replay screenshot mode").select_option("raw")
                page.get_by_role("tab", name="Response").click()
                page.get_by_role("button", name="Copy incident link").click()
                expect(page.get_by_text("Incident link copied.")).to_be_visible()
                incident_url = page.evaluate("navigator.clipboard.readText()")
                parsed_incident = urlparse(incident_url)
                incident_query = parse_qs(parsed_incident.query)
                assert parsed_incident.path == f"/test-platform/runs/{run_id}"
                assert incident_query == {
                    "lane": ["candidate"],
                    "episode": [regression_episode_key],
                    "attempt": ["1"],
                    "step": ["1"],
                    "screenshot": ["raw"],
                    "evidence": ["response"],
                }
                assert "secret" not in incident_url
                assert "artifact_root" not in incident_url

                # Confirm through the UI and verify the created attempt contains
                # exactly the episode shown by the preview.
                retry_result = {}

                def _capture_retry_response(response):
                    if (
                        urlparse(response.url).path
                        == f"/api/platform/v1/runs/{run_id}/retry"
                        and response.request.method == "POST"
                    ):
                        retry_result.update(response.json())

                page.on("response", _capture_retry_response)
                page.get_by_role("button", name="Retry run").click()
                expect(page.get_by_test_id("tp-followup-message")).to_contain_text(
                    "Retry queued attempt 2 for 1 lane episodes.", timeout=10_000
                )
                assert retry_result["selected_lane_episodes"] == [
                    {
                        "lane_key": "candidate",
                        "episode_key": regression_episode_key,
                        "reason": "retry_failed",
                    }
                ]
                with httpx.Client(
                    base_url=stack["api_url"], timeout=10.0
                ) as client:
                    retried_detail = _wait_for_state(
                        client, run_id, "completed", timeout=30.0
                    )
                assert any(
                    attempt["attempt_no"] == 2
                    for attempt in retried_detail["run_attempts"]
                )

                # A clean page and a reload both preserve the immutable attempt-1
                # replay even though attempt 2 now exists.
                incident_page = context.new_page()
                incident_page.goto(incident_url)
                incident_picker = incident_page.get_by_label("Replay episode")
                expect(incident_picker).to_have_value(
                    candidate_regression_value, timeout=15_000
                )
                expect(incident_page.get_by_role("tab", name="Response")).to_have_attribute(
                    "aria-selected", "true"
                )
                expect(incident_page.get_by_label("Replay screenshot mode")).to_have_value(
                    "raw"
                )
                incident_page.reload()
                expect(incident_picker).to_have_value(
                    candidate_regression_value, timeout=15_000
                )
                incident_page.close()
            finally:
                browser.close()


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


def _paired_definition(task_ids, target_id):
    """Two-lane baseline/candidate batch with a compare + gate node.

    Plain batch (no ``linear_sequence``) so a compare node is legal. The
    candidate lane carries a test-only ``failing_task_id`` so the deterministic
    agent can produce one regression and one stable pair.
    """
    return {
        "schema_version": 1,
        "name": "Deterministic paired comparison",
        "nodes": [
            {
                "id": "tasks",
                "type": "task_selection",
                "depends_on": [],
                "config": {"task_ids": task_ids, "sample_n": 1},
            },
            {
                "id": "matrix",
                "type": "matrix",
                "depends_on": ["tasks"],
                "config": {
                    "lanes": {
                        "baseline": {
                            "target_id": target_id,
                            "role": "baseline",
                        },
                        "candidate": {
                            "target_id": target_id,
                            "failing_task_id": task_ids[1],
                        },
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
                },
            },
            {
                "id": "compare",
                "type": "compare",
                "depends_on": ["execute"],
                "config": {},
            },
            {
                "id": "gate",
                "type": "gate",
                "depends_on": ["compare"],
                "config": {"thresholds": {"max_regressions": 0}},
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


def _seed_public_paired_workflow(client: httpx.Client):
    project = client.post(
        "/api/platform/v1/projects",
        json={"name": "Deterministic paired smoke"},
    ).json()
    target = client.post(
        "/api/platform/v1/targets",
        json={
            "project_id": project["id"],
            "name": "Deterministic paired simulator",
            "config": _target_config(),
        },
    ).json()
    health = client.post(f"/api/platform/v1/targets/{target['id']}/health")
    assert health.status_code == 200, health.text
    task_ids = [
        item["task_base_id"] for item in client.get("/api/platform/v1/tasks").json()["items"][:2]
    ]
    assert len(task_ids) == 2
    workflow = client.post(
        f"/api/platform/v1/projects/{project['id']}/workflows",
        json={
            "name": "Deterministic paired comparison",
            "definition": _paired_definition(task_ids, target["id"]),
        },
    )
    assert workflow.status_code == 201, workflow.text
    published = client.post(
        f"/api/platform/v1/workflows/{workflow.json()['id']}/publish"
    )
    assert published.status_code == 200, published.text
    return published.json()["workflow_version_id"], task_ids


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
        _vite_command(repo_root, web_port),
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


def _vite_command(repo_root: Path, port: int) -> list[str]:
    node = shutil.which("node")
    vite_entry = repo_root / "node_modules" / "vite" / "bin" / "vite.js"
    if node is None or not vite_entry.is_file():
        pytest.fail("The browser smoke requires Node.js and the local Vite installation.")
    return [
        node,
        str(vite_entry),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--strictPort",
    ]


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
