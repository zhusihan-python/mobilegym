"""Unit tests for model compatibility classification and the OpenAI adapter.

The adapter tests use ``httpx.MockTransport`` so the real SDK call chain runs
(OpenAI client construction → chat.completions.create → SDK exception mapping)
without any live network access.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from openai import OpenAI

from test_platform.adapters.model_compatibility import (
    OpenAICompatibilityProbe,
    default_client_factory,
)
from test_platform.domain.model_compatibility import (
    AUTHENTICATION_FAILURE,
    COMPATIBLE,
    INDETERMINATE,
    MISSING_MODEL,
    TIMEOUT,
    UNREACHABLE,
    UNSUPPORTED_IMAGE_FORMAT,
    UNSUPPORTED_VISION,
    classify_response,
    explanation_for,
)


# ---- Pure classification function ------------------------------------------


class TestClassifyResponse:
    def test_200_is_compatible(self):
        assert classify_response(status_code=200, body_text="") == COMPATIBLE

    def test_401_is_authentication_failure(self):
        assert classify_response(
            status_code=401, body_text="invalid api key"
        ) == AUTHENTICATION_FAILURE

    def test_403_is_authentication_failure(self):
        assert classify_response(status_code=403, body_text="forbidden") == AUTHENTICATION_FAILURE

    def test_404_with_model_keyword_is_missing_model(self):
        assert classify_response(
            status_code=404, body_text="The model 'foo' does not exist"
        ) == MISSING_MODEL

    def test_404_without_model_keyword_is_indeterminate(self):
        assert classify_response(status_code=404, body_text="not found") == INDETERMINATE

    def test_400_with_format_keyword_is_unsupported_image_format(self):
        assert classify_response(
            status_code=400, body_text="invalid base64 data url"
        ) == UNSUPPORTED_IMAGE_FORMAT

    def test_400_with_vision_keyword_is_unsupported_vision(self):
        assert classify_response(
            status_code=400, body_text="model does not support image input"
        ) == UNSUPPORTED_VISION

    def test_400_mixed_image_and_base64_prefers_format(self):
        """Regression: body mentioning both 'image' and 'base64 format' must
        classify as unsupported_image_format, not unsupported_vision."""
        assert classify_response(
            status_code=400, body_text="image base64 format is invalid"
        ) == UNSUPPORTED_IMAGE_FORMAT

    def test_400_unclassifiable_is_indeterminate(self):
        assert classify_response(status_code=400, body_text="bad request") == INDETERMINATE

    def test_422_with_vision_keyword_is_unsupported_vision(self):
        assert classify_response(
            status_code=422, body_text="no vision capability"
        ) == UNSUPPORTED_VISION

    def test_429_is_indeterminate(self):
        assert classify_response(status_code=429, body_text="rate limit") == INDETERMINATE

    def test_500_is_indeterminate(self):
        assert classify_response(status_code=500, body_text="server error") == INDETERMINATE

    def test_no_status_timeout_exception(self):
        assert classify_response(
            status_code=None, body_text="", exc_type="APITimeoutError"
        ) == TIMEOUT

    def test_no_status_connection_exception(self):
        assert classify_response(
            status_code=None, body_text="", exc_type="APIConnectionError"
        ) == UNREACHABLE

    def test_no_status_unknown_exception_is_indeterminate(self):
        assert classify_response(
            status_code=None, body_text="", exc_type="ValueError"
        ) == INDETERMINATE

    def test_explanation_never_echoes_body(self):
        for code in [
            COMPATIBLE, AUTHENTICATION_FAILURE, MISSING_MODEL,
            UNSUPPORTED_VISION, UNSUPPORTED_IMAGE_FORMAT, TIMEOUT,
            UNREACHABLE, INDETERMINATE,
        ]:
            expl = explanation_for(code, timeout_seconds=15)
            assert "api key" not in expl.lower()
            assert "http" not in expl.lower()


# ---- OpenAI adapter via MockTransport --------------------------------------


def _ok_response() -> httpx.Response:
    return httpx.Response(
        status_code=200,
        json={
            "id": "chatcmpl-probe",
            "object": "chat.completion",
            "model": "test-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "."}, "finish_reason": "length"}],
        },
    )


def _error_response(status_code: int, message: str) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json={"error": {"message": message, "type": "invalid_request_error"}},
    )


def _make_probe_with_transport(handler) -> OpenAICompatibilityProbe:
    """Build a probe whose OpenAI client uses a MockTransport-backed httpx client."""
    transport = httpx.MockTransport(handler)
    closed = {"yes": False}

    original = httpx.Client

    class _TrackingClient(original):
        def close(self):
            closed["yes"] = True
            super().close()

    def factory(*, base_url, api_key, timeout_seconds):
        http_client = _TrackingClient(transport=transport)
        # Patch close tracking: we need to know the OpenAI client closed it.
        http_client._tp_closed_flag = closed  # type: ignore[attr-defined]
        return OpenAI(
            base_url=base_url.rstrip("/"),
            api_key=api_key or "",
            max_retries=0,
            timeout=httpx.Timeout(timeout_seconds),
            http_client=http_client,
        )

    probe = OpenAICompatibilityProbe(client_factory=factory)
    return probe, closed


class TestOpenAICompatibilityProbe:
    def test_compatible_success(self):
        def handler(request):
            return _ok_response()

        probe, closed = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        assert result.code == COMPATIBLE
        assert result.checked_model == "test-model"
        assert result.checked_image_format == "data_url"
        assert result.latency_ms >= 0
        # The OpenAI client (and its httpx connection pool) must be closed.
        assert closed["yes"] is True

    def test_authentication_failure_401(self):
        def handler(request):
            return _error_response(401, "Incorrect API key provided")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-bad",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        assert result.code == AUTHENTICATION_FAILURE

    def test_missing_model_404(self):
        def handler(request):
            return _error_response(404, "The model 'nope' does not exist")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="nope",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        assert result.code == MISSING_MODEL

    def test_unsupported_image_format_400(self):
        def handler(request):
            return _error_response(400, "Invalid base64 data url format")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        assert result.code == UNSUPPORTED_IMAGE_FORMAT

    def test_unsupported_vision_400(self):
        def handler(request):
            return _error_response(400, "This model does not support image input")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        assert result.code == UNSUPPORTED_VISION

    def test_timeout_connect_timeout(self):
        """httpx.ConnectTimeout must classify as the stable timeout code."""
        def handler(request):
            raise httpx.ConnectTimeout("simulated connect timeout")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=2,
        )
        assert result.code == TIMEOUT

    def test_timeout_read_timeout(self):
        """httpx.ReadTimeout must also classify as the stable timeout code."""
        def handler(request):
            raise httpx.ReadTimeout("simulated read timeout")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=2,
        )
        assert result.code == TIMEOUT

    def test_unreachable_connect_error(self):
        """httpx.ConnectError must classify as the stable unreachable code."""
        def handler(request):
            raise httpx.ConnectError("connection refused")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=2,
        )
        assert result.code == UNREACHABLE

    def test_indeterminate_500(self):
        def handler(request):
            return _error_response(500, "internal server error")

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        assert result.code == INDETERMINATE

    def test_authorization_and_image_content_sent(self):
        """The MockTransport handler verifies the SDK received the correct
        Authorization header, model, and image_url content part."""
        captured = {}

        def handler(request):
            captured["auth"] = request.headers.get("authorization")
            body = json.loads(request.content)
            captured["model"] = body.get("model")
            captured["max_tokens"] = body.get("max_tokens")
            messages = body.get("messages", [])
            if messages:
                content = messages[0].get("content", [])
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        captured["image_url"] = part["image_url"]["url"]
            return _ok_response()

        probe, _ = _make_probe_with_transport(handler)
        probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-sentinel-secret",
            model="vision-model",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        assert captured["auth"] == "Bearer sk-sentinel-secret"
        assert captured["model"] == "vision-model"
        assert captured["max_tokens"] == 1
        assert captured["image_url"].startswith("data:image/png;base64,")

    def test_bare_base64_strips_data_url_prefix(self):
        """When image_url_format=bare_base64, the probe sends raw base64
        (proving _format_image_url_messages is applied)."""
        captured = {}

        def handler(request):
            body = json.loads(request.content)
            messages = body.get("messages", [])
            if messages:
                content = messages[0].get("content", [])
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        captured["image_url"] = part["image_url"]["url"]
            return _ok_response()

        probe, _ = _make_probe_with_transport(handler)
        probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-test",
            model="test-model",
            image_url_format="bare_base64",
            timeout_seconds=10,
        )
        assert not captured["image_url"].startswith("data:")
        assert captured["image_url"]  # non-empty base64

    def test_result_does_not_contain_api_key(self):
        """CompatibilityResult must never carry the api_key."""
        def handler(request):
            return _ok_response()

        probe, _ = _make_probe_with_transport(handler)
        result = probe.check(
            base_url="http://test-provider.invalid/v1",
            api_key="sk-sentinel-secret",
            model="test-model",
            image_url_format="data_url",
            timeout_seconds=10,
        )
        result_dict = {
            "code": result.code,
            "explanation": result.explanation,
            "latency_ms": result.latency_ms,
            "checked_model": result.checked_model,
            "checked_image_format": result.checked_image_format,
        }
        assert "sk-sentinel-secret" not in json.dumps(result_dict)


# ---- Probe PNG validity ----------------------------------------------------


class TestProbeImage:
    def test_minimal_png_is_valid_64x64(self):
        """The probe image must be a decodable 64x64 PNG."""
        from test_platform.adapters.model_compatibility import _MINIMAL_PNG_BYTES

        from PIL import Image

        img = Image.open(__import__("io").BytesIO(_MINIMAL_PNG_BYTES))
        img.verify()  # raises if corrupt

        # Re-open for size (verify() leaves the image in an unusable state).
        img = Image.open(__import__("io").BytesIO(_MINIMAL_PNG_BYTES))
        assert img.size == (64, 64)
        assert img.format == "PNG"
