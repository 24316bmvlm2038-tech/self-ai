"""Runtime settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"


def load_dotenv(path: Path) -> None:
    """Minimal .env loader: KEY=VALUE lines. Real environment variables take precedence;
    within the file, a later line overrides an earlier one."""
    if not path.is_file():
        return
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    return float(value) if value else default


@dataclass
class Settings:
    # DeepSeek (or any OpenAI-compatible endpoint, e.g. a local Ollama server)
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = "deepseek-chat"
    models: list[str] = field(default_factory=lambda: ["deepseek-chat", "deepseek-reasoner"])
    temperature: float | None = None

    # Storage
    data_dir: Path = Path("data")

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    password: str = ""
    # Host names the UI may be reached under (guards against DNS rebinding). "*" allows any.
    allowed_hosts: list[str] = field(default_factory=lambda: ["localhost", "127.0.0.1", "::1"])

    # Agent limits
    chat_max_steps: int = 25
    task_max_steps: int = 50
    tool_timeout_seconds: int = 120
    max_tool_result_chars: int = 20_000
    history_max_chars: int = 200_000

    # Background tasks
    max_concurrent_tasks: int = 2
    max_active_tasks: int = 50
    scheduler_poll_seconds: float = 5.0

    # Internet
    allow_private_network: bool = False
    tavily_api_key: str = ""
    max_download_mb: int = 50
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Self-AI/0.1"
    )

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "selfai.db"

    @property
    def api_key_configured(self) -> bool:
        return bool(self.api_key) or self.base_url.rstrip("/") != DEFAULT_BASE_URL

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file)
        model = os.environ.get("DEEPSEEK_MODEL", "").strip() or "deepseek-chat"
        models = [m.strip() for m in os.environ.get("DEEPSEEK_MODELS", "").split(",") if m.strip()]
        if not models:
            models = ["deepseek-chat", "deepseek-reasoner"]
        if model not in models:
            models.insert(0, model)
        temperature = os.environ.get("DEEPSEEK_TEMPERATURE", "").strip()
        return cls(
            api_key=os.environ.get("DEEPSEEK_API_KEY", "").strip(),
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL,
            model=model,
            models=models,
            temperature=float(temperature) if temperature else None,
            data_dir=Path(os.environ.get("SELFAI_DATA_DIR", "").strip() or "data"),
            host=os.environ.get("SELFAI_HOST", "").strip() or "127.0.0.1",
            port=_env_int("SELFAI_PORT", 8000),
            password=os.environ.get("SELFAI_PASSWORD", ""),
            allowed_hosts=[
                h.strip().lower()
                for h in (os.environ.get("SELFAI_ALLOWED_HOSTS", "").strip() or "localhost,127.0.0.1,::1").split(",")
                if h.strip()
            ],
            chat_max_steps=_env_int("SELFAI_CHAT_MAX_STEPS", 25),
            task_max_steps=_env_int("SELFAI_TASK_MAX_STEPS", 50),
            tool_timeout_seconds=_env_int("SELFAI_TOOL_TIMEOUT", 120),
            max_concurrent_tasks=max(1, _env_int("SELFAI_MAX_CONCURRENT_TASKS", 2)),
            max_active_tasks=max(1, _env_int("SELFAI_MAX_ACTIVE_TASKS", 50)),
            scheduler_poll_seconds=_env_float("SELFAI_SCHEDULER_POLL_SECONDS", 5.0),
            allow_private_network=_env_bool("SELFAI_ALLOW_PRIVATE_NETWORK", False),
            tavily_api_key=os.environ.get("TAVILY_API_KEY", "").strip(),
            max_download_mb=_env_int("SELFAI_MAX_DOWNLOAD_MB", 50),
        )
