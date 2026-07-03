from __future__ import annotations

import pytest

from bench_env.runner.cancellation import CancellationToken, RunCancelled
from bench_env.runner.events import ExecutionEvent, NullEventSink


def test_null_event_sink_accepts_events_without_side_effects() -> None:
    event = ExecutionEvent(
        type="episode.started",
        timestamp="2026-07-03T00:00:00.000Z",
        phase="execute",
        worker_id="serial",
        task_id="wechat.OpenBlacklist",
        trial_id=0,
        payload={"episode_key": "wechat.OpenBlacklist|i0|s123|r1|t0"},
    )

    assert NullEventSink().emit(event) is None


def test_cancellation_token_raises_only_after_cancel() -> None:
    token = CancellationToken()

    token.raise_if_cancelled()
    assert token.cancelled is False

    token.cancel()

    assert token.cancelled is True
    with pytest.raises(RunCancelled):
        token.raise_if_cancelled()
