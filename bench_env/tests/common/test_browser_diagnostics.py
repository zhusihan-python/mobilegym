from __future__ import annotations

from bench_env.env.mobile_gym import MobileGymEnv


class _Sink:
    def __init__(self) -> None:
        self.events = []

    def emit(self, event) -> None:
        self.events.append(event)


class _FakePage:
    def __init__(self) -> None:
        self.handlers = {}

    def on(self, event_type, handler) -> None:
        self.handlers[event_type] = handler


def test_browser_pageerror_emits_structured_diagnostic_event():
    sink = _Sink()
    env = MobileGymEnv(
        url="http://localhost:5173",
        headless=True,
        verbose=False,
        worker_id=2,
    )
    env.set_diagnostic_event_sink(sink)
    env.set_current_task("fake.Task")
    env._page = _FakePage()

    env._attach_page_listeners()
    env._page.handlers["pageerror"](RuntimeError("boom"))

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.type == "diagnostic.browser"
    assert event.phase == "browser.page"
    assert event.worker_id == "W2"
    assert event.task_id == "fake.Task"
    assert event.payload["code"] == "BROWSER_PAGE_ERROR"
    assert event.payload["message"] == "boom"
    assert event.payload["page_sequence"] == 1
