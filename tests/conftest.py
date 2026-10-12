from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from selfai.config import Settings


class ToolCall:
    def __init__(self, name: str, **args: Any):
        self.name = name
        self.args = args


class FakeLLM:
    """Scripted stand-in for DeepSeek.

    Each response is a str (final answer), a list of ToolCall (one step of tool use), or a
    callable(messages, tools) returning one of those. With `responder`, every call is
    answered by that function instead of the script.
    """

    def __init__(self, script: list | None = None, responder: Callable | None = None, reasoning: str | None = None):
        self.script = list(script or [])
        self.responder = responder
        self.reasoning = reasoning
        self.calls: list[dict] = []

    async def stream(self, messages, tools=None, model=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "model": model})
        if self.responder is not None:
            response = self.responder(messages, tools)
        else:
            if not self.script:
                raise AssertionError("FakeLLM script exhausted")
            response = self.script.pop(0)
            if callable(response):
                response = response(messages, tools)
        if asyncio.iscoroutine(response):
            response = await response
        if self.reasoning:
            yield {"type": "reasoning", "text": self.reasoning}
        if isinstance(response, str):
            for i in range(0, len(response), 7):
                yield {"type": "content", "text": response[i : i + 7]}
                await asyncio.sleep(0)
            yield {"type": "finish", "reason": "stop"}
            return
        for index, call in enumerate(response):
            raw = json.dumps(call.args)
            half = len(raw) // 2
            # Split like real streaming: id+name first, arguments in fragments.
            yield {
                "type": "tool_call",
                "index": index,
                "id": f"call_{len(self.calls)}_{index}",
                "name": call.name,
                "arguments": raw[:half],
            }
            yield {"type": "tool_call", "index": index, "id": None, "name": None, "arguments": raw[half:]}
        yield {"type": "finish", "reason": "tool_calls"}


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        api_key="test-key",
        data_dir=tmp_path / "data",
        allowed_hosts=["testserver", "localhost", "127.0.0.1"],
        scheduler_poll_seconds=0.05,
        allow_private_network=True,
        tool_timeout_seconds=10,
    )
    s.ensure_dirs()
    return s


def run(coro):
    return asyncio.run(coro)
