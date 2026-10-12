"""SQLite persistence for conversations, messages, background tasks and their runs."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    tool_calls TEXT,
    tool_call_id TEXT,
    name TEXT,
    reasoning TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    interval_minutes REAL,
    next_run_at REAL,
    created_by TEXT NOT NULL DEFAULT 'user',
    last_result TEXT,
    last_run_status TEXT,
    run_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(status, next_run_at);
CREATE TABLE IF NOT EXISTS task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    result TEXT,
    started_at REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_task_runs_task ON task_runs(task_id, id);
CREATE TABLE IF NOT EXISTS task_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_events_run ON task_events(run_id, id);
"""

# Task statuses
PENDING = "pending"  # waiting for next_run_at
RUNNING = "running"
PAUSED = "paused"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
ACTIVE_STATUSES = (PENDING, RUNNING, PAUSED)


def _now() -> float:
    return time.time()


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: tuple | list = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def _one(self, sql: str, params: tuple | list = ()) -> dict | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, params: tuple | list = ()) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ chats

    def create_conversation(self, title: str = "New chat") -> dict:
        now = _now()
        conv_id = uuid.uuid4().hex
        self._execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (conv_id, title, now, now),
        )
        return self.get_conversation(conv_id)  # type: ignore[return-value]

    def get_conversation(self, conv_id: str) -> dict | None:
        return self._one("SELECT * FROM conversations WHERE id = ?", (conv_id,))

    def list_conversations(self) -> list[dict]:
        return self._all("SELECT * FROM conversations ORDER BY updated_at DESC")

    def rename_conversation(self, conv_id: str, title: str) -> None:
        self._execute("UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?", (title, _now(), conv_id))

    def touch_conversation(self, conv_id: str) -> None:
        self._execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conv_id))

    def delete_conversation(self, conv_id: str) -> None:
        self._execute("DELETE FROM conversations WHERE id = ?", (conv_id,))

    def add_message(self, conv_id: str, message: dict, reasoning: str | None = None) -> dict:
        tool_calls = message.get("tool_calls")
        cur = self._execute(
            "INSERT INTO messages (conversation_id, role, content, tool_calls, tool_call_id, name, reasoning, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conv_id,
                message["role"],
                message.get("content"),
                json.dumps(tool_calls) if tool_calls else None,
                message.get("tool_call_id"),
                message.get("name"),
                reasoning,
                _now(),
            ),
        )
        self.touch_conversation(conv_id)
        return self._message_row(self._one("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)))  # type: ignore[arg-type]

    @staticmethod
    def _message_row(row: dict) -> dict:
        row["tool_calls"] = json.loads(row["tool_calls"]) if row.get("tool_calls") else None
        return row

    def list_messages(self, conv_id: str) -> list[dict]:
        rows = self._all("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conv_id,))
        return [self._message_row(r) for r in rows]

    # ------------------------------------------------------------------ tasks

    def create_task(
        self,
        title: str,
        goal: str,
        next_run_at: float,
        interval_minutes: float | None = None,
        created_by: str = "user",
    ) -> dict:
        now = _now()
        task_id = uuid.uuid4().hex[:12]
        self._execute(
            "INSERT INTO tasks (id, title, goal, status, interval_minutes, next_run_at, created_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, title, goal, PENDING, interval_minutes, next_run_at, created_by, now, now),
        )
        return self.get_task(task_id)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> dict | None:
        return self._one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def list_tasks(self, status: str | None = None) -> list[dict]:
        if status:
            return self._all("SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC", (status,))
        return self._all("SELECT * FROM tasks ORDER BY created_at DESC")

    def count_active_tasks(self) -> int:
        row = self._one(
            f"SELECT COUNT(*) AS n FROM tasks WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
            ACTIVE_STATUSES,
        )
        return int(row["n"]) if row else 0

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._execute(f"UPDATE tasks SET {cols} WHERE id = ?", (*fields.values(), task_id))

    def delete_task(self, task_id: str) -> None:
        self._execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def due_tasks(self, now: float, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        return self._all(
            "SELECT * FROM tasks WHERE status = ? AND next_run_at <= ? ORDER BY next_run_at LIMIT ?",
            (PENDING, now, limit),
        )

    def next_due_time(self) -> float | None:
        row = self._one("SELECT MIN(next_run_at) AS t FROM tasks WHERE status = ?", (PENDING,))
        return row["t"] if row and row["t"] is not None else None

    def recover_interrupted(self) -> int:
        """Called at startup: runs that were in progress when the process died get re-queued."""
        now = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE task_runs SET status = ?, result = ?, finished_at = ? WHERE status = ?",
                (FAILED, "Interrupted by a restart.", now, RUNNING),
            )
            cur = self._conn.execute(
                "UPDATE tasks SET status = ?, next_run_at = ?, updated_at = ? WHERE status = ?",
                (PENDING, now, now, RUNNING),
            )
            return cur.rowcount

    def create_run(self, task_id: str) -> int:
        cur = self._execute(
            "INSERT INTO task_runs (task_id, status, started_at) VALUES (?, ?, ?)", (task_id, RUNNING, _now())
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def finish_run(self, run_id: int, status: str, result: str | None) -> None:
        self._execute(
            "UPDATE task_runs SET status = ?, result = ?, finished_at = ? WHERE id = ?",
            (status, result, _now(), run_id),
        )

    def list_runs(self, task_id: str, limit: int = 20) -> list[dict]:
        return self._all("SELECT * FROM task_runs WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task_id, limit))

    def add_event(self, run_id: int, type_: str, data: dict) -> None:
        self._execute(
            "INSERT INTO task_events (run_id, type, data, created_at) VALUES (?, ?, ?, ?)",
            (run_id, type_, json.dumps(data), _now()),
        )

    def list_events(self, run_id: int) -> list[dict]:
        rows = self._all("SELECT * FROM task_events WHERE run_id = ? ORDER BY id", (run_id,))
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows
