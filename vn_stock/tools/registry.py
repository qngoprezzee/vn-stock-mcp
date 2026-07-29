"""Decorator-based tool registry.

Replaces the previous split between `list_tools()` schema block and the
`call_tool()` elif dispatcher in server.py. A single `@register_tool` call
defines both, and lookup is O(1).

Usage:
    from vn_stock.tools.registry import register_tool

    @register_tool(
        name="my_tool",
        description="What the tool does.",
        input_schema={"type": "object", "properties": {"ticker": {"type": "string"}}, "required": ["ticker"]},
    )
    async def my_handler(args: dict) -> list[types.TextContent]:
        ...

The MCP server calls `get_all_specs()` for `list_tools` and `dispatch()`
for `call_tool`. Handlers are looked up by name.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mcp import types

Handler = Callable[[dict], Awaitable[list[Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Handler


_REGISTRY: dict[str, ToolSpec] = {}


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict | None = None,
) -> Callable[[Handler], Handler]:
    """Decorator: register a handler as the MCP implementation of `name`."""
    def _decorator(fn: Handler) -> Handler:
        if name in _REGISTRY:
            raise ValueError(f"Tool '{name}' registered twice — check for duplicate @register_tool")
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}, "required": []},
            handler=fn,
        )
        return fn
    return _decorator


def get_all_specs() -> list[types.Tool]:
    """Return every registered tool as an MCP Tool spec, in registration order."""
    return [
        types.Tool(
            name=spec.name,
            description=spec.description,
            inputSchema=spec.input_schema,
        )
        for spec in _REGISTRY.values()
    ]


async def dispatch(name: str, arguments: dict) -> list[Any]:
    """Call the handler for `name` with the given arguments."""
    spec = _REGISTRY.get(name)
    if spec is None:
        raise ValueError(f"Unknown tool: {name}")
    return await spec.handler(arguments)


def registered_names() -> list[str]:
    """Introspection helper for tests and debugging."""
    return list(_REGISTRY.keys())
