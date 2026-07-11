"""Shared screenshot-message builder for OpenAI-compatible chat completions.

Used by both ``GenericAgentV2.build_messages()`` and the Test Platform
compatibility probe so that real inference and the compatibility check
construct the ``image_url`` content part from the exact same implementation.
"""

from __future__ import annotations

from typing import Any


def build_screenshot_user_message(
    image_data_url: str,
    text: str,
) -> dict[str, Any]:
    """Build a user message carrying a screenshot image + text.

    The content-part layout (``image_url`` first, then ``text``) matches the
    OpenAI chat-completions multimodal schema used by ``generic_v2``.
    """
    return {
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": image_data_url}},
            {"type": "text", "text": text},
        ],
    }
