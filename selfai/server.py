"""HTTP API + web UI. Built on Starlette (pure Python), so it installs on phones too."""

from __future__ import annotations

import base64
import json
import mimetypes
import secrets
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from .agent import Agent
from .chat import DEFAULT_TITLE, ChatService
from .config import Settings
from .db import PENDING, RUNNING, Database
from .errors import LLMError
from .llm import LLM, build_llm
from .models import RECOMMENDED_MODELS, ModelManager, active_model, device_ram_gb, is_android, suggest_model
from .ollama import OllamaClient, normalize_model_name
from .tasks import TaskManager
from .tools import ToolError, build_registry
from .tools.files import list_entries, relative, resolve_path

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("application/manifest+json", ".webmanifest")


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


# ---------------------------------------------------------------------- request helpers


async def json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "Request body must be JSON.") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "Request body must be a JSON object.")
    return data


def text_field(data: dict, key: str, *, required: bool = False, max_len: int = 200, default: str = "") -> str:
    value = data.get(key)
    if value is None:
        if required:
            raise HTTPException(400, f"'{key}' is required.")
        return default
    if not isinstance(value, str):
        raise HTTPException(400, f"'{key}' must be a string.")
    if required and not value.strip():
        raise HTTPException(400, f"'{key}' must not be empty.")
    if len(value) > max_len:
        raise HTTPException(400, f"'{key}' is too long (max {max_len} characters).")
    return value


def number_field(data: dict, key: str, default: float | None = None) -> float | None:
    value = data.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(400, f"'{key}' must be a number.")
    return float(value)


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


# ---------------------------------------------------------------------- app


