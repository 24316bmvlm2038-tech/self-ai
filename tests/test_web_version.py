"""The always-on web version: data in Supabase (faked here), tasks started by a scheduler."""

import asyncio
import base64
import json
import os
import time

import httpx
import pytest
from starlette.testclient import TestClient

from selfai import db as store
from selfai.config import Settings
from selfai.db import Database
from selfai.remote import RemoteDatabase, StorageError
from selfai.server import create_app

from .conftest import FakeLLM, FakeOllama, ToolCall
from .fake_supabase import FakeSupabase
from .test_cloud_and_auth import FakeGroq

H = {"X-SelfAI": "1"}
CRON = {**H, "X-SelfAI-Cron": "tick-secret", "Authorization": ""}  # the scheduler has no password
SIGNED_IN = {"Authorization": "Basic " + base64.b64encode(b"me:correct horse").decode()}


@pytest.fixture
def supabase():
    return FakeSupabase()


@pytest.fixture
def web_settings(tmp_path, supabase):
    s = Settings(
        provider="groq",
        model="",
        data_dir=tmp_path / "server1",
        cloud=True,
        supabase_url="https://project.supabase.co",
        supabase_key=supabase.api_key,
        db_secret=supabase.secret,
        cron_secret="tick-secret",
        password="correct horse",
        run_time_limit=60,
        allow_private_network=True,
        tool_timeout_seconds=10,
    )
    s.ensure_dirs()
    return s


def web_client(settings, supabase, llm=None):
    app = create_app(
        settings,
        llm or FakeLLM(["Hello!"]),
        FakeOllama().client(),
        httpx.MockTransport(FakeGroq().handle),
        supabase.transport(),
    )
    app.state.db.set_setting("key:groq", "gsk-good")
    return TestClient(app, headers=SIGNED_IN)


def stream_events(client, conv_id, content):
    with client.stream("POST", f"/api/conversations/{conv_id}/messages", json={"content": content}, headers=H) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ") and line != "data: {}"]


# --------------------------------------------------------------------------- storage


@pytest.fixture(params=["sqlite", "supabase"])
def any_db(request, tmp_path, supabase):
    if request.param == "sqlite":
        return Database(tmp_path / "x.db")
    return RemoteDatabase("https://project.supabase.co", supabase.api_key, supabase.secret, supabase.transport())


