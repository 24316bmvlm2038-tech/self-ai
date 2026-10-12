import asyncio

import pytest

from selfai.db import Database
from selfai.models import RECOMMENDED_MODELS, ModelManager, active_model, suggest_model

from .conftest import FakeOllama, run


@pytest.mark.parametrize(
    "ram,android,expected",
    [
        (3.6, True, "qwen3.5:0.8b"),  # "4 GB" phone
        (5.5, True, "qwen3.5:2b"),  # "6 GB" phone
        (7.3, True, "qwen3.5:4b"),  # "8 GB" phone
        (15.5, True, "qwen3.5:4b"),  # phone CPUs are too slow for 9B
        (15.5, False, "qwen3.5:9b"),  # "16 GB" computer
        (7.8, False, "qwen3.5:4b"),
        (None, False, "qwen3.5:4b"),
    ],
)
def test_suggest_model(ram, android, expected):
    assert suggest_model(ram, android) == expected


def test_recommended_models_are_complete():
    names = [m["name"] for m in RECOMMENDED_MODELS]
    assert len(names) == len(set(names))
    for m in RECOMMENDED_MODELS:
        assert m["title"] and m["description"] and m["size_gb"] > 0 and m["min_ram_gb"] > 0
    assert suggest_model(8, True) in names


def make(settings, fake):
    db = Database(settings.db_path)
    return db, ModelManager(fake.client(), db, settings)


async def wait_done(manager, name):
    for _ in range(200):
        if manager.pulls.get(name, {}).get("done"):
            return manager.pulls[name]
        await asyncio.sleep(0.01)
    raise AssertionError("pull did not finish")


def test_first_download_becomes_active(settings):
    fake = FakeOllama(models=())
    db, manager = make(settings, fake)

    async def scenario():
        manager.start_pull("qwen3.5:2b")
        return await wait_done(manager, "qwen3.5:2b")

    progress = run(scenario())
    assert progress == {"status": "success", "completed": 100, "total": 100, "error": None, "done": True}
    assert "qwen3.5:2b" in fake.models
    assert active_model(db, settings) == "qwen3.5:2b"


def test_extra_download_keeps_working_model_active(settings):
    fake = FakeOllama(models=("qwen3.5:4b",))
    db, manager = make(settings, fake)

    async def scenario():
        manager.start_pull("gemma4:e2b")
        await wait_done(manager, "gemma4:e2b")

    run(scenario())
    assert active_model(db, settings) == "qwen3.5:4b"


def test_failed_download_reports_error(settings):
    _, manager = make(settings, FakeOllama())

    async def scenario():
        manager.start_pull("bad-model:1b")
        return await wait_done(manager, "bad-model:1b")

    progress = run(scenario())
    assert progress["status"] == "failed" and "file does not exist" in progress["error"]


def test_invalid_names_rejected(settings):
    _, manager = make(settings, FakeOllama())
    for name in ("", "../etc", "a b", "x" * 300):
        with pytest.raises(ValueError):
            manager.start_pull(name)


def test_resolve_active_falls_back_to_installed_model(settings):
    fake = FakeOllama(models=("llama3.2:3b",))
    db, manager = make(settings, fake)
    current, names = run(manager.resolve_active())
    assert current == "llama3.2:3b" and names == ["llama3.2:3b"]
    assert db.get_setting("model") == "llama3.2:3b"


def test_delete_active_model_clears_choice(settings):
    fake = FakeOllama(models=("qwen3.5:4b", "qwen3.5:2b"))
    db, manager = make(settings, fake)
    db.set_setting("model", "qwen3.5:2b")
    run(manager.delete("qwen3.5:2b"))
    assert "qwen3.5:2b" not in fake.models
    assert db.get_setting("model") is None
    assert active_model(db, settings) == "qwen3.5:4b"
    run(manager.delete("not-there:1b"))  # already gone: not an error


def test_active_model_for_deepseek_must_be_offered(settings):
    settings.provider = "deepseek"
    settings.model = "deepseek-chat"
    settings.models = ["deepseek-chat", "deepseek-reasoner"]
    db = Database(settings.db_path)
    db.set_setting("model", "qwen3.5:4b")  # left over from local mode
    assert active_model(db, settings) == "deepseek-chat"
    db.set_setting("model", "deepseek-reasoner")
    assert active_model(db, settings) == "deepseek-reasoner"
