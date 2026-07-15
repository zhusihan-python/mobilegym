"""Unit tests for the compatibility preflight gate and its TTL cache."""

from __future__ import annotations

from test_platform.domain.model_compatibility import (
    AUTHENTICATION_FAILURE,
    COMPATIBLE,
    INDETERMINATE,
    UNSUPPORTED_VISION,
    CompatibilityResult,
    explanation_for,
)
from test_platform.services.compatibility_preflight import CompatibilityPreflight


def _result(code: str, **kw) -> CompatibilityResult:
    defaults = {
        "code": code,
        "explanation": f"test {code}",
        "latency_ms": 10,
        "checked_model": "test-model",
        "checked_image_format": "data_url",
    }
    defaults.update(kw)
    return CompatibilityResult(**defaults)


class _FakeProbe:
    def __init__(self, results: list[CompatibilityResult]) -> None:
        self._results = list(results)
        self.calls = 0

    def check(self, **kw) -> CompatibilityResult:
        self.calls += 1
        return self._results.pop(0) if self._results else _result(COMPATIBLE)


class TestPreflightOutcome:
    def test_compatible_passes(self):
        probe = _FakeProbe([_result(COMPATIBLE)])
        pf = CompatibilityPreflight(probe)
        result = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="test-model",
            image_url_format="data_url",
        )
        assert result.outcome == "passed"
        assert result.code == COMPATIBLE
        assert result.cached is False
        assert probe.calls == 1

    def test_incompatible_fails(self):
        probe = _FakeProbe([_result(UNSUPPORTED_VISION)])
        pf = CompatibilityPreflight(probe)
        result = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="test-model",
            image_url_format="data_url",
        )
        assert result.outcome == "failed"
        assert result.code == UNSUPPORTED_VISION

    def test_auth_failure_fails(self):
        probe = _FakeProbe([_result(AUTHENTICATION_FAILURE)])
        pf = CompatibilityPreflight(probe)
        result = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="test-model",
            image_url_format="data_url",
        )
        assert result.outcome == "failed"
        assert result.code == AUTHENTICATION_FAILURE

    def test_unknown_probe_code_is_redacted_as_indeterminate(self):
        sentinel_code = "provider-secret-sk-sentinel"
        sentinel_explanation = "provider response exposed secret-token-sentinel"
        probe = _FakeProbe(
            [_result(sentinel_code, explanation=sentinel_explanation)]
        )
        pf = CompatibilityPreflight(probe)

        result = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="test-model",
            image_url_format="data_url",
        )

        assert result.outcome == "failed"
        assert result.code == INDETERMINATE
        assert result.explanation == explanation_for(INDETERMINATE)
        assert "sentinel" not in str(result.to_provenance())

    def test_skip_returns_skipped_without_probe_call(self):
        probe = _FakeProbe([])
        pf = CompatibilityPreflight(probe)
        result = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="test-model",
            image_url_format="data_url",
            skip=True,
        )
        assert result.outcome == "skipped"
        assert probe.calls == 0

    def test_non_screenshot_agent_passes_without_probe(self):
        probe = _FakeProbe([])
        pf = CompatibilityPreflight(probe)
        result = pf.check(
            agent="autoglm",
            base_url="http://provider.invalid/v1",
            model="test-model",
            image_url_format="data_url",
        )
        assert result.outcome == "passed"
        assert result.code is None
        assert probe.calls == 0