def test_both_databases_behave_the_same(any_db):
    db = any_db
    assert db.get_setting("provider") is None
    db.set_setting("provider", "groq")
    db.set_setting("provider", "gemini")
    assert db.get_setting("provider") == "gemini"
    db.delete_setting("provider")
    assert db.get_setting("provider") is None

    conv = db.create_conversation("Plans")
    db.add_message(conv["id"], {"role": "user", "content": "hi"})
    calls = [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]
    db.add_message(conv["id"], {"role": "assistant", "content": None, "tool_calls": calls}, reasoning="hmm")
    msgs = db.list_messages(conv["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["tool_calls"] == calls
    assert msgs[1]["reasoning"] == "hmm" and msgs[0]["tool_calls"] is None
    db.rename_conversation(conv["id"], "Trip")
    assert [c["title"] for c in db.list_conversations()] == ["Trip"]

    now = time.time()
    later = db.create_task("Later", "goal", now + 600, 30)
    due = db.create_task("Due", "goal", now - 1)
    assert later["created_by"] == "user" and later["run_count"] == 0 and later["status"] == store.PENDING
    assert [t["id"] for t in db.due_tasks(now, 5)] == [due["id"]]
    assert db.next_due_time() == pytest.approx(now - 1)
    assert db.count_active_tasks() == 2
    assert db.claim_task(due["id"]) and not db.claim_task(due["id"])  # only one server gets it
    run_id = db.create_run(due["id"])
    db.add_event(run_id, "tool_call", {"name": "web_search"})
    assert [e["data"] for e in db.list_events(run_id)] == [{"name": "web_search"}]
    assert [r["id"] for r in db.stale_runs(time.time() + 1)] == [run_id]
    db.finish_run(run_id, store.COMPLETED, "done")
    assert db.stale_runs(time.time() + 1) == []
    db.update_task(due["id"], status=store.COMPLETED, run_count=1)
    assert db.get_task(due["id"])["run_count"] == 1
    assert len(db.list_tasks(store.COMPLETED)) == 1 and len(db.list_tasks()) == 2

    db.delete_task(due["id"])  # runs and events go with it
    assert db.get_task(due["id"]) is None and db.list_runs(due["id"]) == []
    db.delete_conversation(conv["id"])
    assert db.list_messages(conv["id"]) == [] and db.list_conversations() == []


def test_database_needs_the_secret(supabase):
    good = RemoteDatabase("https://project.supabase.co", supabase.api_key, supabase.secret, supabase.transport())
    good.set_setting("key:groq", "gsk-private")
    thief = RemoteDatabase("https://project.supabase.co", supabase.api_key, "guess", supabase.transport())
    assert thief.get_setting("key:groq") is None
    with pytest.raises(StorageError, match="401"):
        thief.set_setting("provider", "groq")


def test_web_settings_from_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith(("SELFAI_", "DEEPSEEK_")):
            monkeypatch.delenv(name)
    for name, value in {
        "SELFAI_SUPABASE_URL": "https://abc.supabase.co/",
        "SELFAI_SUPABASE_KEY": "sb_publishable_x",
        "SELFAI_DB_SECRET": "db",
        "SELFAI_CRON_SECRET": "cron",
        "SELFAI_PROVIDER": "ollama",  # not possible on a web server
    }.items():
        monkeypatch.setenv(name, value)
    s = Settings.from_env(env_file=None)
    assert s.cloud and s.supabase_url == "https://abc.supabase.co" and s.provider == "groq"
    assert str(s.data_dir) == "/tmp/selfai" and s.run_time_limit == 240 and s.max_download_mb == 4
    monkeypatch.delenv("SELFAI_SUPABASE_URL")
    assert not Settings.from_env(env_file=None).cloud


def test_web_version_requires_its_settings(web_settings, supabase):
    web_settings.password = ""
    with pytest.raises(RuntimeError, match="SELFAI_PASSWORD"):
        create_app(web_settings, FakeLLM([]), FakeOllama().client(), None, supabase.transport())


# --------------------------------------------------------------------------- the app


def test_web_version_chat_and_files_survive_a_new_server(web_settings, supabase, tmp_path):
    llm = FakeLLM([[ToolCall("write_file", path="notes/idea.md", content="# Idea")], "Saved notes/idea.md."])
    with web_client(web_settings, supabase, llm) as client:
        assert client.get("/api/status", headers={"Authorization": ""}).status_code == 401  # password needed
        status = client.get("/api/status").json()
        assert status["cloud"] and status["ready"] and status["provider"] == "groq"
        models = client.get("/api/models").json()
        assert models["cloud"] and "ollama" not in [p["id"] for p in models["providers"]]
        assert client.post("/api/providers/active", json={"provider": "ollama"}, headers=H).status_code == 400
        assert client.post("/api/models/pull", json={"name": "qwen3.5:4b"}, headers=H).status_code == 400

        conv = client.post("/api/conversations", json={}, headers=H).json()
        events = stream_events(client, conv["id"], "Write my idea down")
        assert events[-1] == {"type": "done", "content": "Saved notes/idea.md."}
    [stored] = supabase.tables["files"]
    assert stored["path"] == "notes/idea.md" and stored["size"] == 6

    # A different server instance (empty disk) sees the same chats and files.
    web_settings.data_dir = tmp_path / "server2"
    web_settings.ensure_dirs()
    with web_client(web_settings, supabase) as client:
        chat = client.get(f"/api/conversations/{conv['id']}").json()
        assert [m["role"] for m in chat["messages"]] == ["user", "assistant", "tool", "assistant"]
        listing = client.get("/api/files", params={"path": "notes"}).json()
        assert [e["name"] for e in listing["entries"]] == ["idea.md"]
        assert client.get("/api/files/raw", params={"path": "notes/idea.md"}).text == "# Idea"

        put = client.put("/api/files/raw", params={"path": "upload.txt"}, content=b"hello", headers=H)
        assert put.status_code == 200
        assert client.delete("/api/files", params={"path": "notes"}, headers=H).status_code == 200
    assert [f["path"] for f in supabase.tables["files"]] == ["upload.txt"]


def test_web_version_runs_tasks_when_the_scheduler_calls(web_settings, supabase):
    llm = FakeLLM([[ToolCall("write_file", path="report.md", content="ok")], "Report written."])
    with web_client(web_settings, supabase, llm) as client:
        task = client.post("/api/tasks", json={"goal": "Write a report"}, headers=H).json()
        time.sleep(0.2)
        assert client.get(f"/api/tasks/{task['id']}").json()["status"] == store.PENDING  # no background loop

        assert client.post("/api/cron/tick", headers=H).status_code == 403
        assert client.post("/api/cron/tick", headers={**CRON, "X-SelfAI-Cron": "nope"}).status_code == 403
        tick = client.post("/api/cron/tick", headers=CRON)
        assert tick.status_code == 200 and tick.json() == {"recovered": 0, "ran": [task["id"]]}

        done = client.get(f"/api/tasks/{task['id']}").json()
        assert done["status"] == store.COMPLETED and done["last_result"] == "Report written."
        assert [e["type"] for e in done["runs"][0]["events"]] == ["tool_call", "tool_result", "final"]
        assert client.post("/api/cron/tick", headers=CRON).json()["ran"] == []  # nothing left to do
    assert [f["path"] for f in supabase.tables["files"]] == ["report.md"]


def test_runs_cut_off_by_the_host_are_finished_later(web_settings, supabase):
    with web_client(web_settings, supabase, FakeLLM([])) as client:
        db = client.app.state.db
        once = db.create_task("Once", "goal", time.time() + 3600)
        repeat = db.create_task("Repeat", "goal", time.time() + 3600, interval_minutes=60)
        for task in (once, repeat):
            assert db.claim_task(task["id"])
            run_id = db.create_run(task["id"])
            supabase.tables["task_runs"][-1]["started_at"] = time.time() - 3600  # killed long ago
        assert db.stale_runs(time.time())[-1]["id"] == run_id
        assert client.post("/api/cron/tick", headers=CRON).json() == {"recovered": 2, "ran": []}
        assert db.get_task(once["id"])["status"] == store.FAILED
        again = db.get_task(repeat["id"])
        assert again["status"] == store.PENDING and again["next_run_at"] > time.time() + 3000
        assert "Interrupted" in again["last_result"]


def test_time_limits_on_the_web_version(web_settings, supabase):
    web_settings.run_time_limit = 0.3

    async def slow():
        await asyncio.sleep(5)
        return "too late"

    llm = FakeLLM(responder=lambda messages, tools: slow())
    with web_client(web_settings, supabase, llm) as client:
        conv = client.post("/api/conversations", json={}, headers=H).json()
        events = stream_events(client, conv["id"], "Think hard")
        assert events[-1]["type"] == "notice" and "time limit" in events[-1]["text"]

        task = client.post("/api/tasks", json={"goal": "Think very hard"}, headers=H).json()
        client.post("/api/cron/tick", headers=CRON)
        done = client.get(f"/api/tasks/{task['id']}").json()
        assert done["status"] == store.FAILED and "time limit" in done["last_result"]
