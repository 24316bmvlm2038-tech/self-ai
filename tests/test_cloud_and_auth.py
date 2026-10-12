import json

import httpx
import pytest
from starlette.testclient import TestClient

from selfai.auth import SESSION_COOKIE, session_token
from selfai.db import Database
from selfai.providers import CLOUD_PROVIDERS, active_provider, pick_model, run_settings
from selfai.server import create_app

from .conftest import FakeOllama

H = {"X-SelfAI": "1"}


class FakeGroq:
    """Stand-in for an OpenAI-compatible cloud API (Groq here)."""

    def __init__(self):
        self.models = ["openai/gpt-oss-120b", "llama-3.3-70b-versatile", "whisper-large-v3", "meta-llama/llama-guard-4"]
        self.requests: list[dict] = []
        self.status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != "Bearer gsk-good":
            return httpx.Response(401, json={"error": {"message": "Invalid API Key"}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in self.models]})
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), **body})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "Rate limit reached"}})
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "Hi from "}, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {"content": "the cloud!"}, "finish_reason": "stop"}]},
        ]
        text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})


def stream_events(client, conv_id, content):
    with client.stream("POST", f"/api/conversations/{conv_id}/messages", json={"content": content}, headers=H) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ") and line != "data: {}"]


@pytest.fixture
def cloud(settings):
    groq = FakeGroq()
    with TestClient(create_app(settings, None, FakeOllama().client(), httpx.MockTransport(groq.handle))) as client:
        yield client, groq


