"""HTTP API + web UI."""

from __future__ import annotations

import base64
import json
import mimetypes
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__
from .agent import Agent
from .chat import DEFAULT_TITLE, ChatService
from .config import Settings
from .db import PENDING, RUNNING, Database
from .llm import LLM, DeepSeekLLM
from .tasks import TaskManager
from .tools import ToolError, build_registry
from .tools.files import list_entries, relative, resolve_path

STATIC_DIR = Path(__file__).parent / "static"


class BasicAuthMiddleware:
    """Optional password protection (any username) using the browser's built-in login prompt."""

    def __init__(self, app, password: str):
        self.app = app
        self.password = password.encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self._authorized(scope):
            return await self.app(scope, receive, send)
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"www-authenticate", b'Basic realm="Self-AI"'), (b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"Authentication required"})

    def _authorized(self, scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization" and value[:6].lower() == b"basic ":
                try:
                    _, _, password = base64.b64decode(value[6:]).partition(b":")
                except ValueError:
                    return False
                return secrets.compare_digest(password, self.password)
        return False


class RequestGuardMiddleware:
    """Protects a local agent from other websites:
    - Host allowlist: blocks DNS-rebinding (evil.com resolving to 127.0.0.1).
    - Writes to /api/ need an `X-SelfAI` header, which cross-site pages can't send
      without a CORS preflight that this server never approves (blocks CSRF)."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app, allowed_hosts: list[str]):
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if "*" not in self.allowed_hosts:
            host = headers.get("host", "")
            if host.startswith("["):  # [ipv6]:port
                hostname = host[1:].split("]", 1)[0]
            else:
                hostname = host.split(":", 1)[0]
            if hostname.lower() not in self.allowed_hosts:
                return await self._reject(send, 400, f"Host '{host}' is not allowed. Add it to SELFAI_ALLOWED_HOSTS.")
        if scope["method"] not in self.SAFE_METHODS and scope["path"].startswith("/api/") and "x-selfai" not in headers:
            return await self._reject(send, 403, "Missing X-SelfAI header.")
        return await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send, status: int, message: str) -> None:
        body = json.dumps({"detail": message}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=200_000)
    model: str | None = None


class ConversationCreate(BaseModel):
    title: str | None = None


class ConversationUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class TaskCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    goal: str = Field(min_length=1, max_length=50_000)
    delay_minutes: float = 0
    interval_minutes: float | None = None


def create_app(settings: Settings | None = None, llm: LLM | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    agent = Agent(llm or DeepSeekLLM(settings), build_registry(), settings)
    tasks = TaskManager(db, agent, settings)
    chat = ChatService(db, agent, settings, tasks)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await tasks.start()
        try:
            yield
        finally:
            await chat.shutdown()
            await tasks.stop()
            db.close()

    app = FastAPI(title="Self-AI", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.db = db
    app.state.tasks = tasks
    app.state.chat = chat
    app.add_middleware(RequestGuardMiddleware, allowed_hosts=settings.allowed_hosts)
    if settings.password:
        app.add_middleware(BasicAuthMiddleware, password=settings.password)

    workspace = settings.workspace_dir

    def get_conversation_or_404(conv_id: str) -> dict:
        conv = db.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found")
        return conv

    def get_task_or_404(task_id: str) -> dict:
        task = db.get_task(task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task

    def workspace_path(path: str) -> Path:
        try:
            return resolve_path(workspace, path)
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------------ status

    @app.get("/api/status")
    def status() -> dict:
        return {
            "version": __version__,
            "model": settings.model,
            "models": settings.models,
            "base_url": settings.base_url,
            "api_key_configured": settings.api_key_configured,
            "search_provider": "tavily" if settings.tavily_api_key else "duckduckgo",
            "workspace": str(workspace.resolve()),
            "tasks": {
                "running": len(db.list_tasks(RUNNING)),
                "pending": len(db.list_tasks(PENDING)),
            },
        }

    # ------------------------------------------------------------------ conversations

    @app.get("/api/conversations")
    def list_conversations() -> list[dict]:
        convs = db.list_conversations()
        for c in convs:
            c["running"] = chat.is_running(c["id"])
        return convs

    @app.post("/api/conversations")
    def create_conversation(body: ConversationCreate | None = None) -> dict:
        return db.create_conversation((body.title if body else None) or DEFAULT_TITLE)

    @app.get("/api/conversations/{conv_id}")
    def get_conversation(conv_id: str) -> dict:
        conv = get_conversation_or_404(conv_id)
        return {**conv, "running": chat.is_running(conv_id), "messages": db.list_messages(conv_id)}

    @app.patch("/api/conversations/{conv_id}")
    def rename_conversation(conv_id: str, body: ConversationUpdate) -> dict:
        get_conversation_or_404(conv_id)
        db.rename_conversation(conv_id, body.title.strip())
        return get_conversation_or_404(conv_id)

    @app.delete("/api/conversations/{conv_id}")
    async def delete_conversation(conv_id: str) -> dict:
        get_conversation_or_404(conv_id)
        await chat.stop(conv_id)
        db.delete_conversation(conv_id)
        return {"ok": True}

    @app.post("/api/conversations/{conv_id}/messages")
    async def send_message(conv_id: str, body: SendMessage) -> StreamingResponse:
        get_conversation_or_404(conv_id)
        if body.model and body.model not in settings.models:
            raise HTTPException(400, f"Unknown model '{body.model}'. Available: {', '.join(settings.models)}")
        if chat.is_running(conv_id):
            raise HTTPException(409, "This conversation is already generating a reply.")
        queue = chat.start(conv_id, body.content, body.model)

        async def events():
            while True:
                ev = await queue.get()
                if ev is None:
                    yield "event: end\ndata: {}\n\n"
                    return
                yield f"data: {json.dumps(ev)}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/conversations/{conv_id}/stop")
    async def stop_conversation(conv_id: str) -> dict:
        get_conversation_or_404(conv_id)
        return {"stopped": await chat.stop(conv_id)}

    # ------------------------------------------------------------------ tasks

    @app.get("/api/tasks")
    def list_tasks() -> list[dict]:
        return db.list_tasks()

    @app.post("/api/tasks")
    async def create_task(body: TaskCreate) -> dict:
        try:
            return tasks.create_task(body.title, body.goal, body.delay_minutes, body.interval_minutes, "user")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = get_task_or_404(task_id)
        runs = db.list_runs(task_id)
        for i, run in enumerate(runs):
            # Full event logs for the most recent runs only.
            run["events"] = db.list_events(run["id"]) if i < 5 else None
        return {**task, "runs": runs}

    def task_action(task_id: str, action) -> dict:
        get_task_or_404(task_id)
        try:
            action(task_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return get_task_or_404(task_id)

    @app.post("/api/tasks/{task_id}/run")
    async def run_task(task_id: str) -> dict:
        return task_action(task_id, tasks.run_now)

    @app.post("/api/tasks/{task_id}/pause")
    async def pause_task(task_id: str) -> dict:
        return task_action(task_id, tasks.pause)

    @app.post("/api/tasks/{task_id}/resume")
    async def resume_task(task_id: str) -> dict:
        return task_action(task_id, tasks.resume)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str) -> dict:
        get_task_or_404(task_id)
        await tasks.cancel(task_id)
        return get_task_or_404(task_id)

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str) -> dict:
        get_task_or_404(task_id)
        await tasks.delete(task_id)
        return {"ok": True}

    # ------------------------------------------------------------------ files

    @app.get("/api/files")
    def list_files(path: str = ".") -> dict:
        try:
            entries = list_entries(workspace, path)
        except ToolError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"path": relative(workspace, workspace_path(path)), "entries": entries}

    @app.get("/api/files/raw")
    def read_raw(path: str, download: bool = False) -> FileResponse:
        target = workspace_path(path)
        if not target.is_file():
            raise HTTPException(404, "File not found")
        media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type in ("application/json", "application/javascript"):
            media_type += "; charset=utf-8"
        headers = {
            # Files may contain web content the AI saved. A sandboxed page runs in an opaque
            # origin, so its scripts can't use this app's API.
            "Content-Security-Policy": "sandbox allow-scripts allow-forms allow-modals allow-popups allow-downloads",
            "X-Content-Type-Options": "nosniff",
        }
        return FileResponse(
            target,
            media_type=media_type,
            headers=headers,
            filename=target.name if download else None,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.put("/api/files/raw")
    async def upload_file(path: str, request: Request) -> dict:
        target = workspace_path(path)
        if target == workspace.resolve() or target.is_dir():
            raise HTTPException(400, "Path must include a file name")
        limit = settings.max_download_mb * 1024 * 1024
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        size = 0
        try:
            with tmp.open("wb") as fh:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(413, f"File exceeds {settings.max_download_mb} MB")
                    fh.write(chunk)
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
        return {"path": relative(workspace, target), "size": size}

    @app.delete("/api/files")
    def delete_file(path: str) -> dict:
        target = workspace_path(path)
        if target == workspace.resolve():
            raise HTTPException(400, "Refusing to delete the workspace")
        if not target.exists():
            raise HTTPException(404, "Not found")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return {"ok": True}

    # ------------------------------------------------------------------ UI

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    return app
