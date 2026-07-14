from fastapi.testclient import TestClient
import pytest

from test_platform.api.app import create_app
from test_platform.config import PlatformSettings


def _settings(tmp_path):
    return PlatformSettings(
        database_path=tmp_path / "platform.sqlite3",
        runs_dir=tmp_path / "runs",
    )


def _public_spec():
    return {
        "schema_version": 1,
        "agent": {"id": "generic_v2"},
        "model": {
            "protocol": "openai_chat_completions",
            "base_url": "http://127.0.0.1:1234/v1/",
            "name": "  local-model  ",
        },
        "image_input": {"format": "data_url"},
        "generation": {
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 4096,
            "stream": True,
        },
        "inference": {"timeout_seconds": 300},
        "credentials": {"required_slots": []},
    }


class ExplodingCompatibilityProbe:
    def __init__(self):
        self.calls = []

    def check(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("Publishing must not contact the model provider")


def test_project_creates_execution_profile_draft_with_normalized_public_spec(
    tmp_path,
):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={
                "name": "  Generic v2 / local model  ",
                "draft_spec": _public_spec(),
            },
        )

    assert response.status_code == 201
    body = response.json()
    assert body["project_id"] == project["id"]
    assert body["name"] == "Generic v2 / local model"
    assert body["draft_spec"] == {
        **_public_spec(),
        "model": {
            "protocol": "openai_chat_completions",
            "base_url": "http://127.0.0.1:1234/v1",
            "name": "local-model",
        },
    }
    assert body["head_revision"] is None


def test_publish_creates_immutable_revision_one_without_remote_provider_call(
    tmp_path,
):
    probe = ExplodingCompatibilityProbe()
    app = create_app(_settings(tmp_path), compatibility_probe=probe)

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        profile = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Generic v2 / local model", "draft_spec": _public_spec()},
        ).json()

        published = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/publish"
        )

        assert published.status_code == 200
        revision = published.json()
        assert revision["execution_profile_id"] == profile["id"]
        assert revision["revision_no"] == 1
        assert revision["public_spec"] == profile["draft_spec"]
        assert revision["public_spec_hash"].startswith("sha256:")
        assert len(revision["public_spec_hash"]) == len("sha256:") + 64
        assert revision["credential_binding_digest"].startswith("sha256:")

        reloaded = client.get(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profile-revisions/{revision['id']}"
        )

    assert reloaded.status_code == 200
    assert reloaded.json() == revision
    assert probe.calls == []


def test_publishing_another_revision_remains_unavailable_before_lifecycle_slice(
    tmp_path,
):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        profile = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Generic v2 / local model", "draft_spec": _public_spec()},
        ).json()
        first_revision = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/publish"
        ).json()

        second_publish = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/publish"
        )
        reloaded = client.get(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profile-revisions/{first_revision['id']}"
        )

    assert second_publish.status_code == 409
    assert second_publish.json()["error"]["code"] == (
        "EXECUTION_PROFILE_ALREADY_PUBLISHED"
    )
    assert reloaded.json() == first_revision


def test_draft_rejects_raw_secret_like_fields_without_echoing_values(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        spec = _public_spec()
        spec["model"]["api_key"] = "sk-must-never-echo"

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Unsafe profile", "draft_spec": spec},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == (
        "EXECUTION_PROFILE_SECRET_VALUE_FORBIDDEN"
    )
    assert response.json()["error"]["details"] == [{"field": "model.api_key"}]
    assert "sk-must-never-echo" not in response.text


@pytest.mark.parametrize(
    "base_url",
    [
        "https://user:password@models.example/v1",
        "https://models.example/v1?api_key=must-not-echo",
        "https://models.example/v1?signature=must-not-echo",
        "https://models.example/v1?region=private-routing",
    ],
)
def test_draft_rejects_credential_bearing_model_urls(tmp_path, base_url):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        spec = _public_spec()
        spec["model"]["base_url"] = base_url

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Unsafe URL", "draft_spec": spec},
        )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "EXECUTION_PROFILE_URL_CREDENTIALS_FORBIDDEN",
        "message": "Execution Profile URLs must not contain user info or query parameters.",
        "details": [{"field": "model.base_url"}],
        "request_id": response.json()["error"]["request_id"],
    }
    assert "must-not-echo" not in response.text
    assert "private-routing" not in response.text
    assert "password" not in response.text


def test_list_and_detail_reload_normalized_public_settings_and_exact_head(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        profile = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Generic v2 / local model", "draft_spec": _public_spec()},
        ).json()
        revision = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/publish"
        ).json()

        listed = client.get(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles"
        )
        detail = client.get(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}"
        )

    assert listed.status_code == 200
    assert listed.json()["next_cursor"] is None
    assert listed.json()["items"] == [detail.json()]
    assert detail.status_code == 200
    assert detail.json()["draft_spec"]["model"] == {
        "protocol": "openai_chat_completions",
        "base_url": "http://127.0.0.1:1234/v1",
        "name": "local-model",
    }
    assert detail.json()["head_revision"] == revision
    assert '"api_key"' not in detail.text
    assert '"secret_value"' not in detail.text