def test_switch_to_free_cloud_ai_and_chat(cloud, settings):
    client, groq = cloud
    models = client.get("/api/models").json()
    ids = [p["id"] for p in models["providers"]]
    assert ids == ["ollama", "groq", "gemini", "openrouter", "deepseek"]
    groq_card = next(p for p in models["providers"] if p["id"] == "groq")
    assert groq_card["free"] and not groq_card["has_key"] and groq_card["key_url"].startswith("https://")

    bad = client.post("/api/providers/active", json={"provider": "groq", "api_key": "nope"}, headers=H)
    assert bad.status_code == 400 and "rejected the API key" in bad.json()["detail"]
    assert client.post("/api/providers/active", json={"provider": "groq"}, headers=H).status_code == 400  # no key yet

    ok = client.post("/api/providers/active", json={"provider": "groq", "api_key": "gsk-good"}, headers=H)
    assert ok.status_code == 200 and ok.json()["active"] == "groq"

    status = client.get("/api/status").json()
    assert status["ready"] and status["provider"] == "groq" and status["provider_name"] == "Groq"
    assert status["model"] == "openai/gpt-oss-120b"
    assert status["models"] == ["llama-3.3-70b-versatile", "openai/gpt-oss-120b"]  # speech/safety models hidden
    assert status["capabilities"] is None  # no Think toggle for cloud models

    conv = client.post("/api/conversations", json={}, headers=H).json()
    events = stream_events(client, conv["id"], "hello")
    assert "".join(e["text"] for e in events if e["type"] == "token") == "Hi from the cloud!"
    [sent] = groq.requests
    assert sent["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert sent["model"] == "openai/gpt-oss-120b" and sent["tools"]

    client.post("/api/models/active", json={"name": "llama-3.3-70b-versatile"}, headers=H)
    assert client.get("/api/status").json()["model"] == "llama-3.3-70b-versatile"

    # Back to the phone: the on-device model choice is untouched.
    client.post("/api/providers/active", json={"provider": "ollama"}, headers=H)
    status = client.get("/api/status").json()
    assert status["provider"] == "ollama" and status["model"] == "qwen3.5:4b"


def test_cloud_limits_are_explained(cloud, settings):
    client, groq = cloud
    client.post("/api/providers/active", json={"provider": "groq", "api_key": "gsk-good"}, headers=H)
    groq.status = 400  # not retried
    conv = client.post("/api/conversations", json={}, headers=H).json()
    events = stream_events(client, conv["id"], "hello")
    assert events[-1]["type"] == "error" and events[-1]["error"].startswith("Groq error 400")


def test_cloud_without_key_explains(cloud):
    client, _ = cloud
    client.app.state.db.set_setting("provider", "gemini")
    status = client.get("/api/status").json()
    assert status["problem_code"] == "config" and "free Google Gemini key in Models" in status["problem"]


def test_budgets_follow_the_active_ai(settings):
    db = Database(settings.db_path)
    assert run_settings(db, settings) is settings  # configured provider: startup budgets
    db.set_setting("provider", "gemini")
    assert active_provider(db, settings) == "gemini"
    big = run_settings(db, settings)
    assert big.history_max_chars == 200_000 and big.max_tool_result_chars == 20_000
    db.set_setting("provider", "groq")  # free plan with few tokens per minute
    small = run_settings(db, settings)
    assert small.history_max_chars == 16_000 and small.max_tool_result_chars == 6_000
    db.set_setting("provider", "skynet")  # unknown values are ignored
    assert active_provider(db, settings) == settings.provider


def test_pick_model_falls_back_when_default_retired():
    gemini = CLOUD_PROVIDERS["gemini"]
    assert pick_model(["gemini-9-pro", "gemini-9-flash"], gemini) == "gemini-9-flash"
    assert pick_model(["gemini-2.5-flash", "x"], gemini) == "gemini-2.5-flash"
    assert pick_model(["only-one"], CLOUD_PROVIDERS["openrouter"]) == "only-one"


# --------------------------------------------------------------------------- sign-in


@pytest.fixture
def protected(settings):
    settings.password = "correct horse"
    with TestClient(create_app(settings, None, FakeOllama().client()), follow_redirects=False) as client:
        yield client


def test_internet_visitors_must_sign_in(protected):
    page = protected.get("/")
    assert page.status_code == 401 and "Enter your password" in page.text
    assert protected.get("/api/status").status_code == 401
    assert protected.get("/static/manifest.webmanifest").status_code == 200  # app install still works
    assert protected.get("/sw.js").status_code == 200

    wrong = protected.post("/login", data={"password": "nope"})
    assert wrong.status_code == 401 and "Wrong password" in wrong.text
    ok = protected.post("/login", data={"password": "correct horse"})
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    cookie = ok.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}={session_token('correct horse')}") and "HttpOnly" in cookie

    assert protected.get("/api/status").status_code == 200  # cookie kept by the client
    assert protected.get("/", headers={"Host": "ai.example.dpdns.org"}).status_code == 200  # any host name


def test_secure_cookie_and_lockout(protected, monkeypatch):
    monkeypatch.setattr("selfai.auth.asyncio.sleep", lambda s: _noop())
    ok = protected.post("/login", data={"password": "correct horse"}, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in ok.headers["set-cookie"]
    protected.cookies.clear()
    for _ in range(10):
        protected.post("/login", data={"password": "guess"})
    locked = protected.post("/login", data={"password": "correct horse"})
    assert locked.status_code == 429 and "Too many" in locked.text


async def _noop():
    return None


def test_this_device_needs_no_password(settings):
    settings.password = "pw"
    app = create_app(settings, None, FakeOllama().client())
    with TestClient(app, client=("127.0.0.1", 50000)) as local:
        assert local.get("/api/status").status_code == 200
        # The same address through a tunnel (which adds Cloudflare headers) is a visitor.
        assert local.get("/api/status", headers={"CF-Connecting-IP": "203.0.113.9"}).status_code == 401


def test_never_exposed_without_password(settings):
    settings.password = ""
    with TestClient(create_app(settings, None, FakeOllama().client()), client=("127.0.0.1", 1)) as client:
        assert client.get("/api/status").status_code == 200
        blocked = client.get("/", headers={"CF-Connecting-IP": "203.0.113.9", "Host": "localhost"})
        assert blocked.status_code == 403 and "SELFAI_PASSWORD" in blocked.text
