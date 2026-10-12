"""Runs chat turns in the background so a reply keeps going (and is saved) even if the
browser tab is closed. The HTTP layer just relays events from the run's queue."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .agent import Agent, chat_system_prompt, history_to_api, trim_history
from .config import Settings
from .db import Database
from .errors import describe_error
from .models import active_model
from .tasks import TaskManager
from .tools import ToolContext

log = logging.getLogger("selfai.chat")

DEFAULT_TITLE = "New chat"


@dataclass
class ChatRun:
    task: asyncio.Task
    queue: asyncio.Queue


class ChatService:
    def __init__(self, db: Database, agent: Agent, settings: Settings, tasks: TaskManager | None):
        self.db = db
        self.agent = agent
        self.settings = settings
        self.tasks = tasks
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
        self.runs[conv_id] = ChatRun(task, queue)
        return queue

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
            problem = self.settings.config_problem()
            if problem:
                raise RuntimeError(problem)
            model = model or active_model(self.db, self.settings)
            history = trim_history(history_to_api(self.db.list_messages(conv_id)), self.settings.history_max_chars)
            messages = [{"role": "system", "content": chat_system_prompt(self.settings)}, *history]
            ctx = ToolContext(self.settings, self.tasks, origin=f"chat:{conv_id}")
            async for ev in self.agent.run(messages, ctx, self.settings.chat_max_steps, model, think):
                if ev["type"] == "message":
                    self.db.add_message(conv_id, ev["message"], ev.get("reasoning"))
                    partial.clear()
                    continue
                if ev["type"] == "token":
                    partial.append(ev["text"])
                queue.put_nowait(ev)
        except asyncio.CancelledError:
            if "".join(partial).strip():
                self.db.add_message(conv_id, {"role": "assistant", "content": "".join(partial) + "\n\n*[stopped]*"})
            queue.put_nowait({"type": "stopped"})
            raise
        except Exception as exc:  # noqa: BLE001 - report to the client
            log.exception("Chat run failed")
            queue.put_nowait({"type": "error", "error": describe_error(exc)})
        finally:
            self.runs.pop(conv_id, None)
            queue.put_nowait(None)  # end of stream
