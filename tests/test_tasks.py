import asyncio
import time

import pytest

from selfai import db as store
from selfai.agent import Agent
from selfai.db import Database
from selfai.tasks import TaskManager
from selfai.tools import ToolContext, build_registry

from .conftest import FakeLLM, ToolCall, run


def make_manager(settings, llm):
    db = Database(settings.db_path)
    return TaskManager(db, Agent(llm, build_registry(), settings), settings)


async def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not reached")


def test_task_runs_autonomously_and_records_its_work(settings):
    llm = FakeLLM([[ToolCall("write_file", path="tasks/report.md", content="# Report")], "Wrote tasks/report.md."])
    manager = make_manager(settings, llm)

    async def scenario():
        await manager.start()
        task = manager.create_task("Report", "Write a report")
        await wait_for(lambda: manager.db.get_task(task["id"])["status"] == store.COMPLETED)
        await manager.stop()
        return task

    task = run(scenario())
    done = manager.db.get_task(task["id"])
    assert done["run_count"] == 1 and done["last_run_status"] == store.COMPLETED
    assert done["last_result"] == "Wrote tasks/report.md."
    assert (settings.workspace_dir / "tasks/report.md").read_text() == "# Report"

    [run_row] = manager.db.list_runs(task["id"])
    assert run_row["status"] == store.COMPLETED and run_row["finished_at"]
    events = [e["type"] for e in manager.db.list_events(run_row["id"])]
    assert events == ["tool_call", "tool_result", "final"]

    # The background agent got the autonomous system prompt and the goal.
    first = llm.calls[0]["messages"]
    assert "BACKGROUND TASK" in first[0]["content"]
    assert "Write a report" in first[1]["content"]


def test_recurring_task_reschedules_with_previous_result(settings):
    answers = iter(["first result", "second result"])
    llm = FakeLLM(responder=lambda messages, tools: next(answers))
    manager = make_manager(settings, llm)

    async def scenario():
        await manager.start()
        task = manager.create_task("Monitor", "Check the thing", interval_minutes=1)
        await wait_for(lambda: manager.db.get_task(task["id"])["run_count"] == 1)
        t = manager.db.get_task(task["id"])
        assert t["status"] == store.PENDING
        assert 50 < t["next_run_at"] - time.time() <= 60
        manager.run_now(task["id"])
        await wait_for(lambda: manager.db.get_task(task["id"])["run_count"] == 2)
        await manager.stop()
        return task

    task = run(scenario())
    assert manager.db.get_task(task["id"])["last_result"] == "second result"
    second_prompt = llm.calls[1]["messages"][1]["content"]
    assert "run #2" in second_prompt and "first result" in second_prompt


def test_delayed_task_waits(settings):
    manager = make_manager(settings, FakeLLM(["never"]))

    async def scenario():
        await manager.start()
        task = manager.create_task("Later", "Do it later", delay_minutes=30)
        await asyncio.sleep(0.2)
        await manager.stop()
        return task

    task = run(scenario())
    assert manager.db.get_task(task["id"])["status"] == store.PENDING
    assert manager.db.list_runs(task["id"]) == []


def test_cancel_running_task(settings):
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(30)
        return "never"

    manager = make_manager(settings, FakeLLM(responder=lambda m, t: slow()))

    async def scenario():
        await manager.start()
        task = manager.create_task("Slow", "Take forever")
        await asyncio.wait_for(started.wait(), 5)
        await manager.cancel(task["id"])
        await manager.stop()
        return task

    task = run(scenario())
    assert manager.db.get_task(task["id"])["status"] == store.CANCELLED
    assert manager.db.list_runs(task["id"])[0]["status"] == store.CANCELLED


def test_shutdown_requeues_running_task(settings):
    started = asyncio.Event()

    async def slow():
        started.set()
        await asyncio.sleep(30)

    manager = make_manager(settings, FakeLLM(responder=lambda m, t: slow()))

    async def scenario():
        await manager.start()
        task = manager.create_task("Slow", "Take forever")
        await asyncio.wait_for(started.wait(), 5)
        await manager.stop()
        return task

    task = run(scenario())
    assert manager.db.get_task(task["id"])["status"] == store.PENDING


