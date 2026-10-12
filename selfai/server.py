"""HTTP API + web UI. Built on Starlette (pure Python), so it installs on phones too."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import __version__
from .agent import Agent
from .auth import AuthMiddleware, login_endpoint
from .chat import DEFAULT_TITLE, ChatService
from .config import Settings
from .db import PENDING, RUNNING, Database
from .errors import LLMError
from .llm import LLM, RoutingLLM
from .models import RECOMMENDED_MODELS, ModelManager, device_ram_gb, is_android, suggest_model
from .ollama import OllamaClient, normalize_model_name
from .providers import (
    CLOUD_PROVIDERS,
    LOCAL,
    CloudModels,
    active_model,
    active_provider,
    api_key,
    cloud_provider,
    config_problem,
    local_model,
    pick_model,
)
from .tasks import TaskManager
from .tools import ToolError, build_registry
from .tools.files import list_entries, relative, resolve_path

STATIC_DIR = Path(__file__).parent / "static"
KEEPALIVE_SECONDS = 10.0
mimetypes.add_type("application/manifest+json", ".webmanifest")


class RequestGuardMiddleware:
    """Protects a local agent from other websites:
    - Host allowlist: blocks DNS-rebinding (evil.com resolving to 127.0.0.1). Not needed when a
      password is set (the rebinding page has no login), so a public web address just works.
    - Writes to /api/ need an `X-SelfAI` header, which cross-site pages can't send
      without a CORS preflight that this server never approves (blocks CSRF)."""

    SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

    def __init__(self, app, allowed_hosts: list[str], check_host: bool = True):
        self.app = app
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.check_host = check_host

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if self.check_host and "*" not in self.allowed_hosts:
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
    settings: Settings | None = None,
    llm: LLM | None = None,
    ollama: OllamaClient | None = None,
    cloud_transport: httpx.AsyncBaseTransport | None = None,
) -> Starlette:
    settings = settings or Settings.from_env()
    settings.ensure_dirs()
    db = Database(settings.db_path)
    ollama = ollama or OllamaClient(settings.ollama_url, log_path=settings.data_dir / "ollama.log")
    agent = Agent(llm or RoutingLLM(settings, db, ollama, cloud_transport), build_registry(), settings)
    cloud_models = CloudModels(cloud_transport)
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

    # ------------------------------------------------------------------ status & models

    def provider_name(pid: str) -> str:
        return "On this device" if pid == LOCAL else cloud_provider(pid, settings).name

    async def status(request: Request) -> JSONResponse:
        pid = active_provider(db, settings)
        problem, code = config_problem(db, settings), "config"
        model = active_model(db, settings)
        names: list[str] = []
        capabilities = None
        if pid == LOCAL:
            await ollama.ensure_running()
            try:
                model, names = await models.resolve_active()
            except LLMError as exc:
                problem, code = str(exc), "ollama_down"
            else:
                if not names:
                    problem, code = "No AI model is downloaded yet. Open Models to download a free one.", "no_models"
                else:
                    caps = await ollama.capabilities(model)
                    capabilities = sorted(caps) if caps is not None else None
        elif not problem:
            provider = cloud_provider(pid, settings)
            if pid == "deepseek":
                names = settings.models
            else:
                try:
                    names = await cloud_models.list(provider, api_key(db, settings, pid))
                except LLMError as exc:
                    problem, code = str(exc), "cloud_error"
                if names and model not in names:
                    model = pick_model(names, provider)  # e.g. the default model was retired
                    db.set_setting(f"model:{pid}", model)
        return JSONResponse(
            {
                "version": __version__,
                "provider": pid,
                "provider_name": provider_name(pid),
                "model": model,
                "models": names or ([model] if model and pid != LOCAL else []),
                "capabilities": capabilities,
                "ready": problem is None,
                "problem": problem,
                "problem_code": code if problem else None,
                "public_url": settings.public_url or None,
                "search_provider": "tavily" if settings.tavily_api_key else "duckduckgo",
                "workspace": str(workspace.resolve()),
                "tasks": {"running": len(db.list_tasks(RUNNING)), "pending": len(db.list_tasks(PENDING))},
            }
        )

    def providers_list() -> list[dict]:
        pid = active_provider(db, settings)
        items = [
            {
                "id": LOCAL,
                "name": "On this device",
                "free": True,
                "private": True,
                "description": "Free and private: your chats never leave the device, and it works offline. "
                "Slower on phones.",
                "has_key": True,
                "active": pid == LOCAL,
            }
        ]
        for provider_id in CLOUD_PROVIDERS:
            provider = cloud_provider(provider_id, settings)
            items.append(
                {
                    "id": provider.id,
                    "name": provider.name,
                    "free": provider.free,
                    "private": False,
                    "description": provider.description,
                    "key_url": provider.key_url,
                    "has_key": bool(api_key(db, settings, provider.id)),
                    "active": pid == provider.id,
                }
            )
        return items

    async def set_provider(request: Request) -> JSONResponse:
        data = await json_body(request)
        pid = text_field(data, "provider", required=True).strip()
        if pid != LOCAL and pid not in CLOUD_PROVIDERS:
            raise HTTPException(400, f"Unknown AI provider '{pid}'.")
        key = text_field(data, "api_key", max_len=500).strip()
        if pid != LOCAL:
            provider = cloud_provider(pid, settings)
            if key:
                try:
                    names = await cloud_models.list(provider, key, fresh=True)
                except LLMError as exc:
                    raise HTTPException(400, f"That key didn't work. {exc}") from exc
                db.set_setting(f"key:{pid}", key)
                if names and not db.get_setting(f"model:{pid}"):
                    db.set_setting(f"model:{pid}", pick_model(names, provider))
            elif not api_key(db, settings, pid):
                raise HTTPException(400, f"Paste your {provider.name} key first.")
        db.set_setting("provider", pid)
        return JSONResponse({"active": pid, "providers": providers_list()})

    async def list_models(request: Request) -> JSONResponse:
        ram = device_ram_gb()
        android = is_android()
        result: dict = {
            "provider": active_provider(db, settings),
            "providers": providers_list(),
            "active": local_model(db, settings),
            "installed": [],
            "recommended": [],
            "pulls": models.pulls,
            "ram_gb": ram,
            "android": android,
            "suggested": suggest_model(ram, android),
            "error": None,
        }
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
        name = text_field(await json_body(request), "name", required=True)
        try:
            models.start_pull(name)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return JSONResponse({"ok": True}, status_code=202)

    async def delete_model(request: Request) -> JSONResponse:
        name = request.query_params.get("name", "")
        try:
            await models.delete(name)
        except LLMError as exc:
            raise HTTPException(502, str(exc)) from exc
        return JSONResponse({"ok": True})

    async def set_active_model(request: Request) -> JSONResponse:
        name = text_field(await json_body(request), "name", required=True).strip()
        pid = active_provider(db, settings)
        if pid == "deepseek" and name not in settings.models:
            raise HTTPException(400, f"Unknown model '{name}'. Available: {', '.join(settings.models)}")
        db.set_setting("model" if pid == LOCAL else f"model:{pid}", name)
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
        if model and active_provider(db, settings) == "deepseek" and model not in settings.models:
            raise HTTPException(400, f"Unknown model '{model}'. Available: {', '.join(settings.models)}")
        if chat.is_running(conv_id):
            raise HTTPException(409, "This conversation is already generating a reply.")
        queue = chat.start(conv_id, content, model, think)

        async def events():
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    # A phone can take a minute to read the prompt before the first word;
                    # keep the connection visibly alive meanwhile.
                    yield ": keep-alive\n\n"
                    continue
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

    async def service_worker(request: Request) -> FileResponse:
        # Served from the root so it can make the whole app work offline.
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript", headers={"Cache-Control": "no-cache"})

    @asynccontextmanager
    async def lifespan(app: Starlette):
        starting = asyncio.create_task(ollama.ensure_running()) if active_provider(db, settings) == LOCAL else None
        await tasks.start()
        try:
            yield
        finally:
            if starting:
                starting.cancel()
            await chat.shutdown()
            await tasks.stop()
            await models.shutdown()
            db.close()

    routes = [
        Route("/", index),
        Route("/sw.js", service_worker),
        Route("/login", login_endpoint(settings.password), methods=["GET", "POST"]),
        Route("/api/providers/active", set_provider, methods=["POST"]),
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
    app.add_middleware(RequestGuardMiddleware, allowed_hosts=settings.allowed_hosts, check_host=not settings.password)
    app.add_middleware(AuthMiddleware, password=settings.password)
    app.state.settings = settings
    app.state.db = db
    app.state.tasks = tasks
    app.state.chat = chat
    app.state.models = models
    return app
