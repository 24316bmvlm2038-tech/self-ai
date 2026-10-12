"""Model backends. Each `stream()` yields normalized events so the agent loop doesn't depend on
any provider (and tests can substitute a scripted fake):

    {"type": "content", "text": str}
    {"type": "reasoning", "text": str}       # the model's "thinking", shown collapsed in the UI
    {"type": "tool_call", "index": int, "id": str | None, "name": str | None, "arguments": str}
    {"type": "notice", "text": str}           # something the user should know (e.g. no tool support)
    {"type": "finish", "reason": str | None}

Only `httpx` is used (no compiled dependencies), so this installs anywhere, including Android/Termux.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol

import httpx

from .config import Settings
from .db import Database
from .errors import LLMError, api_error_message
from .ollama import OllamaClient
from .providers import LOCAL, active_model, active_provider, api_key, cloud_provider


class LLM(Protocol):
    def stream(
        self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None, think: bool | None = None
    ) -> AsyncIterator[dict]: ...


class ThinkSplitter:
    """Separates a leading <think>...</think> block that some models write into their normal output."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buffer = ""
        self.state = "start"  # start -> thinking -> answer

    def feed(self, text: str) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self.buffer += text
        while self.buffer:
            if self.state == "start":
                head = self.buffer.lstrip()
                if not head or (self.OPEN.startswith(head) and head != self.OPEN):
                    return out  # might still become "<think>"
                if head.startswith(self.OPEN):
                    self.buffer = head[len(self.OPEN) :]
                    self.state = "thinking"
                else:
                    self.state = "answer"
            elif self.state == "thinking":
                end = self.buffer.find(self.CLOSE)
                if end >= 0:
                    if end:
                        out.append(("reasoning", self.buffer[:end]))
                    self.buffer = self.buffer[end + len(self.CLOSE) :].lstrip()
                    self.state = "answer"
                    continue
                keep = next((n for n in range(len(self.CLOSE) - 1, 0, -1) if self.buffer.endswith(self.CLOSE[:n])), 0)
                if len(self.buffer) > keep:
                    out.append(("reasoning", self.buffer[: len(self.buffer) - keep]))
                    self.buffer = self.buffer[len(self.buffer) - keep :]
                return out
            else:
                out.append(("content", self.buffer))
                self.buffer = ""
        return out

    def flush(self) -> list[tuple[str, str]]:
        text, self.buffer = self.buffer, ""
        if not text:
            return []
        return [("reasoning" if self.state == "thinking" else "content", text)]


def _split_events(pairs: list[tuple[str, str]]) -> list[dict]:
    return [{"type": kind, "text": text} for kind, text in pairs if text]


