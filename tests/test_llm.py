import json

import httpx
from openai import AsyncOpenAI

from selfai.llm import DeepSeekLLM

from .conftest import run


def sse(*chunks):
    lines = []
    for delta, finish in chunks:
        payload = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "deepseek-reasoner",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        lines.append(f"data: {json.dumps(payload)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


def test_deepseek_stream_is_normalized(settings):
    captured = {}

    def handler(request: httpx.Request):
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers["authorization"]
        body = sse(
            ({"role": "assistant", "content": None, "reasoning_content": "Thinking..."}, None),
            ({"content": "Let me search."}, None),
            (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": ""},
                        }
                    ]
                },
                None,
            ),
            ({"tool_calls": [{"index": 0, "function": {"arguments": '{"query": '}}]}, None),
            ({"tool_calls": [{"index": 0, "function": {"arguments": '"news"}'}}]}, None),
            ({}, "tool_calls"),
        )
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    settings.temperature = 0.3
    llm = DeepSeekLLM(settings)
    llm.client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    tools = [
        {"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {}}}}
    ]

    async def collect():
        return [ev async for ev in llm.stream([{"role": "user", "content": "hi"}], tools, "deepseek-reasoner")]

    events = run(collect())
    assert events == [
        {"type": "reasoning", "text": "Thinking..."},
        {"type": "content", "text": "Let me search."},
        {"type": "tool_call", "index": 0, "id": "call_1", "name": "web_search", "arguments": ""},
        {"type": "tool_call", "index": 0, "id": None, "name": None, "arguments": '{"query": '},
        {"type": "tool_call", "index": 0, "id": None, "name": None, "arguments": '"news"}'},
        {"type": "finish", "reason": "tool_calls"},
    ]
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["auth"] == "Bearer test-key"
    body = captured["body"]
    assert body["model"] == "deepseek-reasoner" and body["stream"] is True
    assert body["tools"] == tools and body["temperature"] == 0.3