def create_app(
    settings: Settings | None = None, llm: LLM | None = None, ollama: OllamaClient | None = None
) -> Starlette:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    ollama = ollama or OllamaClient(settings.ollama_url)
    agent = Agent(llm or build_llm(settings, ollama), build_registry(), settings)
    tasks = TaskManager(db, agent, settings)
    chat = ChatService(db, agent, settings, tasks)
    models = ModelManager(ollama, db, settings)
    workspace = settings.workspace_dir

    def conversation_or_404(conv_id: str) -> dict:
        conv = db.get_conversation(conv_id)
        if conv is None:
            raise HTTPException(404, "Conversation not found")
        return conv

    def task_or_404(task_id: str) -> dict:
        task = db.get_task(task_id)
        if task is None:
            raise HTTPException(404, "Task not found")
        return task

    def workspace_path(path: str | None) -> Path:
        if path is None:
            raise HTTPException(400, "'path' is required.")
        try:
            return resolve_path(workspace, path)
        except ToolError as exc:
            raise HTTPException(400, str(exc)) from exc

    def require_local() -> None:
        if not settings.is_local:
            raise HTTPException(400, "Model downloads are only available with the local (Ollama) provider.")

    # ------------------------------------------------------------------ status & models

    async def status(request: Request) -> JSONResponse:
        problem, code = settings.config_problem(), "config"
        model = active_model(db, settings)
        names = settings.models
        capabilities = None
        if settings.is_local and not problem:
            try:
                model, names = await models.resolve_active()
            except LLMError as exc:
                problem, code, names = str(exc), "ollama_down", []
            else:
                if not names:
                    problem, code = "No AI model is downloaded yet. Open Models to download a free one.", "no_models"
                else:
                    caps = await ollama.capabilities(model)
                    capabilities = sorted(caps) if caps is not None else None
        return JSONResponse(
            {
                "version": __version__,
                "provider": settings.provider,
                "model": model,
                "models": names,
                "capabilities": capabilities,
                "ready": problem is None,
                "problem": problem,
                "problem_code": code if problem else None,
                "search_provider": "tavily" if settings.tavily_api_key else "duckduckgo",
                "workspace": str(workspace.resolve()),
                "tasks": {"running": len(db.list_tasks(RUNNING)), "pending": len(db.list_tasks(PENDING))},
            }
        )

    async def list_models(request: Request) -> JSONResponse:
        ram = device_ram_gb()
        android = is_android()
        result: dict = {
            "provider": settings.provider,
            "active": active_model(db, settings),
            "installed": [],
            "recommended": [],
            "pulls": models.pulls,
            "ram_gb": ram,
            "android": android,
            "suggested": suggest_model(ram, android),
            "error": None,
        }
        if not settings.is_local:
            result["installed"] = [{"name": m} for m in settings.models]
            return JSONResponse(result)
        try:
            installed = await models.installed()
        except LLMError as exc:
            result["error"] = str(exc)
            installed = []
        for m in installed:
            caps = await ollama.capabilities(m["name"])
            m["capabilities"] = sorted(caps) if caps is not None else None
        names = {normalize_model_name(m["name"]) for m in installed}
        result["installed"] = installed
        result["recommended"] = [
            {**r, "installed": normalize_model_name(r["name"]) in names} for r in RECOMMENDED_MODELS
        ]
        return JSONResponse(result)

    async def pull_model(request: Request) -> JSONResponse:
        require_local()
        name = text_field(await json_body(request), "name", required=True)
        try:
            models.start_pull(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse({"ok": True}, status_code=202)

    async def delete_model(request: Request) -> JSONResponse:
        require_local()
        name = request.query_params.get("name", "")
        try:
            await models.delete(name)
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
        return JSONResponse({"ok": True})

    async def set_active_model(request: Request) -> JSONResponse:
        name = text_field(await json_body(request), "name", required=True).strip()
        if not settings.is_local and name not in settings.models:
            raise HTTPException(400, f"Unknown model '{name}'. Available: {', '.join(settings.models)}")
        db.set_setting("model", name)
        return JSONResponse({"active": name})

    # ------------------------------------------------------------------ conversations

    async def list_conversations(request: Request) -> JSONResponse:
        convs = db.list_conversations()
        for c in convs:
            c["running"] = chat.is_running(c["id"])
        return JSONResponse(convs)

    async def create_conversation(request: Request) -> JSONResponse:
        data = await json_body(request) if await request.body() else {}
        return JSONResponse(db.create_conversation(text_field(data, "title").strip() or DEFAULT_TITLE))

    async def get_conversation(request: Request) -> JSONResponse:
        conv_id = request.path_params["conv_id"]
        conv = conversation_or_404(conv_id)
        return JSONResponse({**conv, "running": chat.is_running(conv_id), "messages": db.list_messages(conv_id)})

    async def rename_conversation(request: Request) -> JSONResponse:
        conv_id = request.path_params["conv_id"]
        conversation_or_404(conv_id)
        title = text_field(await json_body(request), "title", required=True).strip()
        db.rename_conversation(conv_id, title)
        return JSONResponse(conversation_or_404(conv_id))

    async def delete_conversation(request: Request) -> JSONResponse:
        conv_id = request.path_params["conv_id"]
        conversation_or_404(conv_id)
        await chat.stop(conv_id)
        db.delete_conversation(conv_id)
        return JSONResponse({"ok": True})

    async def send_message(request: Request) -> StreamingResponse:
        conv_id = request.path_params["conv_id"]
        conversation_or_404(conv_id)
        data = await json_body(request)
        content = text_field(data, "content", required=True, max_len=200_000)
        model = text_field(data, "model").strip() or None
        think = data.get("think")
        if think is not None and not isinstance(think, bool):
            raise HTTPException(400, "'think' must be true or false.")
        if model and not settings.is_local and model not in settings.models:
            raise HTTPException(400, f"Unknown model '{model}'. Available: {', '.join(settings.models)}")
        if chat.is_running(conv_id):
            raise HTTPException(409, "This conversation is already generating a reply.")
        queue = chat.start(conv_id, content, model, think)

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

    async def stop_conversation(request: Request) -> JSONResponse:
        conv_id = request.path_params["conv_id"]
        conversation_or_404(conv_id)
        return JSONResponse({"stopped": await chat.stop(conv_id)})

    # ------------------------------------------------------------------ tasks

    async def list_tasks(request: Request) -> JSONResponse:
        return JSONResponse(db.list_tasks())

    async def create_task(request: Request) -> JSONResponse:
        data = await json_body(request)
        try:
            task = tasks.create_task(
                text_field(data, "title"),
                text_field(data, "goal", required=True, max_len=50_000),
                number_field(data, "delay_minutes", 0),
                number_field(data, "interval_minutes"),
                "user",
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(task)

    async def get_task(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        task = task_or_404(task_id)
        runs = db.list_runs(task_id)
        for i, run in enumerate(runs):
            # Full event logs for the most recent runs only.
            run["events"] = db.list_events(run["id"]) if i < 5 else None
        return JSONResponse({**task, "runs": runs})

    async def task_action(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        action = request.path_params["action"]
        task_or_404(task_id)
        try:
            if action == "run":
                tasks.run_now(task_id)
            elif action == "pause":
                tasks.pause(task_id)
            elif action == "resume":
                tasks.resume(task_id)
            elif action == "cancel":
                await tasks.cancel(task_id)
            else:
                raise HTTPException(404, f"Unknown action '{action}'")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse(task_or_404(task_id))

    async def delete_task(request: Request) -> JSONResponse:
        task_id = request.path_params["task_id"]
        task_or_404(task_id)
        await tasks.delete(task_id)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------ files

    async def list_files(request: Request) -> JSONResponse:
        path = request.query_params.get("path", ".")
        try:
            entries = list_entries(workspace, path)
        except ToolError as exc:
            raise HTTPException(404, str(exc)) from exc
        return JSONResponse({"path": relative(workspace, workspace_path(path)), "entries": entries})

    async def read_raw(request: Request) -> FileResponse:
        target = workspace_path(request.query_params.get("path"))
        if not target.is_file():
            raise HTTPException(404, "File not found")
        download = request.query_params.get("download", "").lower() in ("1", "true", "yes")
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

    async def upload_file(request: Request) -> JSONResponse:
        target = workspace_path(request.query_params.get("path"))
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
        return JSONResponse({"path": relative(workspace, target), "size": size})

    async def delete_file(request: Request) -> JSONResponse:
        target = workspace_path(request.query_params.get("path"))
        if target == workspace.resolve():
            raise HTTPException(400, "Refusing to delete the workspace")
        if not target.exists():
            raise HTTPException(404, "Not found")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------------ UI

    async def index(request: Request) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    @asynccontextmanager
    async def lifespan(app: Starlette):
        await tasks.start()
        try:
            yield
        finally:
            await chat.shutdown()
            await tasks.stop()
            await models.shutdown()
            db.close()

    routes = [
        Route("/", index),
        Route("/api/status", status),
        Route("/api/models", list_models),
        Route("/api/models", delete_model, methods=["DELETE"]),
        Route("/api/models/pull", pull_model, methods=["POST"]),
        Route("/api/models/active", set_active_model, methods=["POST"]),
        Route("/api/conversations", list_conversations),
        Route("/api/conversations", create_conversation, methods=["POST"]),
        Route("/api/conversations/{conv_id}", get_conversation),
        Route("/api/conversations/{conv_id}", rename_conversation, methods=["PATCH"]),
        Route("/api/conversations/{conv_id}", delete_conversation, methods=["DELETE"]),
        Route("/api/conversations/{conv_id}/messages", send_message, methods=["POST"]),
        Route("/api/conversations/{conv_id}/stop", stop_conversation, methods=["POST"]),
        Route("/api/tasks", list_tasks),
        Route("/api/tasks", create_task, methods=["POST"]),
        Route("/api/tasks/{task_id}", get_task),
        Route("/api/tasks/{task_id}", delete_task, methods=["DELETE"]),
        Route("/api/tasks/{task_id}/{action}", task_action, methods=["POST"]),
        Route("/api/files", list_files),
        Route("/api/files", delete_file, methods=["DELETE"]),
        Route("/api/files/raw", read_raw),
        Route("/api/files/raw", upload_file, methods=["PUT"]),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={HTTPException: http_error})
    app.add_middleware(RequestGuardMiddleware, allowed_hosts=settings.allowed_hosts)
    if settings.password:
        app.add_middleware(BasicAuthMiddleware, password=settings.password)
    app.state.settings = settings
    app.state.db = db
    app.state.tasks = tasks
    app.state.chat = chat
    app.state.models = models
    return app