class OpenAICompatLLM:
    """An OpenAI-compatible chat API: DeepSeek, Groq, Gemini, OpenRouter..."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        name: str = "DeepSeek",
    ):
        self.settings = settings
        self._transport = transport
        self.base_url = base_url or settings.base_url
        self.api_key = settings.api_key if api_key is None else api_key
        self.name = name

    @asynccontextmanager
    async def _open(self, body: dict):
        timeout = httpx.Timeout(600.0, connect=15.0)
        async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport) as client:
            resp = await self._send(client, body)
            try:
                yield resp
            finally:
                await resp.aclose()

    async def _send(self, client: httpx.AsyncClient, body: dict) -> httpx.Response:
        """Start the request, retrying connection failures, rate limits and server errors."""
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        for attempt in range(3):
            retry = attempt < 2
            try:
                request = client.build_request("POST", "/chat/completions", json=body, headers=headers)
                resp = await client.send(request, stream=True)
            except httpx.TransportError as exc:
                if not retry:
                    raise LLMError(f"Could not connect to {self.name}. Check your internet connection.") from exc
                await asyncio.sleep(2**attempt)
                continue
            if resp.status_code < 400:
                return resp
            detail = (await resp.aread()).decode("utf-8", "replace")
            await resp.aclose()
            if retry and (resp.status_code == 429 or resp.status_code >= 500):
                await asyncio.sleep(2**attempt)
                continue
            try:
                detail = json.loads(detail)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = detail[:500]
            raise LLMError(api_error_message(resp.status_code, detail, self.name), resp.status_code)
        raise AssertionError("unreachable")

    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None, think: bool | None = None
    ) -> AsyncIterator[dict]:
        body: dict = {"model": model or self.settings.model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = tools
        if self.settings.temperature is not None:
            body["temperature"] = self.settings.temperature
        splitter = ThinkSplitter()
        async with self._open(body) as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise LLMError(f"{self.name} error: {chunk['error'].get('message', chunk['error'])}")
                for choice in (chunk.get("choices") or [])[:1]:
                    delta = choice.get("delta") or {}
                    reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                    if reasoning:
                        yield {"type": "reasoning", "text": reasoning}
                    if delta.get("content"):
                        for ev in _split_events(splitter.feed(delta["content"])):
                            yield ev
                    for tc in delta.get("tool_calls") or []:
                        fn = tc.get("function") or {}
                        args = fn.get("arguments") or ""
                        yield {
                            "type": "tool_call",
                            "index": tc.get("index", 0),
                            "id": tc.get("id"),
                            "name": fn.get("name"),
                            "arguments": args if isinstance(args, str) else json.dumps(args),
                        }
                    if choice.get("finish_reason"):
                        yield {"type": "finish", "reason": choice["finish_reason"]}
        for ev in _split_events(splitter.flush()):
            yield ev


def to_ollama_messages(messages: list[dict]) -> list[dict]:
    """OpenAI-style messages -> Ollama's native format (arguments as objects, tool results by name)."""
    out = []
    names: dict[str, str] = {}
    for m in messages:
        msg: dict = {"role": m["role"], "content": m.get("content") or ""}
        if m["role"] == "assistant":
            if m.get("reasoning_content"):
                msg["thinking"] = m["reasoning_content"]
            calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc["function"]
                names[tc["id"]] = fn["name"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                calls.append({"function": {"name": fn["name"], "arguments": args if isinstance(args, dict) else {}}})
            if calls:
                msg["tool_calls"] = calls
        elif m["role"] == "tool":
            msg["tool_name"] = names.get(m.get("tool_call_id", ""), "")
        out.append(msg)
    return out


class OllamaLLM:
    """A free model running on this device through Ollama."""

    def __init__(self, settings: Settings, client: OllamaClient):
        self.settings = settings
        self.client = client

    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None, think: bool | None = None
    ) -> AsyncIterator[dict]:
        model = model or self.settings.model
        await self.client.ensure_running()
        caps = await self.client.capabilities(model)
        options: dict = {"num_ctx": self.settings.context_tokens}
        if self.settings.temperature is not None:
            options["temperature"] = self.settings.temperature
        body: dict = {
            "model": model,
            "messages": to_ollama_messages(messages),
            "stream": True,
            "keep_alive": self.settings.keep_alive,
            "options": options,
        }
        if tools:
            if caps is None or "tools" in caps:
                body["tools"] = tools
            else:
                yield {
                    "type": "notice",
                    "text": f"{model} can't use tools, so it can't browse the web or create files. "
                    "Pick a model with tool support in Models.",
                }
        if caps is not None and "thinking" in caps:
            body["think"] = bool(think)

        splitter = ThinkSplitter()
        index = 0
        async for chunk in self.client.chat(body):
            msg = chunk.get("message") or {}
            if msg.get("thinking"):
                yield {"type": "reasoning", "text": msg["thinking"]}
            if msg.get("content"):
                for ev in _split_events(splitter.feed(msg["content"])):
                    yield ev
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                args = fn.get("arguments")
                yield {
                    "type": "tool_call",
                    "index": index,
                    "id": call.get("id"),
                    "name": fn.get("name"),
                    "arguments": args if isinstance(args, str) else json.dumps(args or {}),
                }
                index += 1
            if chunk.get("done"):
                for ev in _split_events(splitter.flush()):
                    yield ev
                yield {"type": "finish", "reason": chunk.get("done_reason")}


class RoutingLLM:
    """Sends each request to the AI currently selected in the app (this device or a cloud API)."""

    def __init__(
        self,
        settings: Settings,
        db: Database,
        ollama: OllamaClient,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        self.db = db
        self.local = OllamaLLM(settings, ollama)
        self._transport = transport

    def stream(
        self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None, think: bool | None = None
    ) -> AsyncIterator[dict]:
        pid = active_provider(self.db, self.settings)
        model = model or active_model(self.db, self.settings)
        if pid == LOCAL:
            return self.local.stream(messages, tools, model, think)
        provider = cloud_provider(pid, self.settings)
        cloud = OpenAICompatLLM(
            self.settings,
            self._transport,
            base_url=provider.base_url,
            api_key=api_key(self.db, self.settings, pid),
            name=provider.name,
        )
        return cloud.stream(messages, tools, model, think)


def build_llm(settings: Settings, db: Database, ollama: OllamaClient) -> LLM:
    return RoutingLLM(settings, db, ollama)
