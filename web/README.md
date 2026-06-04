# VN Stock — Web UI

Next.js 16 + TypeScript + Tailwind 4 frontend for the VN Stock MCP project. Calls the FastAPI HTTP wrapper (`api.py`) at the project root.

## Architecture

```
┌─────────────────┐       HTTP        ┌─────────────────────┐
│  Next.js (3000) │ ────────────────▶ │  FastAPI (8000)     │
│  React UI       │  /api/stock/...   │  api.py             │
└─────────────────┘                   │       ↓             │
                                      │   server.py funcs   │
                                      │   (same as MCP)     │
                                      └─────────────────────┘
```

Both surfaces share the cache (`.cache/`), watchlist (`.watchlist.json`), theses (`theses/`), and decision log (`decisions/LOG.md`). Edits made through Claude Code via MCP are visible in the web UI and vice versa.

## Running locally

You need **two processes**: the FastAPI backend and the Next.js dev server.

### Terminal 1 — FastAPI (from project root)

```bash
cd /Users/quoc.ngo/sideproject/vn-stock-mcp
.venv/bin/python -m uvicorn api:app --reload
```

Serves on `http://127.0.0.1:8000`. The dashboard will show a clear "API offline" banner if this isn't running.

### Terminal 2 — Next.js (from `web/`)

```bash
cd /Users/quoc.ngo/sideproject/vn-stock-mcp/web
npm run dev
```

Open `http://localhost:3000`.

## Pages

| Route | What it does |
|---|---|
| `/` | Dashboard — market overview + economy headlines |
| `/screener` | Quality score multiple tickers, ranked |
| `/position-sizer` | ATR-based stop-loss + sizing calculator |
| `/thesis` | Form for writing a new investment thesis with pre-mortem fields |
| `/performance` | Live metrics from `decisions/LOG.md` + full review verdict |

## Environment

Override the API URL if needed:

```bash
# web/.env.local
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
```

## Tech stack

- **Next.js 16** App Router (Turbopack default, async params)
- **TypeScript** strict
- **Tailwind CSS 4** with `@tailwindcss/typography` for markdown rendering
- **TanStack Query** for server state caching
- **react-markdown** + `remark-gfm` for rendering tool output (tables, lists)
- **lucide-react** for icons

## Adding a new page

1. Add a route handler in `api.py` if it doesn't exist yet
2. Add the typed client function in `lib/api.ts`
3. Create `app/<route>/page.tsx` with `"use client"` and `useQuery`/`useMutation`
4. Add a nav entry in `components/NavBar.tsx`

The pattern: most tool responses are markdown — render with `<MarkdownBlock text={data.text} />`. For data that needs charts (like `/performance`), use the `*-raw` JSON endpoints instead.
