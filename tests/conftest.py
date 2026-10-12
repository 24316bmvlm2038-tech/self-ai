from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from selfai.config import Settings
from selfai.ollama import OllamaClient


class ToolCall:
    def __init__(self, name: str, **args: Any):
        self.name = name
        self.args = args


class FakeLLM:
    """Scripted stand-in for the model backend.

    Each response is a str (final answer), a list of ToolCall (one step of tool use), or a
    callable(messages, tools) returning one of those. With `responder`, every call is
    answered by that function instead of the script.
    """

    def __init__(self, script: list | None = None, responder: Callable | None = None, reasoning: str | None = None):
        self.script = list(script or [])
        self.responder = responder
        self.reasoning = reasoning
        self.calls: list[dict] = []

    async def stream(self, messages, tools=None, model=None, think=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "model": model, "think": think})
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


class FakeOllama:
    """In-memory stand-in for an Ollama server, served through httpx.MockTransport."""

    def __init__(self, models=("qwen3.5:4b",), capabilities=("completion", "tools", "thinking")):
        self.models = {name: list(capabilities) for name in models}
        self.chat_script: list[list[dict]] = []
        self.chat_requests: list[dict] = []
        self.pull_gate: asyncio.Event | None = None
        self.up = True

    def client(self, base_url: str = "http://ollama.test") -> OllamaClient:
        return OllamaClient(base_url, transport=httpx.MockTransport(self.handle))

    @staticmethod
    def ndjson(chunks: list[dict]) -> httpx.Response:
        return httpx.Response(200, content="".join(json.dumps(c) + "\n" for c in chunks).encode())

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.30.0"})
        if path == "/api/tags":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": n, "model": n, "size": 3_400_000_000, "details": {"parameter_size": "4B"}}
                        for n in self.models
                    ]
                },
            )
        name = body.get("model")
        if path == "/api/show":
            if name not in self.models:
                return httpx.Response(404, json={"error": f"model '{name}' not found"})
            return httpx.Response(200, json={"capabilities": self.models[name]})
        if path == "/api/chat":
            self.chat_requests.append(body)
            if name not in self.models:
                return httpx.Response(404, json={"error": f"model '{name}' not found"})
            return self.ndjson(self.chat_script.pop(0))
        if path == "/api/pull":
            if self.pull_gate is not None:
                await self.pull_gate.wait()
            if name.startswith("bad"):
                return self.ndjson(
                    [{"status": "pulling manifest"}, {"error": "pull model manifest: file does not exist"}]
                )
            self.models[name] = ["completion", "tools"]
            return self.ndjson(
                [
                    {"status": "pulling manifest"},
                    {"status": "pulling abc", "digest": "abc", "total": 100, "completed": 40},
                    {"status": "pulling abc", "digest": "abc", "total": 100, "completed": 100},
                    {"status": "success"},
                ]
            )
        if path == "/api/delete":
            if self.models.pop(name, None) is None:
                return httpx.Response(404, json={"error": "model not found"})
            return httpx.Response(200)
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        provider="ollama",
        model="qwen3.5:4b",
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
