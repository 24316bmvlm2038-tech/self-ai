"""DeepSeek client. DeepSeek exposes an OpenAI-compatible API, so we use the openai SDK.

`stream()` yields normalized events so the agent loop doesn't depend on SDK types
(and tests can substitute a scripted fake):

    {"type": "content", "text": str}
    {"type": "reasoning", "text": str}          # deepseek-reasoner "thinking" output
    {"type": "tool_call", "index": int, "id": str | None, "name": str | None, "arguments": str}
    {"type": "finish", "reason": str | None}
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Protocol

from openai import AsyncOpenAI

from .config import Settings


class LLM(Protocol):
    def stream(
        self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None
    ) -> AsyncIterator[dict]: ...


class DeepSeekLLM:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(
            # The SDK refuses an empty key; local OpenAI-compatible servers don't need one.
            api_key=settings.api_key or "not-set",
            base_url=settings.base_url,
            timeout=600,
            max_retries=3,
        )

    async def stream(
        self, messages: list[dict], tools: list[dict] | None = None, model: str | None = None
    ) -> AsyncIterator[dict]:
        kwargs: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": messages,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
        if self.settings.temperature is not None:
            kwargs["temperature"] = self.settings.temperature

        stream = await self.client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta is not None:
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                if delta.content:
                    yield {"type": "content", "text": delta.content}
                for tc in delta.tool_calls or []:
                    fn = tc.function
                    yield {
                        "type": "tool_call",
                        "index": tc.index,
                        "id": tc.id,
                        "name": fn.name if fn else None,
                        "arguments": (fn.arguments if fn and fn.arguments else ""),
                    }
            if choice.finish_reason:
                yield {"type": "finish", "reason": choice.finish_reason}
