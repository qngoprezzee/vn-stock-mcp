# VN Stock Project — Roadmap

A living document tracking what's built, what's next, and the decisions behind both. Update as work progresses.

---

## Current state — one-line summary

**Working system**: MCP server (25 tools) + Next.js web UI + Karpathy-style knowledge layer with 478 ingested sources. Ready to embed + index + add RAG search.

---

## Completed milestones

### ✅ MCP server foundation (initial)
- 13 base tools (data, news, financial statements, technical analysis, PDF reader)
- 5 agent skills (equity analyst, technical analyst, portfolio manager, news analyst, report reader)
- Subprocess isolation for vnstock rate-limit resilience

### ✅ Investing curriculum mapping (4 phases)
Mapped every tool to a phase of a structured investing curriculum:
- Phase 1 Foundation — data + ratios + macro
- Phase 2 Analytical — DCF, earnings quality, peer comparison
- Phase 3 Execution & risk — position sizing, stop-loss, thesis writing
- Phase 4 Mastery — performance review, decision log, pattern recognition

### ✅ 10 improvement ideas all built
1. `get_foreign_flow` — foreign ownership, room, daily net buy/sell
2. `get_earnings_quality` — FCF/NI, accruals, OCF margin, WC discipline, OCF consistency
3. `review_performance` — win rate, expectancy, triage verdict, loss clustering
4. `get_vn_macro_indicators` — World Bank API for GDP/CPI/rates
5. Watchlist (`manage_watchlist` + `check_watchlist`) — RSI, MA50, daily move triggers
6. `stress_test_portfolio` — -10/-20/-30% shocks with sector betas
7. `get_quality_score` — 0-100 composite from ROIC, FCF/NI, debt, growth, margins
8. Caching layer — file-based, TTL per function (24h statements, 5min prices)
9. Fixed `_compare_stocks` subprocess bypass + removed dead `_vnstock_call`
10. Pre-mortem + bias fields added to `save_investment_thesis`

### ✅ Web UI (Next.js 16 + FastAPI)
- `api.py` — FastAPI HTTP wrapper exposing same tool functions as MCP
- 5 pages built: Dashboard, Quality Screen, Position Sizer, Performance, New Thesis
- Tailwind 4 + shadcn-style design, react-markdown for tool output, TanStack Query
- Verified end-to-end with Playwright (real browser tests)
- Both servers share `.cache/`, `.watchlist.json`, `theses/`, `decisions/LOG.md`

### ✅ Documentation layer
- `README.md` — project overview, install, features
- `GUIDE.md` — workflow guide (daily, position lifecycle, weekly, monthly, quarterly)
- `CLAUDE.md` — agent routing + tool reference
- `web/README.md` — frontend run instructions
- `knowledge/README.md` — knowledge layer architecture

### ✅ Knowledge layer — Phase 1 (raw sources)
Karpathy-style layered system. Raw sources are immutable + version-controlled; derived layers regenerable.

- Directory structure: `knowledge/sources/{articles,books,blogs,filings,transcripts,papers,regulatory}/`
- Manifest at `knowledge/manifest.json` tracks every ingested source with content hash for dedup
- **Three ingest pipelines built**:
  - `ingest_rss.py` — auto-pulls Vietnamese RSS feeds (CafeF, VnEconomy, VnExpress, VietStock)
  - `ingest_url.py` — generic web ingestion with trafilatura (HTML) + pymupdf (PDF)
  - `ingest_paste.py` — manual paste for paywalled content (Bloomberg, FT, WSJ)
- **478 sources ingested** as of 2026-06-04:
  - 465 Vietnamese articles
  - 5 Berkshire Hathaway letters (2019-2023, ~10k words each)
  - 3 Howard Marks memos
  - 1 Damodaran paper (Valuing Young Growth Companies, 24k words)
  - 3 Wikipedia entries
  - 1 World Bank Vietnam page

---

## Active work — immediate next session

### Knowledge Layer Phase 2 — Embed + Index (free, local)

**Architectural decision made**: Use local `BAAI/bge-m3` instead of OpenAI embeddings.
- Free (no API)
- Multilingual (excellent on Vietnamese)
- 8K context, dense+sparse+ColBERT in one model
- Runs on Apple Silicon via MPS backend
- ~2GB one-time download