class TestPreflightCache:
    def test_cache_hit_reuses_result(self):
        probe = _FakeProbe([_result(COMPATIBLE)])
        pf = CompatibilityPreflight(probe, cache_ttl_seconds=300)
        # First call — live check.
        r1 = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="m",
            image_url_format="data_url",
        )
        assert r1.cached is False
        # Second call — cache hit.
        r2 = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="m",
            image_url_format="data_url",
        )
        assert r2.cached is True
        assert r2.outcome == "passed"
        assert probe.calls == 1  # only one live check

    def test_cache_expired_rechecks(self):
        # Clock calls: store1(100) → lookup2(500, expired) → store2(500)
        clock_values = [100.0, 500.0, 500.0]
        probe = _FakeProbe([_result(COMPATIBLE), _result(COMPATIBLE)])
        pf = CompatibilityPreflight(
            probe, cache_ttl_seconds=100, clock=lambda: clock_values.pop(0)
        )
        r1 = pf.check(
            agent="generic_v2", base_url="u", model="m", image_url_format="data_url"
        )
        assert r1.cached is False
        r2 = pf.check(
            agent="generic_v2", base_url="u", model="m", image_url_format="data_url"
        )
        assert r2.cached is False  # expired
        assert probe.calls == 2

    def test_failed_result_not_cached(self):
        probe = _FakeProbe([_result(UNSUPPORTED_VISION), _result(COMPATIBLE)])
        pf = CompatibilityPreflight(probe, cache_ttl_seconds=300)
        r1 = pf.check(
            agent="generic_v2", base_url="u", model="m", image_url_format="data_url"
        )
        assert r1.outcome == "failed"
        r2 = pf.check(
            agent="generic_v2", base_url="u", model="m", image_url_format="data_url"
        )
        assert r2.cached is False  # failure was not cached
        assert probe.calls == 2

    def test_different_model_not_cached(self):
        probe = _FakeProbe([_result(COMPATIBLE), _result(COMPATIBLE)])
        pf = CompatibilityPreflight(probe, cache_ttl_seconds=300)
        pf.check(agent="generic_v2", base_url="u", model="m1", image_url_format="data_url")
        pf.check(agent="generic_v2", base_url="u", model="m2", image_url_format="data_url")
        assert probe.calls == 2  # different model → different cache key

    def test_delimiter_collision_does_not_cause_false_cache_hit(self):
        """Regression: pipe-delimited keys collide for inputs that contain '|'.
        Tuple keys must distinguish them."""
        probe = _FakeProbe([_result(COMPATIBLE), _result(UNSUPPORTED_VISION)])
        pf = CompatibilityPreflight(probe, cache_ttl_seconds=300)
        # These two would produce the same "|" -delimited string:
        # "generic_v2|http://h/a|b|c|data_url"
        r1 = pf.check(
            agent="generic_v2",
            base_url="http://h/a|b",
            model="c",
            image_url_format="data_url",
        )
        r2 = pf.check(
            agent="generic_v2",
            base_url="http://h/a",
            model="b|c",
            image_url_format="data_url",
        )
        assert r1.outcome == "passed"
        assert r2.outcome == "failed"  # different config → different probe call
        assert probe.calls == 2

    def test_api_key_disables_cache_read_and_write(self):
        """A no-key compatible result must NOT be reused by a later request
        that carries an API key — compatibility includes auth success."""
        probe = _FakeProbe([_result(COMPATIBLE), _result(COMPATIBLE)])
        pf = CompatibilityPreflight(probe, cache_ttl_seconds=300)
        # First check: no API key → caches the result.
        pf.check(
            agent="generic_v2", base_url="u", model="m",
            image_url_format="data_url", api_key="",
        )
        assert probe.calls == 1
        # Second check: same config BUT with an API key → must NOT hit cache.
        r2 = pf.check(
            agent="generic_v2", base_url="u", model="m",
            image_url_format="data_url", api_key="sk-key",
        )
        assert probe.calls == 2  # cache was NOT read
        assert r2.cached is False


class TestProvenance:
    def test_to_provenance_has_no_secrets(self):
        import json

        probe = _FakeProbe([_result(COMPATIBLE)])
        pf = CompatibilityPreflight(probe)
        result = pf.check(
            agent="generic_v2",
            base_url="http://provider.invalid/v1",
            model="test-model",
            image_url_format="data_url",
            api_key="sk-sentinel-secret",
        )
        prov = result.to_provenance()
        assert "sk-sentinel-secret" not in json.dumps(prov)
        assert prov["outcome"] == "passed"
        assert prov["code"] == COMPATIBLE
        assert "cached" in prov
