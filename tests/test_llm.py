import json

import httpx
import pytest

from selfai.errors import LLMError
from selfai.llm import OllamaLLM, OpenAICompatLLM, ThinkSplitter, to_ollama_messages

from .conftest import FakeOllama, run


async def collect(llm, *args, **kwargs):
    return [ev async for ev in llm.stream(*args, **kwargs)]


# --------------------------------------------------------------------------- DeepSeek / OpenAI-compatible


def sse(*chunks):
    lines = []
    for delta, finish in chunks:
        payload = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        lines.append(f"data: {json.dumps(payload)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines)


def deepseek(settings, handler):
    settings.provider = "deepseek"
    settings.api_key = "test-key"
    settings.model = "deepseek-chat"
    return OpenAICompatLLM(settings, transport=httpx.MockTransport(handler))


def test_deepseek_stream_is_normalized(settings):
    captured = {}

    def handler(request: httpx.Request):
        captured.update(url=str(request.url), body=json.loads(request.content), auth=request.headers["authorization"])
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
    llm = deepseek(settings, handler)
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
    events = run(collect(llm, [{"role": "user", "content": "hi"}], tools, "deepseek-reasoner"))
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
    assert captured["body"] == {
        "model": "deepseek-reasoner",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "tools": tools,
        "temperature": 0.3,
    }


def test_deepseek_retries_server_errors(settings, monkeypatch):
    monkeypatch.setattr("selfai.llm.asyncio.sleep", lambda s: _noop())
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, text="overloaded")
        return httpx.Response(200, text=sse(({"content": "ok"}, "stop")))

    events = run(collect(deepseek(settings, handler), [{"role": "user", "content": "hi"}]))
    assert len(attempts) == 3
    assert events[0] == {"type": "content", "text": "ok"}


async def _noop():
    return None


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {"error": {"message": "Authentication Fails"}}, "rejected the API key"),
        (402, {"error": {"message": "Insufficient Balance"}}, "insufficient balance"),
        (400, {"error": {"message": "bad thing"}}, "AI API error 400: bad thing"),
    ],
)
def test_deepseek_errors_are_explained(settings, status, body, expected):
    llm = deepseek(settings, lambda request: httpx.Response(status, json=body))
    with pytest.raises(LLMError, match=expected):
        run(collect(llm, [{"role": "user", "content": "hi"}]))


def test_deepseek_unreachable(settings, monkeypatch):
    monkeypatch.setattr("selfai.llm.asyncio.sleep", lambda s: _noop())

    def handler(request):
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(LLMError, match="Could not connect"):
        run(collect(deepseek(settings, handler), [{"role": "user", "content": "hi"}]))


# --------------------------------------------------------------------------- Ollama (local models)


def test_ollama_request_and_events(settings):
    fake = FakeOllama()
    fake.chat_script.append(
        [
            {"message": {"role": "assistant", "content": "", "thinking": "Need to look"}, "done": False},
            {"message": {"role": "assistant", "content": "Searching."}, "done": False},
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "web_search", "arguments": {"query": "news"}}},
                        {"id": "x9", "function": {"name": "list_files", "arguments": {}}},
                    ],
                },
                "done": False,
            },
            {"message": {"role": "assistant", "content": ""}, "done": True, "done_reason": "stop"},
        ]
    )
    settings.context_tokens = 4096
    llm = OllamaLLM(settings, fake.client())
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
    events = run(collect(llm, [{"role": "user", "content": "news?"}], tools, None, True))
    assert events == [
        {"type": "reasoning", "text": "Need to look"},
        {"type": "content", "text": "Searching."},
        {"type": "tool_call", "index": 0, "id": None, "name": "web_search", "arguments": '{"query": "news"}'},
        {"type": "tool_call", "index": 1, "id": "x9", "name": "list_files", "arguments": "{}"},
        {"type": "finish", "reason": "stop"},
    ]
    [body] = fake.chat_requests
    assert body["model"] == "qwen3.5:4b"
    assert body["options"]["num_ctx"] == 4096
    assert body["think"] is True and body["tools"] == tools
    assert body["keep_alive"] == "30m" and body["stream"] is True


def test_ollama_model_without_tools_or_thinking(settings):
    fake = FakeOllama(models=("tiny:1b",), capabilities=("completion",))
    fake.chat_script.append([{"message": {"content": "<think>hmm</think>Hello"}, "done": True}])
    llm = OllamaLLM(settings, fake.client())
    tools = [{"type": "function", "function": {"name": "x", "parameters": {"type": "object"}}}]
    events = run(collect(llm, [{"role": "user", "content": "hi"}], tools, "tiny:1b", True))
    assert events[0]["type"] == "notice" and "can't use tools" in events[0]["text"]
    assert {"type": "reasoning", "text": "hmm"} in events
    assert {"type": "content", "text": "Hello"} in events
    body = fake.chat_requests[0]
    assert "tools" not in body and "think" not in body


def test_ollama_errors(settings):
    fake = FakeOllama()
    llm = OllamaLLM(settings, fake.client())
    with pytest.raises(LLMError, match="isn't downloaded yet"):
        run(collect(llm, [{"role": "user", "content": "hi"}], None, "missing:7b"))
    fake.up = False
    with pytest.raises(LLMError, match="Can't reach Ollama"):
        run(collect(llm, [{"role": "user", "content": "hi"}], None, "qwen3.5:4b"))


def test_to_ollama_messages():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "plan",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "data"},
    ]
    assert to_ollama_messages(messages) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "thinking": "plan",
            "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a"}}}],
        },
        {"role": "tool", "content": "data", "tool_name": "read_file"},
    ]


@pytest.mark.parametrize(
    "chunks,expected",
    [
        (["Hello", " world"], [("content", "Hello"), ("content", " world")]),
        (["<think>a", "b</think>", "Answer"], [("reasoning", "a"), ("reasoning", "b"), ("content", "Answer")]),
        (["<thi", "nk>idea</thi", "nk>\n\nDone"], [("reasoning", "idea"), ("content", "Done")]),
        (["  <think>x</think>y"], [("reasoning", "x"), ("content", "y")]),
        (["<thinking is fun"], [("content", "<thinking is fun")]),
        (["<think>never closed"], [("reasoning", "never closed")]),
    ],
)
def test_think_splitter(chunks, expected):
    splitter = ThinkSplitter()
    out = []
    for c in chunks:
        out += splitter.feed(c)
    out += splitter.flush()
    # Chunk boundaries may differ; what matters is which text lands in which channel.
    assert "".join(t for k, t in out if k == "reasoning") == "".join(t for k, t in expected if k == "reasoning")
    assert "".join(t for k, t in out if k == "content") == "".join(t for k, t in expected if k == "content")
