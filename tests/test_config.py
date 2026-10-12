from pathlib import Path

import pytest

from selfai.config import DEFAULT_BASE_URL, DEFAULT_LOCAL_MODEL, Settings

ENV_VARS = [
    "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_MODELS", "DEEPSEEK_BASE_URL", "SELFAI_PORT", "SELFAI_PROVIDER",
    "SELFAI_MODEL", "SELFAI_CONTEXT_TOKENS", "SELFAI_ALLOW_PRIVATE_NETWORK", "SELFAI_ALLOWED_HOSTS",
    "SELFAI_DATA_DIR", "SELFAI_OLLAMA_URL", "SELFAI_MAX_TOOL_RESULT_CHARS", "SELFAI_HISTORY_MAX_CHARS",
]  # fmt: skip


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_free_local_model_is_the_default(tmp_path: Path):
    s = Settings.from_env(tmp_path / "missing.env")
    assert s.provider == "ollama" and s.is_local
    assert s.model == DEFAULT_LOCAL_MODEL
    assert s.ollama_url == "http://127.0.0.1:11434"
    assert s.config_problem() is None
    # Small context: compact tool output and history.
    assert s.context_tokens == 8192
    assert s.max_tool_result_chars == 6000
    assert 8000 <= s.history_max_chars < 20000
    assert s.max_concurrent_tasks == 1
    assert s.workspace_dir == Path("data/workspace")


def test_deepseek_is_used_when_a_key_is_present(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=\nDEEPSEEK_MODEL='deepseek-reasoner'\nDEEPSEEK_API_KEY=sk-from-file\n")
    s = Settings.from_env(env)
    assert s.provider == "deepseek" and not s.is_local
    assert s.api_key == "sk-from-file"
    assert s.model == "deepseek-reasoner"
    assert s.models == ["deepseek-chat", "deepseek-reasoner"]
    assert s.max_tool_result_chars == 20000 and s.history_max_chars == 200_000


def test_env_file_parsing_and_precedence(tmp_path: Path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nSELFAI_PROVIDER=ollama\nSELFAI_MODEL=qwen3.5:2b\nexport SELFAI_PORT=9000\n"
        "SELFAI_ALLOW_PRIVATE_NETWORK=yes\nSELFAI_CONTEXT_TOKENS=16384\nSELFAI_OLLAMA_URL=http://phone:11434/\n"
    )
    monkeypatch.setenv("SELFAI_ALLOWED_HOSTS", "Example.com, localhost")
    monkeypatch.setenv("SELFAI_MODEL", "llama3.2:3b")  # real environment wins over the file
    s = Settings.from_env(env)
    assert s.model == "llama3.2:3b"
    assert s.port == 9000
    assert s.allow_private_network is True
    assert s.allowed_hosts == ["example.com", "localhost"]
    assert s.context_tokens == 16384 and s.history_max_chars == 16384 * 3 - 10_000
    assert s.ollama_url == "http://phone:11434"


def test_deepseek_without_key_is_a_config_problem(monkeypatch):
    monkeypatch.setenv("SELFAI_PROVIDER", "deepseek")
    s = Settings.from_env(None)
    assert "DEEPSEEK_API_KEY" in s.config_problem()
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:1234/v1")  # custom server: key optional
    assert Settings.from_env(None).config_problem() is None
    assert Settings.from_env(None).base_url != DEFAULT_BASE_URL


def test_unknown_provider_rejected(monkeypatch):
    monkeypatch.setenv("SELFAI_PROVIDER", "skynet")
    with pytest.raises(ValueError, match="SELFAI_PROVIDER"):
        Settings.from_env(None)
