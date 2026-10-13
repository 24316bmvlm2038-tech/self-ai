"""Tool registry: the functions the agent can call (internet, files, background tasks)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from ..config import Settings

log = logging.getLogger("selfai.tools")

if TYPE_CHECKING:
    from ..tasks import TaskManager


class ToolError(Exception):
    """An error the model should see and can recover from (bad path, HTTP error, ...)."""


@dataclass
class ToolContext:
    settings: Settings
    tasks: "TaskManager | None" = None
    # "chat:<conversation id>" or "task:<task id>"
    origin: str = "chat"
    # Called after every tool call (the web version saves changed workspace files with it).
    after_tool: Callable[[], Awaitable[None]] | None = None


Handler = Callable[..., Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    handler: Handler

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    async def execute(self, name: str, raw_arguments: str, ctx: ToolContext) -> tuple[bool, str]:
        """Run a tool call. Returns (ok, text result). Never raises for tool-level failures."""
        try:
            return await self._execute(name, raw_arguments, ctx)
        finally:
            if ctx.after_tool:
                try:
                    await ctx.after_tool()
                except Exception:  # noqa: BLE001 - the tool's own result still counts
                    log.exception("Saving workspace files failed")

    async def _execute(self, name: str, raw_arguments: str, ctx: ToolContext) -> tuple[bool, str]:
        tool = self.tools.get(name)
        if tool is None:
            return False, f"Error: unknown tool '{name}'. Available: {', '.join(self.tools)}"
        try:
            args: Any = json.loads(raw_arguments) if raw_arguments.strip() else {}
        except json.JSONDecodeError as exc:
            return False, f"Error: arguments are not valid JSON ({exc}). Raw: {raw_arguments[:500]}"
        if not isinstance(args, dict):
            return False, "Error: arguments must be a JSON object."
        try:
            result = await asyncio.wait_for(tool.handler(ctx, **args), timeout=ctx.settings.tool_timeout_seconds)
        except ToolError as exc:
            return False, f"Error: {exc}"
        except TypeError as exc:
            return False, f"Error: bad arguments for {name}: {exc}"
        except asyncio.TimeoutError:
            return False, f"Error: {name} timed out after {ctx.settings.tool_timeout_seconds}s."
        except Exception as exc:  # noqa: BLE001 - surface any failure to the model
            return False, f"Error: {name} failed: {type(exc).__name__}: {exc}"
        limit = ctx.settings.max_tool_result_chars
        if len(result) > limit:
            result = result[:limit] + f"\n\n[Output truncated: {len(result) - limit} more characters]"
        return True, result


def build_registry() -> ToolRegistry:
    from . import files, tasks, web

    registry = ToolRegistry()
    for module in (web, files, tasks):
        for tool in module.TOOLS:
            registry.register(tool)
    return registry
