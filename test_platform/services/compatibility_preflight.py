"""Compatibility preflight check for run/follow-up creation.

Checks whether a model endpoint can accept the screenshot image format used by
a vision-capable agent, BEFORE the run creation transaction commits. Failed or
indeterminate checks block creation; successful checks are cached for reuse.

Only agents in ``SCREENSHOT_REQUIRED_AGENTS`` are gated; all others pass through
without any probe call.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from test_platform.domain.model_compatibility import (
    ALL_CODES,
    COMPATIBLE,
    INDETERMINATE,
    CompatibilityResult,
    SCREENSHOT_REQUIRED_AGENTS,
    explanation_for,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreflightResult:
    """Outcome of a compatibility preflight check.

    ``outcome`` is one of:
    - ``"passed"`` — the endpoint is compatible (or the agent is not gated).
    - ``"failed"`` — the endpoint is incompatible or indeterminate.
    - ``"skipped"`` — the operator explicitly skipped the check.
    """

    outcome: str
    code: str | None
    explanation: str
    latency_ms: int | None
    checked_model: str | None
    checked_image_format: str | None
    cached: bool

    def to_provenance(self) -> dict[str, Any]:
        """Return a redacted summary for Run Attempt provenance."""
        return {
            "outcome": self.outcome,
            "code": self.code,
            "explanation": self.explanation,
            "latency_ms": self.latency_ms,
            "cached": self.cached,
            "checked_model": self.checked_model,
            "checked_image_format": self.checked_image_format,
        }


def _cache_key(
    *,
    agent: str,
    base_url: str,
    model: str,
    image_url_format: str,
) -> tuple[str, str, str, str]:
    """Unambiguous cache key using a tuple (no delimiter collision)."""
    return (agent, base_url, model, image_url_format)


class CompatibilityPreflight:
    """Preflight gate with a process-local TTL cache.

    The cache key excludes ``api_key`` — compatibility is an endpoint+model
    property, not a credential property. The cache lives for the process
    lifetime (same as ``_RuntimeRunSecretStore``); a restart clears it.
    """

    def __init__(
        self,
        probe: Any,
        *,
        cache_ttl_seconds: float = 300.0,
        clock: Any = time.monotonic,
    ) -> None:
        self._probe = probe
        self._cache_ttl = cache_ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[str, str, str, str], tuple[CompatibilityResult, float]] = {}
        self._lock = threading.Lock()

    def check(
        self,
        *,
        agent: str,
        base_url: str,
        model: str,
        image_url_format: str,
        api_key: str = "",
        skip: bool = False,
        timeout_seconds: float = 15.0,
    ) -> PreflightResult:
        # Non-screenshot agents are not gated.
        if agent not in SCREENSHOT_REQUIRED_AGENTS:
            return PreflightResult(
                outcome="passed",
                code=None,
                explanation="Agent does not require screenshot compatibility.",
                latency_ms=None,
                checked_model=None,
                checked_image_format=None,
                cached=False,
            )

        # Explicit skip (troubleshooting only — console never defaults to skip).
        if skip:
            logger.info(
                "compatibility_preflight skipped agent=%s model=%s", agent, model
            )
            return PreflightResult(
                outcome="skipped",
                code=None,
                explanation="Compatibility check was explicitly skipped.",
                latency_ms=None,
                checked_model=model,
                checked_image_format=image_url_format,
                cached=False,
            )

        # Cache is entirely disabled when an API key is present: compatibility
        # includes authentication success, which is not an endpoint-only property.
        # Both read AND write are gated by `not api_key`.
        key = _cache_key(
            agent=agent,
            base_url=base_url,
            model=model,
            image_url_format=image_url_format,
        )
        if not api_key:
            with self._lock:
                cached_entry = self._cache.get(key)
                if cached_entry is not None:
                    result, expires_at = cached_entry
                    if self._clock() < expires_at:
                        return self._result_from_probe(
                            result,
                            cached=True,
                            checked_model=model,
                            checked_image_format=image_url_format,
                        )

        # Live check.
        result = self._probe.check(
            base_url=base_url,
            api_key=api_key,
            model=model,
            image_url_format=image_url_format,
            timeout_seconds=timeout_seconds,
        )

        # Cache only successful checks (when no api_key is involved — see P2).
        if result.code == COMPATIBLE and not api_key:
            with self._lock:
                self._cache[key] = (result, self._clock() + self._cache_ttl)

        return self._result_from_probe(
            result,
            cached=False,
            checked_model=model,
            checked_image_format=image_url_format,
        )

    def _result_from_probe(
        self,
        result: CompatibilityResult,
        *,
        cached: bool,
        checked_model: str,
        checked_image_format: str,
    ) -> PreflightResult:
        code = result.code if result.code in ALL_CODES else INDETERMINATE
        if code == COMPATIBLE:
            return PreflightResult(
                outcome="passed",
                code=code,
                explanation=explanation_for(code),
                latency_ms=result.latency_ms,
                checked_model=checked_model,
                checked_image_format=checked_image_format,
                cached=cached,
            )
        return PreflightResult(
            outcome="failed",
            code=code,
            explanation=explanation_for(code),
            latency_ms=result.latency_ms,
            checked_model=checked_model,
            checked_image_format=checked_image_format,
            cached=cached,
        )
