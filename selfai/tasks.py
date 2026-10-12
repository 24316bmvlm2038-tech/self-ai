"""Background task manager: a scheduler loop that runs due tasks autonomously with the agent."""

from __future__ import annotations

import asyncio
import logging
import time

from . import db as store
from .agent import Agent, task_system_prompt, task_user_prompt
from .config import Settings
from .errors import describe_error
from .providers import active_model, config_problem, run_settings
from .tools import ToolContext

log = logging.getLogger("selfai.tasks")

MIN_INTERVAL_MINUTES = 1.0
EVENT_TEXT_LIMIT = 4000


class TaskManager:
    def __init__(self, db: store.Database, agent: Agent, settings: Settings):
        self.db = db
        self.agent = agent
        self.settings = settings
        self._running: dict[str, asyncio.Task] = {}
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._shutting_down = False

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        recovered = self.db.recover_interrupted()
        if recovered:
            log.info("Re-queued %d task(s) interrupted by the last shutdown", recovered)
        self._shutting_down = False
        self._loop = asyncio.get_running_loop()
        self._loop_task = asyncio.create_task(self._scheduler(), name="selfai-scheduler")

    async def stop(self) -> None:
        self._shutting_down = True
        if self._loop_task:
            self._loop_task.cancel()
        running = list(self._running.values())
        for task in running:
            task.cancel()
        await asyncio.gather(*running, *([self._loop_task] if self._loop_task else []), return_exceptions=True)

    def is_running(self, task_id: str) -> bool:
        return task_id in self._running

    def _notify(self) -> None:
        """Wake the scheduler now (safe to call from any thread)."""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            self._wake.set()
        else:
            self._loop.call_soon_threadsafe(self._wake.set)

    # ------------------------------------------------------------------ commands

    def create_task(
        self,
        title: str,
        goal: str,
        delay_minutes: float | None = 0,
        interval_minutes: float | None = None,
        created_by: str = "user",
    ) -> dict:
        title = (title or "").strip()
        goal = (goal or "").strip()
        if not goal:
            raise ValueError("The task goal is empty.")
        if not title:
            title = goal.splitlines()[0][:60]
        delay = float(delay_minutes or 0)
        if delay < 0:
            raise ValueError("delay_minutes cannot be negative.")
        interval = float(interval_minutes) if interval_minutes else None
        if interval is not None and interval < MIN_INTERVAL_MINUTES:
            raise ValueError(f"interval_minutes must be at least {MIN_INTERVAL_MINUTES:g}.")
        if self.db.count_active_tasks() >= self.settings.max_active_tasks:
            raise ValueError(
                f"Too many active tasks (limit {self.settings.max_active_tasks}). "
                "Cancel or wait for some to finish first."
            )
        task = self.db.create_task(
            title=title[:200],
            goal=goal,
            next_run_at=time.time() + delay * 60,
            interval_minutes=interval,
            created_by=created_by,
        )
        self._notify()
        return task

    async def cancel(self, task_id: str) -> None:
        self.db.update_task(task_id, status=store.CANCELLED, next_run_at=None)
        running = self._running.get(task_id)
        if running:
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)

    def pause(self, task_id: str) -> None:
        task = self._require(task_id)
        if task["status"] not in (store.PENDING, store.RUNNING):
            raise ValueError(f"Only pending or running tasks can be paused (task is {task['status']}).")
        # A running task finishes its current run, then stays paused.
        self.db.update_task(task_id, status=store.PAUSED)

    def resume(self, task_id: str) -> None:
        task = self._require(task_id)
        if task["status"] != store.PAUSED:
            raise ValueError("Task is not paused.")
        if self.is_running(task_id):
            self.db.update_task(task_id, status=store.RUNNING)
            return
        self.db.update_task(task_id, status=store.PENDING, next_run_at=max(task["next_run_at"] or 0, time.time()))
        self._notify()

    def run_now(self, task_id: str) -> None:
        self._require(task_id)
        if self.is_running(task_id):
            raise ValueError("Task is already running.")
        self.db.update_task(task_id, status=store.PENDING, next_run_at=time.time())
        self._notify()

    async def delete(self, task_id: str) -> None:
        await self.cancel(task_id)
        self.db.delete_task(task_id)

    def _require(self, task_id: str) -> dict:
        task = self.db.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        return task

    # ------------------------------------------------------------------ scheduler

    async def _scheduler(self) -> None:
        while True:
            self._wake.clear()
            try:
                free = self.settings.max_concurrent_tasks - len(self._running)
                for task in self.db.due_tasks(time.time(), free):
                    self._launch(task)
            except Exception:  # noqa: BLE001 - keep the scheduler alive
                log.exception("Scheduler iteration failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self.settings.scheduler_poll_seconds)
            except asyncio.TimeoutError:
                pass

    def _launch(self, task: dict) -> None:
        self.db.update_task(task["id"], status=store.RUNNING)
        run_id = self.db.create_run(task["id"])
        job = asyncio.create_task(self._run(task, run_id), name=f"selfai-task-{task['id']}")
        self._running[task["id"]] = job

        def _done(_: asyncio.Task) -> None:
            self._running.pop(task["id"], None)
            self._wake.set()  # a slot is free

        job.add_done_callback(_done)
        log.info("Started task %s (%s), run %d", task["id"], task["title"], run_id)

    async def _run(self, task: dict, run_id: int) -> None:
        status, result = store.FAILED, "Run did not finish."
        interrupted = False
        try:
            problem = config_problem(self.db, self.settings)
            if problem:
                raise RuntimeError(problem)
            settings = run_settings(self.db, self.settings)
            messages = [
                {"role": "system", "content": task_system_prompt(settings, task)},
                {"role": "user", "content": task_user_prompt(task)},
            ]
            ctx = ToolContext(settings, self, origin=f"task:{task['id']}")
            final = ""
            model = active_model(self.db, settings)
            think = settings.task_thinking
            async for ev in self.agent.run(messages, ctx, settings.task_max_steps, model, think):
                kind = ev["type"]
                if kind == "message" and ev["message"]["role"] == "assistant":
                    msg = ev["message"]
                    if msg["content"] and msg.get("tool_calls"):
                        self.db.add_event(run_id, "thought", {"content": msg["content"][:EVENT_TEXT_LIMIT]})
                elif kind == "tool_call":
                    self.db.add_event(run_id, "tool_call", {"name": ev["name"], "arguments": ev["arguments"]})
                elif kind == "tool_result":
                    self.db.add_event(
                        run_id,
                        "tool_result",
                        {"name": ev["name"], "ok": ev["ok"], "content": ev["content"][:EVENT_TEXT_LIMIT]},
                    )
                elif kind == "notice":
                    self.db.add_event(run_id, "notice", {"content": ev["text"]})
                elif kind == "done":
                    final = ev["content"]
            status = store.COMPLETED
            result = final.strip() or "(The task finished without a final report.)"
            self.db.add_event(run_id, "final", {"content": result})
        except asyncio.CancelledError:
            interrupted = self._shutting_down
            status = store.FAILED if interrupted else store.CANCELLED
            result = "Interrupted by shutdown; will resume on restart." if interrupted else "Cancelled."
            self.db.add_event(run_id, "notice", {"content": result})
            raise
        except Exception as exc:  # noqa: BLE001 - record any failure on the run
            log.exception("Task %s failed", task["id"])
            result = describe_error(exc)
            self.db.add_event(run_id, "error", {"content": result})
        finally:
            self._finish(task, run_id, status, result, interrupted)

    def _finish(self, task: dict, run_id: int, status: str, result: str, interrupted: bool) -> None:
        self.db.finish_run(run_id, status, result)
        current = self.db.get_task(task["id"])
        if current is None:  # deleted while running
            return
        fields: dict = {
            "run_count": current["run_count"] + 1,
            "last_result": result,
            "last_run_status": status,
        }
        if interrupted:
            fields.update(status=store.PENDING, next_run_at=time.time())
        elif current["status"] in (store.CANCELLED, store.PAUSED):
            pass  # user decision made during the run wins
        elif current["interval_minutes"]:
            fields.update(status=store.PENDING, next_run_at=time.time() + current["interval_minutes"] * 60)
        else:
            fields.update(status=status, next_run_at=None)
        self.db.update_task(task["id"], **fields)
        log.info("Task %s run %d finished: %s", task["id"], run_id, status)
