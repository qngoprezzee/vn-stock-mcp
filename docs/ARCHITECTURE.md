# Architecture

## Overview

`vn-stock-mcp` is a Vietnamese equity research toolkit exposed over both MCP
(for Claude) and HTTP (for the Next.js UI). The two surfaces share their
business logic — refactoring the middle layer benefits both.

```
┌───────────────────────────┐          ┌────────────────────────────┐
│   Claude Code / MCP CLI   │          │    Next.js UI (web/)       │
└───────────┬───────────────┘          └────────────┬───────────────┘
            │ stdio (MCP protocol)                  │ HTTP (JSON)
            ▼                                       ▼
   ┌──────────────────┐                    ┌────────────────┐
   │    server.py     │◄────────────────► │     api.py      │
   │  (MCP entrypoint,│  imports server._*  │ (FastAPI)      │
   │   40 tool handlers)                    └────────┬───────┘
   └────────┬─────────┘                              │
            │                                        │
            ▼                                        │
   ┌─────────────────────────────────────────────────┴──────┐
   │                     vn_stock/                          │
   │                                                        │
   │  config.py     Storage paths, TTLs, sector defs        │
   │  logging.py    Structured logger                       │
   │                                                        │
   │  data/         External data access                    │
   │    vnstock_client.py  Subprocess-isolated vnstock      │
   │    cache.py           File cache (.cache/)             │
   │    worldbank.py       WB API + retry + stale fallback  │
   │                                                        │
   │  analytics/    Pure math (no I/O — easy to test)       │
   │    returns.py         TWR, CAGR, period_return, corr   │
   │    technical.py       Candles, pivots, gaps, Wyckoff   │
   │    divergence.py      Price↔indicator divergence       │
   │                                                        │
   │  storage/      Persistent JSON state                   │
   │    schemas.py         Pydantic models                  │
   │                                                        │
   │  render/       Markdown builders                       │
   │    tables.py          Table builder                    │
   │                                                        │
   │  tools/        (Phase B: registry to replace elif)     │
   └─────────────────┬──────────────────────────────────────┘
                     │
                     ▼
        External data sources:
          - vnstock (via _vnstock_worker.py subprocess)
          - World Bank Open Data API
          - RSS feeds (CafeF, VnEconomy, VnExpress, ...)
          - MASVN broker API
```

## Module Boundaries

| Layer | Responsibility | Depends on |
|---|---|---|
| `server.py` (root) | MCP protocol, tool routing, handler orchestration | `vn_stock.*`, `mcp` |
| `api.py` (root) | FastAPI endpoints, HTTP schemas, file uploads | `server`, `fastapi` |
| `vn_stock.data` | External I/O (subprocesses, HTTP, cache) | `vn_stock.config`, `vn_stock.logging` |
| `vn_stock.analytics` | Pure math on time series and DataFrames | numpy / pandas only |
| `vn_stock.storage` | Persistent JSON state validation | `pydantic` |
| `vn_stock.render` | Markdown formatting helpers | (none) |
| `vn_stock.config` | Constants, paths, sector lookups | (none) |
| `vn_stock.logging` | Structured logging setup | (none) |
| `_vnstock_worker.py` (root) | Child subprocess for vnstock calls — DO NOT CHANGE | vnstock, sys |

**Invariant**: `vn_stock.analytics`, `vn_stock.render`, `vn_stock.config`,
`vn_stock.logging` are safe to import in any order — no side effects, no I/O.
`vn_stock.data` touches disk/network; its cache side effects are deterministic.

## Persistent State

All persistent state lives at repo root as JSON files, gitignored:

| File | Schema | Producer |
|---|---|---|
| `.watchlist.json` | list of ticker strings | `manage_watchlist` |
| `.portfolio.json` | `Portfolio` (schemas.py) | `manage_portfolio` |
| `.portfolio_snapshots.json` | list of `Snapshot` | auto-appended by `get_portfolio_overview` |
| `.m2_series.json` | list of `M2Point` | `manage_m2_series` |
| `.cache/` | vnstock + WB response cache | `data/cache.py`, `data/worldbank.py` |

Read paths from `vn_stock.config` — never hard-code them.

## Cross-cutting Concerns

### Caching

- `vn_stock/data/cache.py` — file cache for vnstock. Per-function TTLs in `config.CACHE_TTL`.
- `vn_stock/data/worldbank.py` — WB annual data, 7-day TTL, 90-day stale-fallback.

### Logging

Import `from vn_stock.logging import get_logger`. Use it in modules that hit
external systems. The logger writes to stderr in `key=value` format for grep
(MCP protocol claims stdout).

Set `VN_STOCK_LOG_LEVEL=DEBUG` to see cache hit/miss + subprocess events.

### Error handling

External data sources fail often (WB 502, vnstock rate limits, RSS timeouts).
Convention:
- `data/*.py` returns `None` / empty list / freshness label on failure — never raises.
- Handlers log warnings via the module logger but don't raise to the MCP caller.
- Handlers return a graceful text-based error message when a critical fetch fails.

## Where to Add New Code

| I want to add… | Where |
|---|---|
| A new pure math primitive | `vn_stock/analytics/<domain>.py` |
| A new data source (broker API, RSS, etc.) | `vn_stock/data/<source>.py` |
| A new persistent state file | `vn_stock/storage/schemas.py` (schema) + `config.py` (path) |
| A new MCP tool | `server.py` — one handler with `@register_tool(name=..., description=..., input_schema=...)`. See `docs/ADDING_A_TOOL.md`. |
| A new HTTP endpoint | `api.py` — call `await server._<handler>()` |
| A new UI page | `web/app/<page>/page.tsx` + client fn in `web/lib/api.ts` |
| Shared UI component | `web/components/<Name>.tsx` (see `MarkdownBlock`) |

## Refactor Status

- ✅ **Phase A** — Analytics + schemas + shared foundations. Constants moved
  from server.py inline into `vn_stock/config.py`. Pure math functions moved
  to `vn_stock/analytics/`. Data-access I/O moved to `vn_stock/data/`. Storage
  schemas defined in `vn_stock/storage/schemas.py`. server.py shrunk from
  7,427 → 6,890 lines. Byte-identical parity verified across 5 canonical tool
  outputs.
- ✅ **Phase B** — Tool registry. Introduced `vn_stock/tools/registry.py`
  with `@register_tool` decorator, `get_all_specs()`, and `dispatch()`. All 41
  tool schemas moved from a monolithic `list_tools()` block into decorators
  colocated with their handlers. Elimination of the 85-line elif dispatcher.
  server.py shrunk from 6,890 → 6,031 lines. Byte-identical parity verified.
  Adding a new tool now requires exactly one file edit (see
  `docs/ADDING_A_TOOL.md`).
- ⏳ **Phase C** — Frontend `ResultPanel`/`ErrorBanner` extraction.
- 🕰️ **Deferred** — Per-domain tool modules, FastAPI router split, page
  splitting (`_sections/`), test infrastructure.

## Known Latent Bugs Fixed During Phase A

- **`_period_return` name collision**: server.py had two module-scope definitions
  (line 6356 snapshot-based, line 6555 price-series). Second overrode first,
  silently breaking `_get_portfolio_returns`. Phase A disambiguates by renaming
  to `period_return_from_snapshots` and `period_return_from_series` in
  `vn_stock.analytics.returns`.
