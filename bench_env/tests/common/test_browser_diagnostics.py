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


def test_browser_diagnostic_producers_preserve_episode_step_and_app_context():
    class Request:
        failure = "connection refused"
        url = "http://localhost:5173/api/data"

    class Response:
        status = 503
        url = "http://localhost:5173/api/data"

    class ConsoleMessage:
        type = "error"
        text = "render failed"

    sink = _Sink()
    env = MobileGymEnv(
        url="http://localhost:5173",
        headless=True,
        verbose=False,
        worker_id=3,
    )
    env.set_diagnostic_event_sink(sink)
    env.set_current_task("fake.Task")
    env.set_diagnostic_context(
        episode_key="fake.Task::0",
        step=4,
        app_ids=["fake"],
    )
    env._page = _FakePage()
    env._attach_page_listeners()

    env._page.handlers["pageerror"](RuntimeError("boom"))
    env._page.handlers["requestfailed"](Request())
    env._page.handlers["response"](Response())
    env._page.handlers["console"](ConsoleMessage())

    assert [event.payload["code"] for event in sink.events] == [
        "BROWSER_PAGE_ERROR",
        "BROWSER_REQUEST_FAILED",
        "BROWSER_HTTP_ERROR",
        "BROWSER_CONSOLE_ERROR",
    ]
    assert [event.phase for event in sink.events] == [
        "browser.page",
        "browser.network",
        "browser.network",
        "browser.console",
    ]
    for event in sink.events:
        assert event.worker_id == "W3"
        assert event.task_id == "fake.Task"
        assert event.episode_key == "fake.Task::0"
        assert event.payload["step"] == 4
        assert event.payload["app_ids"] == ["fake"]
