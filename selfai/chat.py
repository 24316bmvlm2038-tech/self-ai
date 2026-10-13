"""Runs chat turns in the background so a reply keeps going (and is saved) even if the
browser tab is closed. The HTTP layer just relays events from the run's queue."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .agent import Agent, chat_system_prompt, history_to_api, trim_history
from .config import Settings
from .db import Database
from .errors import describe_error
from .providers import active_model, config_problem, run_settings
from .tasks import TaskManager
from .tools import ToolContext

if TYPE_CHECKING:
    from .remote import WorkspaceSync

log = logging.getLogger("selfai.chat")

DEFAULT_TITLE = "New chat"


@dataclass
class ChatRun:
    task: asyncio.Task
    queue: asyncio.Queue
    timer: asyncio.TimerHandle | None = None
    timed_out: bool = False


class ChatService:
    def __init__(
        self,
        db: Database,
        agent: Agent,
        settings: Settings,
        tasks: TaskManager | None,
        files: "WorkspaceSync | None" = None,
    ):
        self.db = db
        self.agent = agent
        self.settings = settings
        self.tasks = tasks
        self.files = files  # cloud: workspace files stored in the database
        self.runs: dict[str, ChatRun] = {}

    def is_running(self, conv_id: str) -> bool:
        return conv_id in self.runs

    def start(self, conv_id: str, user_text: str, model: str | None = None, think: bool | None = None) -> asyncio.Queue:
        if conv_id in self.runs:
            raise RuntimeError("This conversation is already generating a reply.")
        conv = self.db.get_conversation(conv_id)
        if conv is not None and conv["title"] == DEFAULT_TITLE:
            first_line = " ".join(user_text.split())[:60]
            self.db.rename_conversation(conv_id, first_line or DEFAULT_TITLE)
        self.db.add_message(conv_id, {"role": "user", "content": user_text})
        queue: asyncio.Queue = asyncio.Queue()
        task = asyncio.create_task(self._run(conv_id, model, think, queue), name=f"selfai-chat-{conv_id}")
        run = self.runs[conv_id] = ChatRun(task, queue)
        if self.settings.run_time_limit:
            run.timer = asyncio.get_running_loop().call_later(self.settings.run_time_limit, self._time_up, run)
        return queue

    @staticmethod
    def _time_up(run: ChatRun) -> None:
        run.timed_out = True
        run.task.cancel()

    async def stop(self, conv_id: str) -> bool:
        run = self.runs.get(conv_id)
        if run is None:
            return False
        run.task.cancel()
        await asyncio.gather(run.task, return_exceptions=True)
        return True

    async def shutdown(self) -> None:
        for conv_id in list(self.runs):
            await self.stop(conv_id)

    async def _run(self, conv_id: str, model: str | None, think: bool | None, queue: asyncio.Queue) -> None:
        partial: list[str] = []
        try:
            problem = config_problem(self.db, self.settings)
            if problem:
                raise RuntimeError(problem)
            settings = run_settings(self.db, self.settings)
            model = model or active_model(self.db, settings)
            history = trim_history(history_to_api(self.db.list_messages(conv_id)), settings.history_max_chars)
            messages = [{"role": "system", "content": chat_system_prompt(settings)}, *history]
            if self.files:
                await self.files.pull()
            ctx = ToolContext(
                settings, self.tasks, origin=f"chat:{conv_id}", after_tool=self.files.push if self.files else None
            )
            async for ev in self.agent.run(messages, ctx, settings.chat_max_steps, model, think):
                if ev["type"] == "message":
                    self.db.add_message(conv_id, ev["message"], ev.get("reasoning"))
                    partial.clear()
                    continue
                if ev["type"] == "token":
                    partial.append(ev["text"])
                queue.put_nowait(ev)
        except asyncio.CancelledError:
            run = self.runs.get(conv_id)
            timed_out = run is not None and run.timed_out
            if "".join(partial).strip():
                self.db.add_message(conv_id, {"role": "assistant", "content": "".join(partial) + "\n\n*[stopped]*"})
            if not timed_out:
                queue.put_nowait({"type": "stopped"})
                raise
            minutes = self.settings.run_time_limit / 60
            text = f'Stopped after {minutes:g} minutes, the time limit for one reply here. Say "continue" to go on.'
            queue.put_nowait({"type": "notice", "text": text})
        except Exception as exc:  # noqa: BLE001 - report to the client
            log.exception("Chat run failed")
            queue.put_nowait({"type": "error", "error": describe_error(exc)})
        finally:
            run = self.runs.pop(conv_id, None)
            if run and run.timer:
                run.timer.cancel()
            queue.put_nowait(None)  # end of stream
