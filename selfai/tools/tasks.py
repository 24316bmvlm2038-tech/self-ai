"""Tools that let the agent schedule its own background work."""

from __future__ import annotations

from datetime import datetime

from . import Tool, ToolContext, ToolError


def _manager(ctx: ToolContext):
    if ctx.tasks is None:
        raise ToolError("Background tasks are not available in this context.")
    return ctx.tasks


def _fmt_time(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


async def create_task(
    ctx: ToolContext,
    title: str,
    goal: str,
    delay_minutes: float = 0,
    interval_minutes: float | None = None,
) -> str:
    try:
        task = _manager(ctx).create_task(
            title=title,
            goal=goal,
            delay_minutes=delay_minutes,
            interval_minutes=interval_minutes,
            created_by=ctx.origin,
        )
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    when = "now" if not delay_minutes else f"at {_fmt_time(task['next_run_at'])}"
    repeat = f", then every {interval_minutes:g} minutes" if interval_minutes else ""
    return (
        f"Created background task {task['id']} '{task['title']}'. It will start {when}{repeat}. "
        "The user can follow it in the Tasks panel."
    )


async def list_tasks(ctx: ToolContext, status: str | None = None) -> str:
    tasks = _manager(ctx).db.list_tasks(status or None)
    if not tasks:
        return "No background tasks."
    lines = []
    for t in tasks[:50]:
        repeat = f", every {t['interval_minutes']:g} min" if t["interval_minutes"] else ""
        line = f"- {t['id']} [{t['status']}{repeat}] {t['title']} (runs: {t['run_count']}, next: {_fmt_time(t['next_run_at'])})"
        if t["last_result"]:
            line += f"\n  last result: {t['last_result'][:300]}"
        lines.append(line)
    return "\n".join(lines)


async def cancel_task(ctx: ToolContext, task_id: str) -> str:
    manager = _manager(ctx)
    if ctx.origin == f"task:{task_id}":
        raise ToolError("A task cannot cancel itself; just finish with your final report.")
    if manager.db.get_task(task_id) is None:
        raise ToolError(f"No task with id {task_id}.")
    await manager.cancel(task_id)
    return f"Cancelled task {task_id}."


TOOLS = [
    Tool(
        name="create_task",
        description=(
            "Start a background task that you (the AI) will work on autonomously, without the user waiting. "
            "Use it for long research jobs, multi-step projects, monitoring, or anything the user wants done "
            "later or on a schedule. Write the goal as complete, self-contained instructions, because the "
            "background run will not see this conversation."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short name for the task."},
                "goal": {"type": "string", "description": "Detailed, self-contained instructions and expected output."},
                "delay_minutes": {
                    "type": "number",
                    "description": "Wait this long before the first run.",
                    "default": 0,
                },
                "interval_minutes": {
                    "type": "number",
                    "description": "If set, repeat the task every N minutes (minimum 1). Omit for a one-off task.",
                },
            },
            "required": ["title", "goal"],
        },
        handler=create_task,
    ),
    Tool(
        name="list_tasks",
        description="List background tasks with their status and latest results.",
        parameters={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "running", "paused", "completed", "failed", "cancelled"],
                },
            },
        },
        handler=list_tasks,
    ),
    Tool(
        name="cancel_task",
        description="Cancel a background task (stops it if running and prevents future runs).",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
        handler=cancel_task,
    ),
]
