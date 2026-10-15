"""Cloud storage for the always-on web version (e.g. on Vercel), where the server has no
lasting disk: chats, tasks, settings and workspace files live in a Supabase Postgres
database, reached through its REST API (PostgREST).

The tables only answer requests that carry the app's secret (`X-SelfAI-Secret`), checked by
row-level security in the database (see deploy/supabase.sql). The API key alone is public."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .db import ACTIVE_STATUSES, FAILED, PENDING, RUNNING

log = logging.getLogger("selfai.remote")

PAGE = 1000  # Supabase returns at most 1000 rows per request


class StorageError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _in(values) -> str:
    return "in.(" + ",".join(values) + ")"


class RemoteDatabase:
    """Same interface as db.Database, stored in Supabase."""

    def __init__(self, url: str, api_key: str, secret: str, transport: httpx.BaseTransport | None = None):
        headers = {"apikey": api_key, "X-SelfAI-Secret": secret}
        if api_key.count(".") == 2:  # legacy JWT keys also go in Authorization
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.Client(
            base_url=url.rstrip("/") + "/rest/v1", headers=headers, timeout=30.0, transport=transport
        )

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------ REST helpers

    def _request(self, method: str, table: str, *, params=None, json_body=None, prefer: str = "") -> httpx.Response:
        headers = {"Prefer": prefer} if prefer else {}
        attempts = 2 if method in ("GET", "DELETE") else 1  # don't risk inserting twice
        for attempt in range(attempts):
            try:
                resp = self._client.request(method, f"/{table}", params=params, json=json_body, headers=headers)
                break
            except httpx.TransportError as exc:
                if attempt + 1 == attempts:
                    raise StorageError(f"Could not reach the database: {exc}") from exc
                time.sleep(0.5)
        if resp.status_code >= 400:
            raise StorageError(f"Database error {resp.status_code} on {method} {table}: {resp.text[:300]}")
        return resp

    def _all(self, table: str, params: dict) -> list[dict]:
        if "limit" in params:
            return self._request("GET", table, params=params).json()
        rows: list[dict] = []
        while True:
            page = self._request("GET", table, params={**params, "limit": PAGE, "offset": len(rows)}).json()
            rows.extend(page)
            if len(page) < PAGE:
                return rows

    def _one(self, table: str, params: dict) -> dict | None:
        rows = self._request("GET", table, params={**params, "limit": 1}).json()
        return rows[0] if rows else None

    def _insert(self, table: str, row: dict, *, upsert: bool = False) -> dict:
        prefer = "return=representation" + (",resolution=merge-duplicates" if upsert else "")
        return self._request("POST", table, json_body=row, prefer=prefer).json()[0]

    def _update(self, table: str, params: dict, fields: dict, *, returning: bool = False) -> list[dict]:
        resp = self._request(
            "PATCH", table, params=params, json_body=fields, prefer="return=representation" if returning else ""
        )
        return resp.json() if returning else []

    def _delete(self, table: str, params: dict) -> None:
        self._request("DELETE", table, params=params)

    # ------------------------------------------------------------------ settings

    def get_setting(self, key: str) -> str | None:
        row = self._one("settings", {"key": f"eq.{key}", "select": "value"})
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._insert("settings", {"key": key, "value": value}, upsert=True)

    def delete_setting(self, key: str) -> None:
        self._delete("settings", {"key": f"eq.{key}"})

    # ------------------------------------------------------------------ chats

    def create_conversation(self, title: str = "New chat") -> dict:
        now = _now()
        return self._insert(
            "conversations", {"id": uuid.uuid4().hex, "title": title, "created_at": now, "updated_at": now}
        )

    def get_conversation(self, conv_id: str) -> dict | None:
        return self._one("conversations", {"id": f"eq.{conv_id}"})

    def list_conversations(self) -> list[dict]:
        return self._all("conversations", {"order": "updated_at.desc"})

    def rename_conversation(self, conv_id: str, title: str) -> None:
        self._update("conversations", {"id": f"eq.{conv_id}"}, {"title": title, "updated_at": _now()})

    def touch_conversation(self, conv_id: str) -> None:
        self._update("conversations", {"id": f"eq.{conv_id}"}, {"updated_at": _now()})

    def delete_conversation(self, conv_id: str) -> None:
        self._delete("conversations", {"id": f"eq.{conv_id}"})  # messages go with it (ON DELETE CASCADE)

    def add_message(self, conv_id: str, message: dict, reasoning: str | None = None) -> dict:
        tool_calls = message.get("tool_calls")
        row = self._insert(
            "messages",
            {
                "conversation_id": conv_id,
                "role": message["role"],
                "content": message.get("content"),
                "tool_calls": json.dumps(tool_calls) if tool_calls else None,
                "tool_call_id": message.get("tool_call_id"),
                "name": message.get("name"),
                "reasoning": reasoning,
                "created_at": _now(),
            },
        )
        self.touch_conversation(conv_id)
        return self._message_row(row)

    @staticmethod
    def _message_row(row: dict) -> dict:
        row["tool_calls"] = json.loads(row["tool_calls"]) if row.get("tool_calls") else None
        return row

    def list_messages(self, conv_id: str) -> list[dict]:
        rows = self._all("messages", {"conversation_id": f"eq.{conv_id}", "order": "id"})
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
        return self._insert(
            "tasks",
            {
                "id": uuid.uuid4().hex[:12],
                "title": title,
                "goal": goal,
                "status": PENDING,
                "interval_minutes": interval_minutes,
                "next_run_at": next_run_at,
                "created_by": created_by,
                "created_at": now,
                "updated_at": now,
            },
        )

    def get_task(self, task_id: str) -> dict | None:
        return self._one("tasks", {"id": f"eq.{task_id}"})

    def list_tasks(self, status: str | None = None) -> list[dict]:
        params = {"order": "created_at.desc"}
        if status:
            params["status"] = f"eq.{status}"
        return self._all("tasks", params)

    def count_active_tasks(self) -> int:
        return len(self._all("tasks", {"status": _in(ACTIVE_STATUSES), "select": "id"}))

    def update_task(self, task_id: str, **fields: Any) -> None:
        if fields:
            self._update("tasks", {"id": f"eq.{task_id}"}, {**fields, "updated_at": _now()})

    def claim_task(self, task_id: str) -> bool:
        """Mark a due task as running, unless another server already did."""
        claimed = self._update(
            "tasks",
            {"id": f"eq.{task_id}", "status": f"eq.{PENDING}"},
            {"status": RUNNING, "updated_at": _now()},
            returning=True,
        )
        return bool(claimed)

    def delete_task(self, task_id: str) -> None:
        self._delete("tasks", {"id": f"eq.{task_id}"})

    def due_tasks(self, now: float, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        return self._all(
            "tasks", {"status": f"eq.{PENDING}", "next_run_at": f"lte.{now}", "order": "next_run_at", "limit": limit}
        )

    def next_due_time(self) -> float | None:
        row = self._one(
            "tasks", {"status": f"eq.{PENDING}", "select": "next_run_at", "order": "next_run_at.asc.nullslast"}
        )
        return row["next_run_at"] if row else None

    def recover_interrupted(self) -> int:
        """Re-queue every running task. Only safe when no other server is running tasks."""
        now = _now()
        self._update(
            "task_runs",
            {"status": f"eq.{RUNNING}"},
            {"status": FAILED, "result": "Interrupted by a restart.", "finished_at": now},
        )
        rows = self._update(
            "tasks",
            {"status": f"eq.{RUNNING}"},
            {"status": PENDING, "next_run_at": now, "updated_at": now},
            returning=True,
        )
        return len(rows)

    def stale_runs(self, started_before: float) -> list[dict]:
        return self._all("task_runs", {"status": f"eq.{RUNNING}", "started_at": f"lt.{started_before}", "order": "id"})

    def create_run(self, task_id: str) -> int:
        return int(self._insert("task_runs", {"task_id": task_id, "status": RUNNING, "started_at": _now()})["id"])

    def finish_run(self, run_id: int, status: str, result: str | None) -> None:
        self._update("task_runs", {"id": f"eq.{run_id}"}, {"status": status, "result": result, "finished_at": _now()})

    def list_runs(self, task_id: str, limit: int = 20) -> list[dict]:
        return self._all("task_runs", {"task_id": f"eq.{task_id}", "order": "id.desc", "limit": limit})

    def add_event(self, run_id: int, type_: str, data: dict) -> None:
        self._request(
            "POST",
            "task_events",
            json_body={"run_id": run_id, "type": type_, "data": json.dumps(data), "created_at": _now()},
        )

    def list_events(self, run_id: int) -> list[dict]:
        rows = self._all("task_events", {"run_id": f"eq.{run_id}", "order": "id"})
        for r in rows:
            r["data"] = json.loads(r["data"])
        return rows

    # ------------------------------------------------------------------ workspace files

    def list_files(self) -> dict[str, str]:
        """{path: sha1} of every stored file."""
        return {r["path"]: r["sha"] for r in self._all("files", {"select": "path,sha", "order": "path"})}

    def get_file(self, path: str) -> bytes | None:
        row = self._one("files", {"path": f"eq.{path}", "select": "data"})
        return base64.b64decode(row["data"]) if row else None

    def put_file(self, path: str, data: bytes, sha: str) -> None:
        self._insert(
            "files",
            {
                "path": path,
                "data": base64.b64encode(data).decode(),
                "sha": sha,
                "size": len(data),
                "updated_at": _now(),
            },
            upsert=True,
        )

    def delete_file(self, path: str) -> None:
        self._delete("files", {"path": f"eq.{path}"})

    # ------------------------------------------------------------------ scheduler

    def register_tick_url(self, url: str) -> None:
        """Tell the database's scheduler where to wake this server (see deploy/supabase.sql)."""
        try:
            resp = self._client.post("/rpc/selfai_register", json={"tick_url": url})
        except httpx.TransportError as exc:
            raise StorageError(f"Could not reach the database: {exc}") from exc
        if resp.status_code >= 400:
            raise StorageError(f"Database error {resp.status_code} registering {url}: {resp.text[:300]}")


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()  # noqa: S324 - change detection, not security


