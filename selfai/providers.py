"""Where the AI runs: on this device (Ollama), or a cloud API. Cloud options include free,
fast ones (Groq, Google Gemini, OpenRouter) and the paid DeepSeek API.

The choice and API keys are saved in the database, so they can be changed in the app
without editing files or restarting."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace

import httpx

from .config import DEFAULT_BASE_URL, DEFAULT_LOCAL_MODEL, Settings
from .db import Database
from .errors import LLMError, api_error_message

LOCAL = "ollama"


@dataclass(frozen=True)
class CloudProvider:
    id: str
    name: str
    base_url: str
    default_model: str | None
    key_url: str
    env_key: str
    free: bool
    description: str
    prefer: str = ""  # part of a good model's name, used when default_model isn't offered
    large_budget: bool = False  # generous rate limits: send longer history and tool output


CLOUD_PROVIDERS: dict[str, CloudProvider] = {
    "groq": CloudProvider(
        id="groq",
        name="Groq",
        base_url="https://api.groq.com/openai/v1",
        default_model="openai/gpt-oss-120b",
        key_url="https://console.groq.com/keys",
        env_key="GROQ_API_KEY",
        free=True,
        description="Free and very fast. The free plan has a daily limit; switch to Gemini if you reach it.",
        prefer="gpt-oss",
    ),
    "gemini": CloudProvider(
        id="gemini",
        name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        default_model="gemini-2.5-flash",
        key_url="https://aistudio.google.com/apikey",
        env_key="GEMINI_API_KEY",
        free=True,
        description="Free and fast, with a daily limit. Google may use free-plan chats to improve its products.",
        prefer="flash",
        large_budget=True,
    ),
    "openrouter": CloudProvider(
        id="openrouter",
        name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        default_model=None,
        key_url="https://openrouter.ai/keys",
        env_key="OPENROUTER_API_KEY",
        free=True,
        description="Many free models from different companies, but only about 50 requests a day.",
        prefer=":free",
    ),
    "deepseek": CloudProvider(
        id="deepseek",
        name="DeepSeek",
        base_url=DEFAULT_BASE_URL,
        default_model="deepseek-chat",
        key_url="https://platform.deepseek.com/api_keys",
        env_key="DEEPSEEK_API_KEY",
        free=False,
        description="Paid per use (cheap). Strong, fast, no daily limit.",
        large_budget=True,
    ),
}

# Model names that aren't chat models (speech, embeddings, safety filters, images...).
NOT_CHAT = ("whisper", "tts", "embed", "guard", "orpheus", "image", "imagen", "veo", "aqa", "transcribe", "audio")


def active_provider(db: Database, settings: Settings) -> str:
    chosen = db.get_setting("provider")
    if (chosen == LOCAL and not settings.cloud) or chosen in CLOUD_PROVIDERS:
        return chosen
    return settings.provider


def cloud_provider(pid: str, settings: Settings) -> CloudProvider:
    provider = CLOUD_PROVIDERS[pid]
    if pid == "deepseek":
        provider = replace(provider, base_url=settings.base_url)
    return provider


def api_key(db: Database, settings: Settings, pid: str) -> str:
    saved = db.get_setting(f"key:{pid}")
    if saved:
        return saved
    if pid == "deepseek":
        return settings.api_key
    return os.environ.get(CLOUD_PROVIDERS[pid].env_key, "").strip()


def local_model(db: Database, settings: Settings) -> str:
    """The model to use on this device (chosen in Models, else the configured default)."""
    return db.get_setting("model") or (settings.model if settings.provider == LOCAL else DEFAULT_LOCAL_MODEL)


def active_model(db: Database, settings: Settings) -> str:
    """The model chosen in the app for the active provider, else that provider's default."""
    pid = active_provider(db, settings)
    if pid == LOCAL:
        return local_model(db, settings)
    chosen = db.get_setting(f"model:{pid}")
    if chosen:
        return chosen
    if pid == settings.provider and settings.model:
        return settings.model
    return CLOUD_PROVIDERS[pid].default_model or ""


def config_problem(db: Database, settings: Settings) -> str | None:
    """Why the AI can't run with the current choice, if anything."""
    pid = active_provider(db, settings)
    if pid == LOCAL or api_key(db, settings, pid):
        return None
    provider = cloud_provider(pid, settings)
    if pid == "deepseek" and provider.base_url.rstrip("/") != DEFAULT_BASE_URL:
        return None  # a custom OpenAI-compatible server may not need a key
    return f"Add your {'free ' if provider.free else ''}{provider.name} key in Models to use it."


def run_settings(db: Database, settings: Settings) -> Settings:
    """Settings for one chat reply or task run, with prompt budgets that fit the active AI.
    Small local models and tight free plans get compact history and tool output."""
    pid = active_provider(db, settings)
    if pid == settings.provider:
        return settings  # budgets were already chosen for this provider at startup
    if pid == LOCAL:
        history, tool_output, chat_steps, task_steps = max(8_000, settings.context_tokens * 3 - 10_000), 6_000, 15, 30
    elif CLOUD_PROVIDERS[pid].large_budget:
        history, tool_output, chat_steps, task_steps = 200_000, 20_000, 25, 50
    else:
        history, tool_output, chat_steps, task_steps = 16_000, 6_000, 15, 30
    return replace(
        settings,
        history_max_chars=history,
        max_tool_result_chars=tool_output,
        chat_max_steps=chat_steps,
        task_max_steps=task_steps,
    )


def pick_model(models: list[str], provider: CloudProvider) -> str:
    if provider.default_model in models:
        return provider.default_model  # type: ignore[return-value]
    preferred = [m for m in models if provider.prefer and provider.prefer in m]
    return (preferred or models or [provider.default_model or ""])[0]


class CloudModels:
    """Lists the chat models a cloud provider offers (cached for a few minutes)."""

    TTL = 600.0

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport
        self._cache: dict[tuple[str, str], tuple[float, list[str]]] = {}

    async def list(self, provider: CloudProvider, key: str, fresh: bool = False) -> list[str]:
        cache_key = (provider.id, key)
        hit = self._cache.get(cache_key)
        if hit and not fresh and time.monotonic() - hit[0] < self.TTL:
            return hit[1]
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with httpx.AsyncClient(base_url=provider.base_url, timeout=15.0, transport=self._transport) as client:
                resp = await client.get("/models", headers=headers)
        except httpx.HTTPError as exc:
            raise LLMError(f"Could not reach {provider.name}. Check your internet connection.") from exc
        if resp.status_code >= 400:
            try:
                detail = resp.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                detail = resp.text[:300]
            raise LLMError(api_error_message(resp.status_code, detail, provider.name), resp.status_code)
        models = []
        for item in resp.json().get("data") or []:
            model_id = str(item.get("id") or "").removeprefix("models/")
            if not model_id or any(word in model_id.lower() for word in NOT_CHAT):
                continue
            if provider.id == "openrouter" and (
                not model_id.endswith(":free") or "tools" not in (item.get("supported_parameters") or [])
            ):
                continue  # only free models that can use tools
            models.append(model_id)
        models = sorted(set(models))
        self._cache[cache_key] = (time.monotonic(), models)
        return models