def test_crash_recovery_requeues_running_tasks(settings):
    db = Database(settings.db_path)
    task = db.create_task("Crashed", "goal", next_run_at=time.time())
    db.update_task(task["id"], status=store.RUNNING)
    run_id = db.create_run(task["id"])
    assert db.recover_interrupted() == 1
    assert db.get_task(task["id"])["status"] == store.PENDING
    assert db.list_runs(task["id"])[0]["status"] == store.FAILED
    assert run_id


def test_failed_run_is_recorded(settings):
    def boom(messages, tools):
        raise RuntimeError("model exploded")

    manager = make_manager(settings, FakeLLM(responder=boom))

    async def scenario():
        await manager.start()
        task = manager.create_task("Boom", "fail")
        await wait_for(lambda: manager.db.get_task(task["id"])["status"] == store.FAILED)
        await manager.stop()
        return task

    task = run(scenario())
    t = manager.db.get_task(task["id"])
    assert "model exploded" in t["last_result"]
    run_row = manager.db.list_runs(task["id"])[0]
    assert [e["type"] for e in manager.db.list_events(run_row["id"])] == ["error"]


def test_pause_and_resume(settings):
    manager = make_manager(settings, FakeLLM(["ok"]))
    task = manager.create_task("Later", "x", delay_minutes=10)
    manager.pause(task["id"])
    assert manager.db.get_task(task["id"])["status"] == store.PAUSED
    with pytest.raises(ValueError):
        manager.pause(task["id"])
    manager.resume(task["id"])
    assert manager.db.get_task(task["id"])["status"] == store.PENDING


def test_validation(settings):
    manager = make_manager(settings, FakeLLM())
    with pytest.raises(ValueError, match="empty"):
        manager.create_task("t", "  ")
    with pytest.raises(ValueError, match="at least"):
        manager.create_task("t", "goal", interval_minutes=0.1)
    with pytest.raises(ValueError, match="negative"):
        manager.create_task("t", "goal", delay_minutes=-1)
    settings.max_active_tasks = 1
    manager.create_task("", "first line becomes title\nmore")
    assert manager.db.list_tasks()[0]["title"] == "first line becomes title"
    with pytest.raises(ValueError, match="Too many"):
        manager.create_task("t", "goal")


def test_agent_can_schedule_its_own_background_work(settings):
    """A chat turn calls create_task; the scheduler then runs that task on its own."""

    def responder(messages, tools):
        system = messages[0]["content"]
        if "BACKGROUND TASK" in system:
            return "Background job finished."
        if messages[-1]["role"] == "user":
            return [ToolCall("create_task", title="Research", goal="Research X and report")]
        return "I started a background task for that."

    llm = FakeLLM(responder=responder)
    manager = make_manager(settings, llm)

    async def scenario():
        await manager.start()
        ctx = ToolContext(settings, manager, origin="chat:abc")
        messages = [{"role": "system", "content": "chat"}, {"role": "user", "content": "research X in the background"}]
        events = [e async for e in manager.agent.run(messages, ctx, 5)]
        [task] = manager.db.list_tasks()
        await wait_for(lambda: manager.db.get_task(task["id"])["status"] == store.COMPLETED)
        await manager.stop()
        return events, task

    events, task = run(scenario())
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] and task["id"] in result["content"]
    assert task["created_by"] == "chat:abc"
    assert manager.db.get_task(task["id"])["last_result"] == "Background job finished."


def test_task_cannot_cancel_itself(settings):
    manager = make_manager(settings, FakeLLM())
    task = manager.create_task("t", "goal", delay_minutes=5)
    ok, out = run(
        build_registry().execute(
            "cancel_task", f'{{"task_id": "{task["id"]}"}}', ToolContext(settings, manager, origin=f"task:{task['id']}")
        )
    )
    assert not ok and "cannot cancel itself" in out
