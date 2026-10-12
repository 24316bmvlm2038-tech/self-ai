"""File tools. Everything is sandboxed to the workspace directory."""

from __future__ import annotations

import asyncio
import io
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import Tool, ToolContext, ToolError

MAX_LIST_ENTRIES = 500


def resolve_path(workspace: Path, path: str | None) -> Path:
    """Resolve a user/model supplied path inside the workspace, refusing escapes."""
    root = workspace.resolve()
    rel = (path or ".").strip().replace("\\", "/").lstrip("/") or "."
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise ToolError(f"Path '{path}' is outside the workspace.")
    return candidate


def relative(workspace: Path, path: Path) -> str:
    rel = path.resolve().relative_to(workspace.resolve()).as_posix()
    return rel or "."


def clean_lines(text: str) -> str:
    """Normalize whitespace and collapse runs of blank lines."""
    out: list[str] = []
    blank = False
    for line in text.splitlines():
        line = re.sub(r"[ \t\u00a0\u200b]+", " ", line).strip()
        if not line:
            if not blank and out:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    return "\n".join(out).strip()


def pdf_to_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [f"--- page {i} ---\n{page.extract_text() or ''}" for i, page in enumerate(reader.pages, 1)]
    return clean_lines("\n".join(pages))


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def list_entries(workspace: Path, path: str = ".", recursive: bool = False) -> list[dict]:
    target = resolve_path(workspace, path)
    if not target.exists():
        raise ToolError(f"'{path}' does not exist.")
    if not target.is_dir():
        raise ToolError(f"'{path}' is a file, not a directory.")
    iterator = target.rglob("*") if recursive else target.iterdir()
    entries = []
    for p in sorted(iterator, key=lambda x: (not x.is_dir(), x.as_posix().lower())):
        if len(entries) >= MAX_LIST_ENTRIES:
            break
        stat = p.stat()
        entries.append(
            {
                "path": relative(workspace, p),
                "name": p.name,
                "is_dir": p.is_dir(),
                "size": 0 if p.is_dir() else stat.st_size,
                "modified": stat.st_mtime,
            }
        )
    return entries


async def write_file(ctx: ToolContext, path: str, content: str, append: bool = False) -> str:
    workspace = ctx.settings.workspace_dir
    target = resolve_path(workspace, path)
    if target == workspace.resolve() or target.is_dir():
        raise ToolError(f"'{path}' is a directory.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a" if append else "w", encoding="utf-8") as fh:
        fh.write(content)
    verb = "Appended" if append else "Wrote"
    return (
        f"{verb} {len(content)} characters to {relative(workspace, target)} (now {human_size(target.stat().st_size)})."
    )


async def read_file(ctx: ToolContext, path: str, start: int = 0, max_chars: int = 20_000) -> str:
    workspace = ctx.settings.workspace_dir
    target = resolve_path(workspace, path)
    if not target.is_file():
        raise ToolError(f"'{path}' is not a file.")
    data = target.read_bytes()
    if data[:5] == b"%PDF-":
        text = await asyncio.to_thread(pdf_to_text, data)
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return (
                f"{relative(workspace, target)} is a binary file ({human_size(len(data))}); it cannot be shown as text."
            )
    start = max(0, int(start))
    max_chars = max(1, min(int(max_chars), ctx.settings.max_tool_result_chars))
    chunk = text[start : start + max_chars]
    remaining = len(text) - (start + len(chunk))
    if remaining > 0:
        chunk += f"\n\n[{remaining} more characters; call read_file with start={start + len(chunk)} to continue]"
    return chunk


async def list_files(ctx: ToolContext, path: str = ".", recursive: bool = False) -> str:
    entries = list_entries(ctx.settings.workspace_dir, path, bool(recursive))
    if not entries:
        return f"'{path}' is empty."
    lines = []
    for e in entries:
        if e["is_dir"]:
            lines.append(f"[dir]  {e['path']}/")
        else:
            modified = datetime.fromtimestamp(e["modified"]).strftime("%Y-%m-%d %H:%M")
            lines.append(f"[file] {e['path']}  ({human_size(e['size'])}, modified {modified})")
    if len(entries) >= MAX_LIST_ENTRIES:
        lines.append(f"... listing stopped at {MAX_LIST_ENTRIES} entries")
    return "\n".join(lines)


async def delete_file(ctx: ToolContext, path: str) -> str:
    workspace = ctx.settings.workspace_dir
    target = resolve_path(workspace, path)
    if target == workspace.resolve():
        raise ToolError("Refusing to delete the whole workspace.")
    if not target.exists():
        raise ToolError(f"'{path}' does not exist.")
    if target.is_dir():
        shutil.rmtree(target)
        return f"Deleted directory {path}."
    target.unlink()
    return f"Deleted {path}."


TOOLS = [
    Tool(
        name="write_file",
        description=(
            "Create or overwrite a text file in the workspace (parent folders are created automatically). "
            "Use for documents, reports, code, notes, CSV/JSON data, HTML pages, etc. "
            "Set append=true to add to the end of an existing file instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path in the workspace, e.g. 'reports/summary.md'."},
                "content": {"type": "string", "description": "Full text content to write."},
                "append": {"type": "boolean", "description": "Append instead of overwrite.", "default": False},
            },
            "required": ["path", "content"],
        },
        handler=write_file,
    ),
    Tool(
        name="read_file",
        description="Read a text or PDF file from the workspace. Large files are returned in chunks; use 'start' to page.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "description": "Character offset to start from.", "default": 0},
                "max_chars": {"type": "integer", "default": 20000},
            },
            "required": ["path"],
        },
        handler=read_file,
    ),
    Tool(
        name="list_files",
        description="List files and folders in the workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "default": "."},
                "recursive": {"type": "boolean", "default": False},
            },
        },
        handler=list_files,
    ),
    Tool(
        name="delete_file",
        description="Delete a file or folder (recursively) from the workspace.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=delete_file,
    ),
]
