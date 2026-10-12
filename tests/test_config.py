from pathlib import Path

from selfai.config import DEFAULT_BASE_URL, Settings

ENV_VARS = [
    "DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_MODELS", "DEEPSEEK_BASE_URL", "SELFAI_PORT",
    "SELFAI_ALLOW_PRIVATE_NETWORK", "SELFAI_ALLOWED_HOSTS", "SELFAI_DATA_DIR",
]  # fmt: skip


def clear_env(monkeypatch):
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_env_file_later_lines_win_but_real_env_wins_over_file(tmp_path: Path, monkeypatch):
    clear_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nDEEPSEEK_API_KEY=\nDEEPSEEK_MODEL='deepseek-reasoner'\nexport SELFAI_PORT=9000\n"
        "SELFAI_ALLOW_PRIVATE_NETWORK=yes\nDEEPSEEK_API_KEY=sk-from-file\n"
    )
    monkeypatch.setenv("SELFAI_ALLOWED_HOSTS", "Example.com, localhost")
    s = Settings.from_env(env)
    assert s.api_key == "sk-from-file"
    assert s.model == "deepseek-reasoner"
    assert s.models == ["deepseek-chat", "deepseek-reasoner"]
    assert s.port == 9000
    assert s.allow_private_network is True
    assert s.allowed_hosts == ["example.com", "localhost"]
    assert s.api_key_configured

    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-real-env")
    assert Settings.from_env(env).api_key == "sk-real-env"


def test_defaults_and_custom_endpoint(tmp_path: Path, monkeypatch):
    clear_env(monkeypatch)
    s = Settings.from_env(tmp_path / "missing.env")
    assert s.base_url == DEFAULT_BASE_URL and s.model == "deepseek-chat"
    assert not s.api_key_configured
    assert s.workspace_dir == Path("data/workspace")

    # Local OpenAI-compatible servers (e.g. Ollama) don't need a key.
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("DEEPSEEK_MODEL", "my-local-model")
    s = Settings.from_env(None)
    assert s.api_key_configured
    assert s.models[0] == "my-local-model"