class WorkspaceSync:
    """Keeps the local workspace folder (temporary on serverless hosts) in step with the files
    stored in the database. The tools keep working on normal files: `pull()` before work starts,
    `push()` after anything may have changed them."""

    def __init__(self, db: RemoteDatabase, root: Path, max_bytes: int):
        self.db = db
        self.root = root
        self.max_bytes = max_bytes
        self.synced: dict[str, str] = {}  # path -> sha1 at the last pull/push
        self._hashes: dict[str, tuple[int, int, str]] = {}  # path -> (size, mtime_ns, sha1)
        self._lock = asyncio.Lock()

    async def pull(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._pull)

    async def push(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._push)

    def _local_files(self) -> dict[str, str]:
        files: dict[str, str] = {}
        if not self.root.is_dir():
            return files
        for path in self.root.rglob("*"):
            if not path.is_file() or path.name.endswith(".part"):
                continue
            rel = path.relative_to(self.root).as_posix()
            st = path.stat()
            cached = self._hashes.get(rel)
            if cached and cached[:2] == (st.st_size, st.st_mtime_ns):
                files[rel] = cached[2]
                continue
            sha = _sha1(path.read_bytes())
            self._hashes[rel] = (st.st_size, st.st_mtime_ns, sha)
            files[rel] = sha
        return files

    def _pull(self) -> None:
        remote = self.db.list_files()
        local = self._local_files()
        for rel, sha in remote.items():
            if local.get(rel) != sha:
                data = self.db.get_file(rel)
                if data is None:
                    continue
                target = self.root / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            self.synced[rel] = sha
        for rel in list(self.synced):
            if rel not in remote:  # deleted elsewhere
                (self.root / rel).unlink(missing_ok=True)
                del self.synced[rel]

    def _push(self) -> None:
        local = self._local_files()
        for rel, sha in local.items():
            if self.synced.get(rel) == sha:
                continue
            path = self.root / rel
            if path.stat().st_size > self.max_bytes:
                log.warning("Not saving %s: larger than %d bytes", rel, self.max_bytes)
                continue
            self.db.put_file(rel, path.read_bytes(), sha)
            self.synced[rel] = sha
        for rel in list(self.synced):
            if rel not in local:
                self.db.delete_file(rel)
                del self.synced[rel]