**Steps planned:**
1. Add to `requirements.txt`: `sentence-transformers`, `lancedb`, `pylance`
2. Build `knowledge/pipelines/chunk.py` — splits each source into ~500-token chunks with overlap, tags with parent metadata
3. Build `knowledge/pipelines/embed.py` — runs bge-m3 over all chunks, writes to LanceDB at `knowledge/store/lance.db`
4. Build `knowledge/pipelines/refresh.py` — orchestrator that runs ingest → chunk → embed in sequence (idempotent, only processes new sources)

**Cost / time estimate:**
- One-time model download: 2GB, ~5min
- Embedding 478 sources (~150k tokens total): ~5min on M-series Mac
- Subsequent updates: ~5sec per new source

### Knowledge Layer Phase 3 — Search + Synthesis

Two paths planned (no overlap):

**3a — MCP tool (free with Claude subscription):**
- Add `knowledge_search(query, top_k=5)` tool to `server.py`
- Returns: top-k relevant chunks with source citations
- Claude Code (the user's subscription) does the synthesis
- Total cost: $0

**3b — Web UI `/ask` page:**
- FastAPI route `/api/knowledge/ask` — runs retrieval, returns chunks
- Next.js page `/ask` — chat UI showing retrieved chunks
- Synthesis options:
  - Skip synthesis, show chunks only (free, useful as "search with snippets")
  - Add tiny Anthropic API key for Haiku synthesis (~$0.001/query)
  - Defer until later

---

## Backlog (organized by area)

### Knowledge layer Phase 4 — Curated wiki
- Set up `web/app/wiki/[slug]/page.tsx` for MDX rendering
- LLM-assisted drafting (Claude Code drafts, you review/merge)
- ~30 starter topics covering:
  - VN-specific: foreign room, T+2.5 settlement, parent vs consolidated, HOSE vs HNX lot sizes, ESOP dilution
  - Concepts: ROIC, DCF, accruals, working capital cycle, moat assessment, position sizing
  - Sectors: banking dynamics, real estate, tech services, steel cyclicality
- Each wiki page links to both:
  - Live tool (e.g. ROIC page → `/screener` CTA)
  - Source citations (chunks from Buffett, Damodaran, Wikipedia)

### Knowledge layer Phase 5 — More pipelines
- `ingest_pdf.py` (standalone) for local PDF files (annual reports, broker research, books)
- `ingest_youtube.py` — yt-dlp + Whisper for podcast transcripts (Vietnam Innovators, Vietnam Briefing, etc.)
- `ingest_substack.py` — RSS-based for blogs (Net Interest, Doomberg, Money Stuff free archive)
- `ingest_epub.py` — for ebooks (investing classics in EPUB)

### Web UI — additional pages
- **Stress test page** — paste holdings, see -10/-20/-30% scenarios with sector beta breakdown
- **Watchlist page** — manage tickers + run `check_watchlist` scan with visual triggers
- **Decision log page** — quick form to record BUY/SELL/ADD/TRIM
- **Stock detail page** at `/stock/[ticker]` — combined overview + technical + DCF + foreign flow + analyst news
- **Chart components** (Recharts) — equity curve on performance, sector heatmap, valuation bands

### Knowledge sources to add
**Free auto-pull (URL ingestion):**
- More Damodaran papers (paths changed since 2009 — find current URLs)
- Buffett letters 2014-2018 (PDF)
- Munger speeches (Daily Journal, Caltech)
- All Howard Marks memos archive (~50 memos)
- Mauboussin essays via Columbia GSB archive
- BIS quarterly review (macro context)

**Manual paste (paywalled high-value):**
- Matt Levine's Money Stuff (newsletter free, web paywalled)
- Marc Rubinstein — Net Interest (free posts)
- Byrne Hobart — The Diff
- Aswath Damodaran's blog posts
- Cliff Asness — AQR research

**Manual book chapters (fair-use excerpts):**
- *Security Analysis* — Graham & Dodd (key chapters)
- *Margin of Safety* — Klarman (rare, but excerpts circulate)
- *Poor Charlie's Almanack* — Munger
- *The Outsiders* — Thorndike (8 case studies)
- *100 Baggers* — Mayer
- *Capital Returns* — Marathon Asset Management

### Broken / needs cleanup
- Some RSS feeds 404 in `server.py`:
  - `CafeF - Đầu tư` → URL changed
  - `Tin Nhanh CK` → URL changed (HTTPS redirect issue)
- Báo Đầu tư feeds have malformed XML preamble (fix with lenient parser)
- Vietnam Investment Review feed has invalid XML token
- Cleanup: refactor `ingest_rss.py` to use `knowledge/pipelines/_common.py` (currently has duplicated logic)

### Investopedia / Cloudflare-blocked sources
Investopedia returns 403/CAPTCHA via plain HTTP. Options:
1. Use Wikipedia for definitions (already in registry, works)
2. Use `ingest_paste.py` for select Investopedia entries
3. Add Playwright-based fetcher for stubborn sites (overkill for now)

---

## Open decisions

### 1. Synthesis path for web UI
Three options:
- **A**: Skip synthesis in web UI, only show retrieved chunks (free, ship now)
- **B**: Add Anthropic API key + use Haiku 4.5 for synthesis (~$0.001/query, ~$1-3/month for personal use)
- **C**: Embed an "open in Claude Code" deep link that copies the question to clipboard

**Leaning A** for MVP, **B** later if the search becomes the daily driver.

### 2. Vector store choice
- **LanceDB** (recommended) — file-based, no server, hybrid search built-in
- **sqlite-vec** — simpler if we want everything in one SQLite file
- **Qdrant** — overkill for local use, requires Docker

**Leaning LanceDB.**

### 3. Chunking strategy
- **Recursive character split** (langchain default) — simple, works for most content
- **Markdown header split** — respects document structure, better for Buffett letters / Damodaran papers
- **Semantic chunking** (embed sentences, group by similarity) — more accurate but slower

**Leaning markdown-header split with 500-token chunks + 50-token overlap.**

### 4. Wiki page generation
- **Pure manual** — slow but reliable
- **LLM drafts → human review** (recommended) — fast iteration
- **Auto-generated continuously** — risky, hallucinates VN-specific facts

**Leaning LLM-drafts-then-review.**

---

## Longer-term ideas (not blocking)

### Product
- **Mobile-first Zalo/Telegram bot** — `/score FPT`, `/sizing FPT 500M`, `/review` — feels native for VN audience
- **Browser extension** — capture current page → send to local knowledge layer (solves the paywall problem at scale)
- **Email digest** — daily summary of watchlist triggers + thesis breakdowns
- **Sharing layer** — public-readable thesis links (anonymous URLs)

### Engineering
- **Tests** — pytest for tool functions, Playwright for UI smoke tests
- **CI/CD** — GitHub Actions to run RSS ingest daily + verify cache health
- **Observability** — log all tool calls + response times, find slow paths
- **Better PDF extraction** — currently pymupdf text only; add table extraction for financials in annual reports
- **Multi-user mode** — auth + per-user `.watchlist.json`, `decisions/LOG.md`

### Data
- **Vietnamese tokenizer for ticker detection** — current regex misses some (proper nouns)
- **Sector beta from historical correlation** — replace hardcoded heuristics in `stress_test_portfolio`
- **Real-time foreign flow time-series** — scrape CafeF or VietStock historical foreign trade
- **Sentiment scoring on news headlines** — feed into watchlist triggers

### Research
- **Backtest framework** — replay decision log against historical prices, compute alpha
- **Factor decomposition** — analyze if your edge comes from sector picks, sizing, or timing
- **Quality scoring across all VN-listed stocks** — bulk run `get_quality_score` over HOSE universe, surface top 50

---

## Conventions for this document

- ✅ = completed; date in section
- ⏳ = active work this session
- 📋 = backlog with rough priority
- Cross out items only after they ship; don't delete completed work (history matters)
- When you change a decision in "Open decisions", move the old rationale to a `## Decision log` section at the bottom
- Update "Current state" line at top whenever a major milestone ships

---

*Last updated: 2026-06-04. Update this footer when you make material changes.*
