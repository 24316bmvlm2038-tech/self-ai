"""In-memory stand-in for Supabase's REST API (PostgREST), covering what selfai.remote uses.

Like the real database, rows are only visible with the right X-SelfAI-Secret header (the
row-level security policies in deploy/supabase.sql)."""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

import httpx

SCHEMA = {
    # table: (primary key, identity?, columns with defaults, (foreign key column, parent table))
    "conversations": ("id", False, {"id": None, "title": None, "created_at": None, "updated_at": None}, None),
    "messages": (
        "id",
        True,
        dict.fromkeys(
            [
                "id",
                "conversation_id",
                "role",
                "content",
                "tool_calls",
                "tool_call_id",
                "name",
                "reasoning",
                "created_at",
            ]
        ),
        ("conversation_id", "conversations"),
    ),
    "tasks": (
        "id",
        False,
        {
            **dict.fromkeys(["id", "title", "goal", "status", "interval_minutes", "next_run_at"]),
            "created_by": "user",
            "last_result": None,
            "last_run_status": None,
            "run_count": 0,
            "created_at": None,
            "updated_at": None,
        },
        None,
    ),
    "task_runs": (
        "id",
        True,
        dict.fromkeys(["id", "task_id", "status", "result", "started_at", "finished_at"]),
        ("task_id", "tasks"),
    ),
    "task_events": ("id", True, dict.fromkeys(["id", "run_id", "type", "data", "created_at"]), ("run_id", "task_runs")),
    "settings": ("key", False, {"key": None, "value": None}, None),
    "files": ("path", False, dict.fromkeys(["path", "data", "sha", "size", "updated_at"]), None),
}


def _convert(raw: str, sample):
    if isinstance(sample, bool):
        return raw == "true"
    if isinstance(sample, (int, float)):
        return float(raw)
    return raw


def _matches(row: dict, column: str, expr: str) -> bool:
    op, _, raw = expr.partition(".")
    value = row.get(column)
    if op == "is":
        return value is None if raw == "null" else False
    if op == "in":
        options = raw.strip("()").split(",")
        return value is not None and str(value) in options
    if value is None:
        return False
    target = _convert(raw, value)
    return {
        "eq": value == target,
        "lt": value < target,
        "lte": value <= target,
        "gt": value > target,
        "gte": value >= target,
    }[op]


class FakeSupabase:
    def __init__(self, secret: str = "s3cret", api_key: str = "sb_publishable_test"):
        self.secret = secret
        self.api_key = api_key
        self.tables: dict[str, list[dict]] = {name: [] for name in SCHEMA}
        self.next_id = dict.fromkeys(SCHEMA, 1)
        self.requests: list[tuple[str, str]] = []
        self.config: dict[str, str] = {}  # the database's private settings

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------ helpers

    def _delete_rows(self, table: str, doomed: list[dict]) -> None:
        pk = SCHEMA[table][0]
        ids = {r[pk] for r in doomed}
        self.tables[table] = [r for r in self.tables[table] if r[pk] not in ids]
        for child, (_, _, _, fk) in SCHEMA.items():  # ON DELETE CASCADE
            if fk and fk[1] == table:
                self._delete_rows(child, [r for r in self.tables[child] if r[fk[0]] in ids])

    @staticmethod
    def _order(rows: list[dict], spec: str) -> list[dict]:
        for part in reversed(spec.split(",")):
            column, *mods = part.split(".")
            desc = "desc" in mods
            nulls_first = "nullsfirst" in mods or ("nullslast" not in mods and desc)
            present = [r for r in rows if r.get(column) is not None]
            missing = [r for r in rows if r.get(column) is None]
            present.sort(key=lambda r: r[column], reverse=desc)
            rows = missing + present if nulls_first else present + missing
        return rows

    # ------------------------------------------------------------------ request handling

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers.get("apikey") == self.api_key, "missing API key"
        table = request.url.path.removeprefix("/rest/v1/")
        self.requests.append((request.method, table))
        if table == "rpc/selfai_register":
            if request.headers.get("x-selfai-secret") != self.secret:
                return httpx.Response(401, json={"code": "42501", "message": "not authorized"})
            self.config["tick_url"] = json.loads(request.content)["tick_url"]
            return httpx.Response(204)
        if table not in SCHEMA:
            return httpx.Response(404, json={"message": f"relation {table} does not exist"})
        authorized = request.headers.get("x-selfai-secret") == self.secret
        pk, identity, columns, fk = SCHEMA[table]
        params = parse_qsl(request.url.query.decode())
        control = {k: v for k, v in params if k in ("select", "order", "limit", "offset")}
        filters = [(k, v) for k, v in params if k not in control]
        prefer = request.headers.get("prefer", "")
        rows = self.tables[table] if authorized else []  # row-level security
        selected = [r for r in rows if all(_matches(r, k, v) for k, v in filters)]

        if request.method == "GET":
            if "order" in control:
                selected = self._order(selected, control["order"])
            offset = int(control.get("offset", 0))
            selected = selected[offset:]
            if "limit" in control:
                selected = selected[: int(control["limit"])]
            if control.get("select", "*") != "*":
                keep = control["select"].split(",")
                selected = [{k: r[k] for k in keep} for r in selected]
            return httpx.Response(200, json=selected)

        if request.method == "POST":
            if not authorized:
                return httpx.Response(401, json={"code": "42501", "message": "new row violates row-level security"})
            body = json.loads(request.content)
            created = []
            for item in body if isinstance(body, list) else [body]:
                row = {**columns, **item}
                if identity:
                    row[pk] = self.next_id[table]
                    self.next_id[table] += 1
                if fk and not any(p[SCHEMA[fk[1]][0]] == row[fk[0]] for p in self.tables[fk[1]]):
                    return httpx.Response(409, json={"code": "23503", "message": "foreign key violation"})
                existing = next((r for r in self.tables[table] if r[pk] == row[pk]), None)
                if existing is not None:
                    if "resolution=merge-duplicates" not in prefer:
                        return httpx.Response(409, json={"code": "23505", "message": "duplicate key"})
                    existing.update(item)
                    created.append(dict(existing))
                    continue
                self.tables[table].append(row)
                created.append(dict(row))
            if "return=representation" in prefer:
                return httpx.Response(201, json=created)
            return httpx.Response(201)

        if request.method == "PATCH":
            fields = json.loads(request.content)
            for row in selected:
                row.update(fields)
            if "return=representation" in prefer:
                return httpx.Response(200, json=[dict(r) for r in selected])
            return httpx.Response(204)

        if request.method == "DELETE":
            self._delete_rows(table, selected)
            return httpx.Response(204)
        return httpx.Response(405)
