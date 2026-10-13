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


PROVIDERS = ("ollama", "groq", "gemini", "openrouter", "deepseek")
DEFAULT_LOCAL_MODEL = "qwen3.5:4b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


@dataclass
class Settings:
    # Which AI runs the agent:
    #   "ollama"   - a free model downloaded to this device (phone or computer) via Ollama
    #   "deepseek" - the DeepSeek API (or any OpenAI-compatible API via base_url)
    provider: str = "ollama"
    model: str = DEFAULT_LOCAL_MODEL
    temperature: float | None = None

    # Ollama
    ollama_url: str = DEFAULT_OLLAMA_URL
    context_tokens: int = 8192  # context window requested from local models (uses RAM)
    keep_alive: str = "30m"  # keep the model loaded between messages
    task_thinking: bool = True  # let background tasks use "thinking" when the model supports it

    # DeepSeek / OpenAI-compatible
    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    models: list[str] = field(default_factory=lambda: ["deepseek-chat", "deepseek-reasoner"])

    # Storage
    data_dir: Path = Path("data")

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    password: str = ""
    # The public web address, if Self-AI is reachable from the internet (see setup-url.sh).
    public_url: str = ""
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

    # Always-on web version (serverless hosting such as Vercel): data lives in Supabase, and
    # background tasks are started by a scheduler calling /api/cron/tick.
    cloud: bool = False
    supabase_url: str = ""
    supabase_key: str = ""
    db_secret: str = ""  # sent with every database request; checked by the database
    cron_secret: str = ""  # proves a /api/cron/tick call comes from the scheduler
    run_time_limit: float = 0.0  # seconds per reply or task run (0 = no limit)

    # Internet
    allow_private_network: bool = False
    tavily_api_key: str = ""
    max_download_mb: int = 50
    user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Self-AI/0.2"
    )

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "selfai.db"

    @property
    def is_local(self) -> bool:
        return self.provider == "ollama"

    def config_problem(self) -> str | None:
        """A configuration error that stops the AI from running, if any."""
        if self.provider == "deepseek" and not self.api_key and self.base_url.rstrip("/") == DEFAULT_BASE_URL:
            return (
                "DEEPSEEK_API_KEY is not set. Add it to the .env file (get a key at "
                "https://platform.deepseek.com/api_keys) and restart Self-AI, or use a free local model "
                "with SELFAI_PROVIDER=ollama."
            )
        return None

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")) -> "Settings":
        if env_file is not None:
            load_dotenv(env_file)
        env = os.environ.get
        api_key = env("DEEPSEEK_API_KEY", "").strip()
        supabase_url = env("SELFAI_SUPABASE_URL", "").strip().rstrip("/")
        cloud = bool(supabase_url)
        default_provider = "deepseek" if api_key else ("groq" if cloud else "ollama")
        provider = env("SELFAI_PROVIDER", "").strip().lower() or default_provider
        if provider not in PROVIDERS:
            raise ValueError(f"SELFAI_PROVIDER must be one of {', '.join(PROVIDERS)}, got '{provider}'")
        if cloud and provider == "ollama":
            provider = default_provider  # a web server has no on-device models
        local = provider == "ollama"

        deepseek_models = [m.strip() for m in env("DEEPSEEK_MODELS", "").split(",") if m.strip()]
        deepseek_models = deepseek_models or ["deepseek-chat", "deepseek-reasoner"]
        if local:
            model = env("SELFAI_MODEL", "").strip() or DEFAULT_LOCAL_MODEL
        elif provider == "deepseek":
            model = env("SELFAI_MODEL", "").strip() or env("DEEPSEEK_MODEL", "").strip() or "deepseek-chat"
            if model not in deepseek_models:
                deepseek_models.insert(0, model)
        else:
            model = env("SELFAI_MODEL", "").strip()  # empty: the provider's default

        # Small local models have small context windows, and some free cloud plans allow few
        # tokens per minute: keep prompts and tool output compact for those.
        compact = provider in ("ollama", "groq", "openrouter")
        context_tokens = _env_int("SELFAI_CONTEXT_TOKENS", 8192 if local else 64_000)
        if local:
            default_history = max(8_000, context_tokens * 3 - 10_000)
        else:
            default_history = 16_000 if compact else 200_000
        temperature = env("SELFAI_TEMPERATURE", "").strip() or env("DEEPSEEK_TEMPERATURE", "").strip()
        return cls(
            provider=provider,
            model=model,
            temperature=float(temperature) if temperature else None,
            ollama_url=(env("SELFAI_OLLAMA_URL", "").strip() or DEFAULT_OLLAMA_URL).rstrip("/"),
            context_tokens=context_tokens,
            keep_alive=env("SELFAI_KEEP_ALIVE", "").strip() or "30m",
            task_thinking=_env_bool("SELFAI_TASK_THINKING", True),
            api_key=api_key,
            base_url=(env("DEEPSEEK_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/"),
            models=deepseek_models,
            # Serverless hosts only allow writing to /tmp (the real data is in Supabase).
            data_dir=Path(env("SELFAI_DATA_DIR", "").strip() or ("/tmp/selfai" if cloud else "data")),
            host=env("SELFAI_HOST", "").strip() or "127.0.0.1",
            port=_env_int("SELFAI_PORT", 8000),
            password=env("SELFAI_PASSWORD", ""),
            public_url=env("SELFAI_PUBLIC_URL", "").strip().rstrip("/"),
            allowed_hosts=[
                h.strip().lower()
                for h in (env("SELFAI_ALLOWED_HOSTS", "").strip() or "localhost,127.0.0.1,::1").split(",")
                if h.strip()
            ],
            chat_max_steps=_env_int("SELFAI_CHAT_MAX_STEPS", 15 if compact else 25),
            task_max_steps=_env_int("SELFAI_TASK_MAX_STEPS", 30 if compact else 50),
            tool_timeout_seconds=_env_int("SELFAI_TOOL_TIMEOUT", 60 if cloud else 120),
            max_tool_result_chars=_env_int("SELFAI_MAX_TOOL_RESULT_CHARS", 6_000 if compact else 20_000),
            history_max_chars=_env_int("SELFAI_HISTORY_MAX_CHARS", default_history),
            max_concurrent_tasks=max(1, _env_int("SELFAI_MAX_CONCURRENT_TASKS", 1 if local else 2)),
            max_active_tasks=max(1, _env_int("SELFAI_MAX_ACTIVE_TASKS", 50)),
            scheduler_poll_seconds=_env_float("SELFAI_SCHEDULER_POLL_SECONDS", 5.0),
            allow_private_network=_env_bool("SELFAI_ALLOW_PRIVATE_NETWORK", False),
            tavily_api_key=env("TAVILY_API_KEY", "").strip(),
            # Serverless requests carry at most ~4.5 MB.
            max_download_mb=_env_int("SELFAI_MAX_DOWNLOAD_MB", 4 if cloud else 50),
            cloud=cloud,
            supabase_url=supabase_url,
            supabase_key=env("SELFAI_SUPABASE_KEY", "").strip(),
            db_secret=env("SELFAI_DB_SECRET", "").strip(),
            cron_secret=env("SELFAI_CRON_SECRET", "").strip(),
            # Serverless functions are stopped after 300 s: finish replies and task runs before that.
            run_time_limit=_env_float("SELFAI_RUN_TIME_LIMIT", 240.0 if cloud else 0.0),
        )
