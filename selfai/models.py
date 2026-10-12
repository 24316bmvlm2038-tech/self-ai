"""Which model to use, the catalog of recommended free models, and downloading them via Ollama."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path

from .config import Settings
from .db import Database
from .errors import LLMError, describe_error
from .ollama import OllamaClient, normalize_model_name

log = logging.getLogger("selfai.models")

# Free, open models that support tool calling (needed for web access, files and tasks).
# Sizes are approximate Ollama download sizes; the Models panel shows exact sizes once installed.
RECOMMENDED_MODELS = [
    {
        "name": "qwen3.5:4b",
        "title": "Qwen 3.5 · 4B",
        "size_gb": 3.4,
        "min_ram_gb": 8,
        "description": "Best pick for most phones with 8 GB+ RAM. Strong reasoning, reliable tool use, can think step by step.",
    },
    {
        "name": "qwen3.5:2b",
        "title": "Qwen 3.5 · 2B",
        "size_gb": 2.7,
        "min_ram_gb": 6,
        "description": "Faster and lighter. Good for phones with 6 GB RAM.",
    },
    {
        "name": "qwen3.5:0.8b",
        "title": "Qwen 3.5 · 0.8B",
        "size_gb": 1.0,
        "min_ram_gb": 4,
        "description": "Tiny and quick, for older phones. Handles simple questions and tasks.",
    },
    {
        "name": "qwen3.5:9b",
        "title": "Qwen 3.5 · 9B",
        "size_gb": 6.6,
        "min_ram_gb": 16,
        "description": "Much smarter, for computers or phones with 16 GB RAM. Slower on phones.",
    },
    {
        "name": "gemma4:e2b",
        "title": "Gemma 4 · E2B (Google)",
        "size_gb": 7.2,
        "min_ram_gb": 12,
        "description": "Google's open model. Understands images and audio as well as text.",
    },
]

MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


def is_android() -> bool:
    return "com.termux" in os.environ.get("PREFIX", "") or Path("/system/build.prop").exists()


def device_ram_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    try:
        return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3, 1)
    except (ValueError, OSError, AttributeError):
        return None


def suggest_model(ram_gb: float | None, android: bool) -> str:
    """The best recommended model this device can run comfortably. Devices report less RAM
    than advertised (an "8 GB" phone shows about 7.3 GB), hence the lower thresholds."""
    if ram_gb is None:
        return "qwen3.5:4b"
    if ram_gb < 4.5:
        return "qwen3.5:0.8b"
    if ram_gb < 6.8:
        return "qwen3.5:2b"
    if android or ram_gb < 14:
        return "qwen3.5:4b"  # bigger models are too slow on phone CPUs
    return "qwen3.5:9b"


def active_model(db: Database, settings: Settings) -> str:
    """The model chosen in the UI, else the configured default."""
    chosen = db.get_setting("model")
    if chosen and (settings.is_local or chosen in settings.models):
        return chosen
    return settings.model


class ModelManager:
    """Lists, downloads and removes local models, tracking download progress for the UI."""

    def __init__(self, client: OllamaClient, db: Database, settings: Settings):
        self.client = client
        self.db = db
        self.settings = settings
        self.pulls: dict[str, dict] = {}
        self._jobs: dict[str, asyncio.Task] = {}

    async def installed(self) -> list[dict]:
        return await self.client.list_models()

    async def resolve_active(self) -> tuple[str, list[str]]:
        """The active model and installed model names. If the active model isn't installed but
        others are, switch to one of those so the AI keeps working."""
        names = [m["name"] for m in await self.installed()]
        current = active_model(self.db, self.settings)
        if names and normalize_model_name(current) not in {normalize_model_name(n) for n in names}:
            current = names[0]
            self.db.set_setting("model", current)
        return current, names

    def start_pull(self, name: str) -> None:
        name = name.strip()
        if not MODEL_NAME.match(name):
            raise ValueError(f"'{name}' is not a valid model name.")
        job = self._jobs.get(name)
        if job and not job.done():
            return
        self.pulls[name] = {"status": "starting", "completed": 0, "total": 0, "error": None, "done": False}
        self._jobs[name] = asyncio.create_task(self._pull(name), name=f"selfai-pull-{name}")

    async def _pull(self, name: str) -> None:
        progress = self.pulls[name]
        try:
            async for event in self.client.pull(name):
                progress["status"] = event.get("status", progress["status"])
                if event.get("total"):
                    progress["total"] = event["total"]
                    progress["completed"] = event.get("completed", 0)
            progress.update(status="success", done=True)
            self.client.forget(name)
            # If the active model isn't installed (e.g. the very first download), use this one.
            installed = {normalize_model_name(m["name"]) for m in await self.installed()}
            if normalize_model_name(active_model(self.db, self.settings)) not in installed:
                self.db.set_setting("model", name)
            log.info("Downloaded model %s", name)
        except asyncio.CancelledError:
            progress.update(status="cancelled", done=True)
            raise
        except Exception as exc:  # noqa: BLE001 - shown in the UI
            progress.update(status="failed", error=describe_error(exc), done=True)
            log.warning("Download of %s failed: %s", name, exc)

    async def delete(self, name: str) -> None:
        job = self._jobs.pop(name, None)
        if job and not job.done():
            job.cancel()
        self.pulls.pop(name, None)
        try:
            await self.client.delete(name)
        except LLMError as exc:
            if exc.status != 404:
                raise
        if normalize_model_name(active_model(self.db, self.settings)) == normalize_model_name(name):
            self.db.delete_setting("model")

    async def shutdown(self) -> None:
        jobs = [j for j in self._jobs.values() if not j.done()]
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)
