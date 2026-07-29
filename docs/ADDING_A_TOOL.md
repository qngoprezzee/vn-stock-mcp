# Adding a New MCP Tool

Since Phase B (tool registry), adding a tool is a **single-file edit** — the
old "3-place" workflow (schema in `list_tools()`, handler somewhere, dispatch
elif) is gone.

## The Recipe

1. Open `server.py`.
2. Pick the appropriate section (near related tools by convention).
3. Add a handler function decorated with `@register_tool`:

```python
@register_tool(
    name="get_my_new_tool",
    description=(
        "Human-readable description that Claude sees when deciding whether "
        "to call this tool. Be specific — this is your marketing copy."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "ticker": {"type": "string", "description": "VN ticker symbol"},
            "days": {"type": "integer", "default": 90, "description": "..."},
        },
        "required": ["ticker"],
    },
)
async def _get_my_new_tool(args: dict) -> list[types.TextContent]:
    ticker = args["ticker"].upper()
    days = int(args.get("days", 90))
    # ... fetch → compute → render ...
    return [types.TextContent(type="text", text="...")]
```

4. If the tool should be reachable from the Next.js UI, add a FastAPI endpoint
   in `api.py`:

```python
@app.post("/api/my/new-tool")
async def my_new_tool(req: MyRequest) -> dict[str, str]:
    result = await server._get_my_new_tool(req.model_dump())
    return {"text": _text(result)}
```

5. Update `web/lib/api.ts` with a typed client function.

That's it. The MCP server picks up the new tool automatically because the
`@register_tool` decorator runs at module import and inserts an entry into
the `vn_stock.tools.registry._REGISTRY` dict.

## Verifying

```bash
.venv/bin/python -c "
from vn_stock.tools.registry import registered_names
names = registered_names()
print(f'{len(names)} tools registered')
assert 'get_my_new_tool' in names, 'new tool not registered'
"
```

Then curl the FastAPI endpoint (or ask Claude in an MCP session) to smoke-test
the round-trip.

## Conventions

- **Handler function name**: prefix with `_` (they're implementation details;
  external contract is the MCP name string).
- **Handler signature**: `async def _<name>(args: dict) -> list[types.TextContent]`
  (or `list` if returning `ImageContent`). Use `_args` if you don't consume args.
- **Section**: group new tools near existing tools of the same domain in
  `server.py`. Domain modularization is deferred to future work.
- **Description**: write for Claude, not for a human developer. Focus on WHEN
  to use the tool, not HOW it works internally.
- **Input schema**: only what Claude needs to know. `default` fields are
  respected by MCP clients.

## Reuse Before You Write

Before writing new logic, check whether a helper exists in the shared package:

| Need | Look in |
|---|---|
| Fetch VN stock data | `vn_stock.data.vnstock_client.vnstock_subprocess` |
| Fetch WB macro data | `vn_stock.data.worldbank.fetch_wb_indicator` |
| Compute returns / CAGR / TWR | `vn_stock.analytics.returns` |
| Candles / pivots / Wyckoff | `vn_stock.analytics.technical` |
| Divergence detection | `vn_stock.analytics.divergence` |
| Storage paths | `vn_stock.config` |
| Portfolio / M2 / snapshot schemas | `vn_stock.storage.schemas` |
| Sector constants | `vn_stock.config.VN_SECTORS`, `CYCLICAL_SECTORS`, etc. |
| Markdown table | `vn_stock.render.tables.render_table` |
| Logging | `vn_stock.logging.get_logger(__name__)` |

## Removing a Tool

Delete the decorator + handler function from `server.py`. Nothing else — no
dispatch table to prune, no schema list to edit.

## Renaming a Tool

- Change the `name=` argument in the decorator.
- Update any code that calls `dispatch("old_name", ...)` — but nothing in the
  codebase should be doing that; only the MCP client (Claude) will.
- Skills in `.agents/skills/*.md` may reference the old name; grep + update.
- CLAUDE.md may need a table update.
