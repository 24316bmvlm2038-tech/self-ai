import base64
import json
import time

import pytest
from fastapi.testclient import TestClient

from selfai.server import create_app

from .conftest import FakeLLM, ToolCall

H = {"X-SelfAI": "1"}


@pytest.fixture
def make_client(settings):
    clients = []

    def factory(llm=None):
        client = TestClient(create_app(settings, llm or FakeLLM(["Hello!"])))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for c in clients:
        c.__exit__(None, None, None)


def stream_events(client, conv_id, content, **extra):
    events = []
    with client.stream(
        "POST", f"/api/conversations/{conv_id}/messages", json={"content": content, **extra}, headers=H
    ) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("data: ") and line != "data: {}":
                events.append(json.loads(line[6:]))
    return events


def wait_until(fn, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_index_and_status(make_client):
    client = make_client()
    page = client.get("/")
    assert page.status_code == 200 and "Self-AI" in page.text
    assert client.get("/static/app.js").status_code == 200
    status = client.get("/api/status").json()
    assert status["api_key_configured"] is True
    assert status["model"] == "deepseek-chat"
    assert status["search_provider"] == "duckduckgo"


def test_chat_round_trip_with_tools(make_client, settings):
    llm = FakeLLM([[ToolCall("write_file", path="hello.txt", content="hi there")], "Created **hello.txt**."])
    client = make_client(llm)
    conv = client.post("/api/conversations", json={}, headers=H).json()
    assert conv["title"] == "New chat"

    events = stream_events(client, conv["id"], "Please create hello.txt", model="deepseek-reasoner")
    types = [e["type"] for e in events]
    assert types[0] == "tool_call" and "tool_result" in types and types[-1] == "done"
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Created **hello.txt**."
    assert llm.calls[0]["model"] == "deepseek-reasoner"
    assert (settings.workspace_dir / "hello.txt").read_text() == "hi there"

    data = client.get(f"/api/conversations/{conv['id']}").json()
    assert data["title"] == "Please create hello.txt"
    assert data["running"] is False
    assert [m["role"] for m in data["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert data["messages"][1]["tool_calls"][0]["function"]["name"] == "write_file"

    # Follow-up turn sends the stored history back to the model.
    llm.script.append("Sure.")
    stream_events(client, conv["id"], "thanks")
    sent = llm.calls[-1]["messages"]
    assert sent[0]["role"] == "system"
    assert [m["role"] for m in sent[1:]] == ["user", "assistant", "tool", "assistant", "user"]

    listed = client.get("/api/conversations").json()
    assert listed[0]["id"] == conv["id"]

    assert (
        client.patch(f"/api/conversations/{conv['id']}", json={"title": "Renamed"}, headers=H).json()["title"]
        == "Renamed"
    )
    assert client.delete(f"/api/conversations/{conv['id']}", headers=H).status_code == 200
    assert client.get(f"/api/conversations/{conv['id']}").status_code == 404


def test_chat_errors(make_client, settings):
    client = make_client()
    conv = client.post("/api/conversations", json={}, headers=H).json()
    r = client.post(f"/api/conversations/{conv['id']}/messages", json={"content": "x", "model": "gpt-9"}, headers=H)
    assert r.status_code == 400
    assert client.post("/api/conversations/nope/messages", json={"content": "x"}, headers=H).status_code == 404

    settings.api_key = ""
    events = stream_events(client, conv["id"], "hello")
    assert events[-1]["type"] == "error" and "DEEPSEEK_API_KEY" in events[-1]["error"]


def test_stop_generation_keeps_partial_answer(make_client):
    import asyncio
    import threading

    class SlowLLM:
        async def stream(self, messages, tools=None, model=None):
            yield {"type": "content", "text": "Partial answer"}
            await asyncio.sleep(30)

    client = make_client(SlowLLM())
    conv = client.post("/api/conversations", json={}, headers=H).json()
    result = {}
    # TestClient buffers streamed bodies, so consume the stream in another thread.
    thread = threading.Thread(target=lambda: result.setdefault("events", stream_events(client, conv["id"], "hi")))
    thread.start()
    wait_until(lambda: client.get(f"/api/conversations/{conv['id']}").json()["running"])
    time.sleep(0.1)
    assert client.post(f"/api/conversations/{conv['id']}/stop", headers=H).json() == {"stopped": True}
    thread.join(10)
    assert [e["type"] for e in result["events"]] == ["token", "stopped"]
    data = client.get(f"/api/conversations/{conv['id']}").json()
    assert data["running"] is False
    assert data["messages"][-1]["content"].startswith("Partial answer")
    assert "[stopped]" in data["messages"][-1]["content"]


def test_background_task_api(make_client):
    client = make_client(FakeLLM(["Task report."]))
    r = client.post("/api/tasks", json={"title": "Job", "goal": "Do the job"}, headers=H)
    assert r.status_code == 200
    task_id = r.json()["id"]
    done = wait_until(lambda: (t := client.get(f"/api/tasks/{task_id}").json())["status"] == "completed" and t)
    assert done["last_result"] == "Task report."
    assert done["runs"][0]["events"][-1]["type"] == "final"
    assert client.get("/api/tasks").json()[0]["id"] == task_id

    assert client.post("/api/tasks", json={"goal": "x", "interval_minutes": 0.1}, headers=H).status_code == 400
    assert client.post(f"/api/tasks/{task_id}/resume", headers=H).status_code == 400
    assert client.get("/api/tasks/missing").status_code == 404
    assert client.delete(f"/api/tasks/{task_id}", headers=H).status_code == 200
    assert client.get("/api/tasks").json() == []


def test_task_controls(make_client):
    client = make_client(FakeLLM(["ok"]))
    task = client.post(
        "/api/tasks", json={"goal": "later", "delay_minutes": 60, "interval_minutes": 30}, headers=H
    ).json()
    assert client.post(f"/api/tasks/{task['id']}/pause", headers=H).json()["status"] == "paused"
    assert client.post(f"/api/tasks/{task['id']}/resume", headers=H).json()["status"] == "pending"
    assert client.post(f"/api/tasks/{task['id']}/cancel", headers=H).json()["status"] == "cancelled"
    client.post(f"/api/tasks/{task['id']}/run", headers=H)
    wait_until(lambda: client.get(f"/api/tasks/{task['id']}").json()["run_count"] == 1)


def test_files_api(make_client, settings):
    client = make_client()
    r = client.put("/api/files/raw?path=docs/a.md", content=b"# Title", headers=H)
    assert r.json() == {"path": "docs/a.md", "size": 7}
    listing = client.get("/api/files").json()
    assert listing["path"] == "." and listing["entries"][0]["name"] == "docs"
    assert client.get("/api/files?path=docs").json()["entries"][0]["path"] == "docs/a.md"

    raw = client.get("/api/files/raw?path=docs/a.md")
    assert raw.text == "# Title"
    assert raw.headers["content-security-policy"].startswith("sandbox")
    assert "attachment" in client.get("/api/files/raw?path=docs/a.md&download=true").headers["content-disposition"]

    assert client.get("/api/files/raw?path=../../etc/passwd").status_code == 400
    assert client.put("/api/files/raw?path=../evil.txt", content=b"x", headers=H).status_code == 400
    assert client.get("/api/files/raw?path=nope.txt").status_code == 404
    assert client.delete("/api/files?path=.", headers=H).status_code == 400
    assert client.delete("/api/files?path=docs", headers=H).status_code == 200
    assert not (settings.workspace_dir / "docs").exists()


def test_cross_site_writes_and_foreign_hosts_are_rejected(make_client):
    client = make_client()
    # A cross-site page can POST "simple" requests but cannot add custom headers.
    r = client.post("/api/tasks", content=json.dumps({"goal": "evil"}))
    assert r.status_code == 403
    assert client.get("/api/tasks").json() == []
    # DNS rebinding: requests addressed to another host name.
    assert client.get("/api/status", headers={"Host": "evil.example:8000"}).status_code == 400
    assert client.get("/api/status", headers={"Host": "localhost:8000"}).status_code == 200


def test_password_protection(settings, make_client):
    settings.password = "s3cret"
    client = make_client()
    assert client.get("/api/status").status_code == 401
    bad = base64.b64encode(b"user:wrong").decode()
    assert client.get("/api/status", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    good = base64.b64encode(b"anyone:s3cret").decode()
    assert client.get("/api/status", headers={"Authorization": f"Basic {good}"}).status_code == 200


def test_new_task_starts_immediately_without_waiting_for_poll(settings, make_client):
    settings.scheduler_poll_seconds = 30
    client = make_client(FakeLLM(["done"]))
    time.sleep(0.2)  # let the scheduler settle into its long wait
    task_id = client.post("/api/tasks", json={"goal": "quick"}, headers=H).json()["id"]
    wait_until(lambda: client.get(f"/api/tasks/{task_id}").json()["status"] == "completed", timeout=3)