def test_saving_a_changed_draft_does_not_mutate_published_revision(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        profile = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Generic v2 / local model", "draft_spec": _public_spec()},
        ).json()
        revision = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/publish"
        ).json()
        changed_spec = _public_spec()
        changed_spec["model"]["name"] = "next-model"

        saved = client.patch(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/draft",
            json={"draft_spec": changed_spec},
        )
        reloaded_revision = client.get(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profile-revisions/{revision['id']}"
        )

    assert saved.status_code == 200
    assert saved.json()["draft_spec"]["model"]["name"] == "next-model"
    assert saved.json()["head_revision"] == revision
    assert reloaded_revision.status_code == 200
    assert reloaded_revision.json() == revision


@pytest.mark.parametrize(
    ("case", "field"),
    [
        ("missing_model_name", "model.name"),
        ("unsupported_image_format", "image_input.format"),
        ("credential_slots_before_binding_support", "credentials.required_slots"),
        ("coerced_stream", "generation.stream"),
    ],
)
def test_static_validation_rejects_incomplete_or_unsupported_specs(
    tmp_path,
    case,
    field,
):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        spec = _public_spec()
        if case == "missing_model_name":
            spec["model"].pop("name")
        elif case == "unsupported_image_format":
            spec["image_input"]["format"] = "remote_url"
        elif case == "credential_slots_before_binding_support":
            spec["credentials"]["required_slots"] = ["model_api_key"]
        else:
            spec["generation"]["stream"] = 1

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Invalid profile", "draft_spec": spec},
        )
        listed = client.get(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles"
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EXECUTION_PROFILE_SPEC_INVALID"
    assert response.json()["error"]["details"][0]["field"] == field
    assert listed.json()["items"] == []


def test_canonical_public_hash_is_stable_for_equivalent_normalized_specs(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        first = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Spaced", "draft_spec": _public_spec()},
        ).json()
        normalized_spec = _public_spec()
        normalized_spec["model"] = {
            "name": "local-model",
            "base_url": "http://127.0.0.1:1234/v1",
            "protocol": "openai_chat_completions",
        }
        second = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Normalized", "draft_spec": normalized_spec},
        ).json()

        first_revision = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{first['id']}/publish"
        ).json()
        second_revision = client.post(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{second['id']}/publish"
        ).json()

    assert first_revision["public_spec_hash"] == (
        "sha256:3d71ef81a34bc6d78054f83d6c094360be1893f79b2d1ffac1bc79a90d29c37d"
    )
    assert second_revision["public_spec_hash"] == first_revision["public_spec_hash"]


def test_selected_project_cannot_discover_or_publish_another_projects_profile(
    tmp_path,
):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        owner = client.post(
            "/api/platform/v1/projects",
            json={"name": "Owner"},
        ).json()
        other = client.post(
            "/api/platform/v1/projects",
            json={"name": "Other"},
        ).json()
        profile = client.post(
            f"/api/platform/v1/projects/{owner['id']}/execution-profiles",
            json={"name": "Owner profile", "draft_spec": _public_spec()},
        ).json()
        revision = client.post(
            f"/api/platform/v1/projects/{owner['id']}"
            f"/execution-profiles/{profile['id']}/publish"
        ).json()

        other_list = client.get(
            f"/api/platform/v1/projects/{other['id']}/execution-profiles"
        )
        other_detail = client.get(
            f"/api/platform/v1/projects/{other['id']}"
            f"/execution-profiles/{profile['id']}"
        )
        other_publish = client.post(
            f"/api/platform/v1/projects/{other['id']}"
            f"/execution-profiles/{profile['id']}/publish"
        )
        other_revision = client.get(
            f"/api/platform/v1/projects/{other['id']}"
            f"/execution-profile-revisions/{revision['id']}"
        )
        missing_revision = client.get(
            f"/api/platform/v1/projects/{other['id']}"
            "/execution-profile-revisions/missing-revision"
        )

    assert other_list.status_code == 200
    assert other_list.json()["items"] == []
    assert other_detail.status_code == 404
    assert other_publish.status_code == 404
    assert other_publish.json()["error"]["code"] == "EXECUTION_PROFILE_NOT_FOUND"
    assert other_revision.status_code == 404
    assert missing_revision.status_code == 404
    for response in (other_revision, missing_revision):
        assert response.json()["error"]["code"] == (
            "EXECUTION_PROFILE_REVISION_NOT_FOUND"
        )
        assert response.json()["error"]["message"] == (
            "Execution Profile Revision was not found."
        )


def test_profile_name_must_remain_non_empty_after_normalization(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()

        response = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "   ", "draft_spec": _public_spec()},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EXECUTION_PROFILE_NAME_REQUIRED"


def test_updated_profile_name_must_remain_non_empty_after_normalization(tmp_path):
    app = create_app(_settings(tmp_path))

    with TestClient(app) as client:
        project = client.post(
            "/api/platform/v1/projects",
            json={"name": "Execution profiles"},
        ).json()
        profile = client.post(
            f"/api/platform/v1/projects/{project['id']}/execution-profiles",
            json={"name": "Kept name", "draft_spec": _public_spec()},
        ).json()

        response = client.patch(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}/draft",
            json={"name": "   ", "draft_spec": _public_spec()},
        )
        reloaded = client.get(
            f"/api/platform/v1/projects/{project['id']}"
            f"/execution-profiles/{profile['id']}"
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "EXECUTION_PROFILE_NAME_REQUIRED"
    assert reloaded.json()["name"] == "Kept name"
