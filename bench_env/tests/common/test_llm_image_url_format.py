from __future__ import annotations

from bench_env.llm.openai_chat import _format_image_url_messages


def test_bare_base64_image_url_format_strips_data_url_without_mutating_original() -> None:
    data_url = "data:image/jpeg;base64,/9j/4AAQ"
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "describe"},
            ],
        }
    ]

    formatted = _format_image_url_messages(messages, "bare_base64")

    assert formatted[0]["content"][0]["image_url"]["url"] == "/9j/4AAQ"
    assert messages[0]["content"][0]["image_url"]["url"] == data_url


def test_data_url_image_url_format_keeps_original_message_object() -> None:
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": "https://example.com/image.png"}},
            ],
        }
    ]

    assert _format_image_url_messages(messages, "data_url") is messages
