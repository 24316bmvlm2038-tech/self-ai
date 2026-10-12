from selfai.agent import (
    INTERRUPTED_RESULT,
    STEP_LIMIT_NOTICE,
    Agent,
    history_to_api,
    trim_history,
)
from selfai.tools import ToolContext, build_registry

from .conftest import FakeLLM, ToolCall, run


async def collect(agent, messages, ctx, max_steps=10):
    return [ev async for ev in agent.run(messages, ctx, max_steps)]


def test_agent_uses_tools_then_answers(settings):
    llm = FakeLLM(
        [
            [ToolCall("write_file", path="notes/plan.md", content="step 1"), ToolCall("list_files", path="notes")],
            "I saved your plan to `notes/plan.md`.",
        ]
    )
    agent = Agent(llm, build_registry(), settings)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "make a plan"}]
    events = run(collect(agent, messages, ToolContext(settings)))

    assert (settings.workspace_dir / "notes/plan.md").read_text() == "step 1"
    kinds = [e["type"] for e in events]
    assert kinds[:7] == ["message", "tool_call", "tool_result", "message", "tool_call", "tool_result", "message"]
    assert events[-1] == {"type": "done", "content": "I saved your plan to `notes/plan.md`."}
    assert "".join(e["text"] for e in events if e["type"] == "token") == events[-1]["content"]

    # Arguments streamed in fragments are reassembled.
    call = next(e for e in events if e["type"] == "tool_call")
    assert call["name"] == "write_file" and '"notes/plan.md"' in call["arguments"]
    assert next(e for e in events if e["type"] == "tool_result")["ok"] is True

    # The second model call saw the tool results.
    second = llm.calls[1]["messages"]
    assert [m["role"] for m in second[-3:]] == ["assistant", "tool", "tool"]
    assert second[-3]["tool_calls"][0]["function"]["name"] == "write_file"
    assert "plan.md" in second[-1]["content"]
    assert llm.calls[0]["tools"] and any(t["function"]["name"] == "web_search" for t in llm.calls[0]["tools"])


def test_tool_errors_are_fed_back_not_raised(settings):
    llm = FakeLLM([[ToolCall("read_file", path="missing.txt")], "That file does not exist."])
    events = run(
        collect(Agent(llm, build_registry(), settings), [{"role": "user", "content": "x"}], ToolContext(settings))
    )
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False and "not a file" in result["content"]
    assert events[-1]["type"] == "done"


def test_step_limit_forces_final_answer(settings):
    def responder(messages, tools):
        if tools is None:
            return "Summary: ran out of steps."
        return [ToolCall("list_files")]

    llm = FakeLLM(responder=responder)
    messages = [{"role": "user", "content": "loop forever"}]
    events = run(collect(Agent(llm, build_registry(), settings), messages, ToolContext(settings), max_steps=3))
    assert len(llm.calls) == 4
    assert llm.calls[-1]["tools"] is None
    assert llm.calls[-1]["messages"][-1] == {"role": "user", "content": STEP_LIMIT_NOTICE}
    assert any(e["type"] == "notice" for e in events)
    assert events[-1] == {"type": "done", "content": "Summary: ran out of steps."}


def test_reasoning_is_streamed_and_passed_back_within_turn(settings):
    llm = FakeLLM([[ToolCall("list_files")], "done"], reasoning="Let me think.")
    events = run(
        collect(Agent(llm, build_registry(), settings), [{"role": "user", "content": "x"}], ToolContext(settings))
    )
    assert any(e["type"] == "reasoning" and e["text"] == "Let me think." for e in events)
    first_message = next(e for e in events if e["type"] == "message")
    assert first_message["reasoning"] == "Let me think."
    assert "reasoning_content" not in first_message["message"]  # stored separately
    assert llm.calls[1]["messages"][-2]["reasoning_content"] == "Let me think."


def test_history_to_api_repairs_interrupted_tool_calls():
    rows = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "",
            "reasoning": "secret thoughts",
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "list_files", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "list_files", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "files"},
        {"role": "tool", "tool_call_id": "zzz", "content": "orphan"},
        {"role": "user", "content": "again"},
        {"role": "assistant", "content": None, "tool_calls": None},  # empty (stopped before any output)
        {"role": "assistant", "content": "ok"},
    ]
    out = history_to_api(rows)
    assert out == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": rows[1]["tool_calls"]},
        {"role": "tool", "tool_call_id": "a", "content": "files"},
        {"role": "tool", "tool_call_id": "b", "content": INTERRUPTED_RESULT},
        {"role": "user", "content": "again"},
        {"role": "assistant", "content": "ok"},
    ]


def test_trim_history_keeps_recent_turns_starting_with_user():
    messages = []
    for i in range(10):
        messages += [
            {"role": "user", "content": f"q{i} " + "x" * 100},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": f"c{i}", "content": "r" * 100},
            {"role": "assistant", "content": f"a{i}"},
        ]
    assert trim_history(messages, 10**9) == messages
    trimmed = trim_history(messages, 1500)
    assert trimmed[0]["role"] == "user"
    assert trimmed[-1] == messages[-1]
    assert len(trimmed) < len(messages)
    # A single huge turn still keeps the latest user message.
    assert trim_history(messages, 10)[0]["content"].startswith("q9")
