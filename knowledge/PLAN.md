# LLM Wiki — Work Plan

Focused execution plan for building the Karpathy-style knowledge wiki. Each task has acceptance criteria so you (or a future agent) can pick it up cold and know when it's done.

## Goal

Build a knowledge layer with five tiers — raw immutable sources at the bottom, a queryable wiki + Q&A at the top — covering Vietnamese stock investing and universal financial concepts. Use **local embeddings (bge-m3)** + **Claude Code subscription for synthesis** so steady-state cost is **$0**.

## Target architecture (when complete)

```
┌─────────────────────────────────────────────────────────────┐
│  L5  /wiki — curated MDX pages with citations              │
│      /ask  — RAG-powered Q&A with source links              │
└─────────────────────────────────────────────────────────────┘
                          ▲
┌─────────────────────────────────────────────────────────────┐
│  L4  MCP tool: knowledge_search(query, top_k)               │
│      FastAPI: /api/knowledge/search                         │
│      Hybrid retrieval (dense + sparse)                      │
└─────────────────────────────────────────────────────────────┘
                          ▲
┌─────────────────────────────────────────────────────────────┐
│  L3  Vector index (LanceDB at knowledge/store/)             │
│      bge-m3 embeddings, gitignored                          │
└─────────────────────────────────────────────────────────────┘
                          ▲
┌─────────────────────────────────────────────────────────────┐
│  L2  Chunked text (knowledge/processed/chunks.jsonl)        │
│      Markdown-header split, ~500 tokens, 50 overlap         │
└─────────────────────────────────────────────────────────────┘
                          ▲
┌─────────────────────────────────────────────────────────────┐
│  L1  Raw sources (knowledge/sources/, committed)            │
│      ✅ Phase 1 done — 478 sources ingested                 │
└─────────────────────────────────────────────────────────────┘
```

---

## Status legend

- ✅ Done
- ⏳ Active (in progress)
- 📋 Queued (ready to pick up)
- ❓ Blocked on decision

---

# Phase 1 — Raw sources layer ✅ DONE

Recorded here for completeness. Pipelines and corpus already in place.

- ✅ Directory layout + manifest schema
- ✅ `ingest_rss.py` — VN RSS feeds → 465 articles
- ✅ `ingest_url.py` — generic web + PDF → 13 sources (Buffett, Marks, Damodaran, Wikipedia, World Bank)
- ✅ `ingest_paste.py` — manual paste for paywalled content
- ✅ `_common.py` — shared frontmatter / hash / ticker extraction
- ✅ Curated `registry.json` of starter URLs

---

# Phase 2 — Embed + Index 📋 NEXT

Goal: every source becomes searchable by semantic meaning.

## Task 2.1 — Add dependencies
**What:** Install `sentence-transformers`, `lancedb`, `pylance` to the venv.
**Files:** `requirements.txt`
**Acceptance:**
- `from sentence_transformers import SentenceTransformer; SentenceTransformer("BAAI/bge-m3")` loads without error
- `import lancedb; lancedb.connect("/tmp/test.db")` works
**Estimate:** 5 min (mostly waiting for the 2GB model download)

## Task 2.2 — Build chunker
**What:** Read sources from `knowledge/sources/`, split into ~500-token chunks with 50-token overlap, preserving frontmatter metadata. Markdown-header-aware split so chapters/sections stay intact.
**Files:** `knowledge/pipelines/chunk.py`, writes to `knowledge/processed/chunks.jsonl`
**Schema** for each chunk:
```json
{
  "chunk_id": "<source_id>__<chunk_idx>",
  "source_id": "<source_id>",
  "source_path": "knowledge/sources/...",
  "source_category": "books|articles|...",
  "source_name": "Berkshire Hathaway Letters",
  "title": "2022 Letter to Shareholders",
  "url": "https://...",
  "authors": ["Warren Buffett"],
  "language": "en",
  "tickers_mentioned": [...],
  "chunk_idx": 7,
  "total_chunks": 23,
  "section_heading": "On Float and Intrinsic Value",
  "text": "...500 tokens of content..."
}
```
**Acceptance:**
- Running `python -m knowledge.pipelines.chunk` produces `processed/chunks.jsonl` with one chunk per line
- A 10k-word Buffett letter produces 15-25 chunks
- Re-running is idempotent (uses source content_hash to skip unchanged)
- Total chunks for current corpus: ~2000-4000
**Estimate:** 1-2 hours

## Task 2.3 — Build embedder
**What:** Run bge-m3 on every chunk, write to LanceDB. Hybrid index (dense vectors + sparse BM25).
**Files:** `knowledge/pipelines/embed.py`, writes to `knowledge/store/lance.db/`
**Acceptance:**
- `python -m knowledge.pipelines.embed` embeds all chunks in `processed/chunks.jsonl`
- Stores in LanceDB table `knowledge_chunks` with columns: `chunk_id`, `vector`, `text`, all metadata
- Embedding time on M-series Mac: ~10ms/chunk (~1-2 min total for current corpus)
- Re-running only embeds chunks not yet in the index (idempotent)
- A test query like `model.encode("free cash flow")` returns a 1024-dim vector
**Estimate:** 2-3 hours

## Task 2.4 — Build orchestrator
**What:** Single command that runs ingest → chunk → embed in order. Smart enough to only process what's new.
**Files:** `knowledge/pipelines/refresh.py`
**Acceptance:**
- `python -m knowledge.pipelines.refresh` runs the full pipeline
- Subsequent runs add only new sources (no duplicate work)
- Prints summary: X new sources, Y new chunks, Z new embeddings
**Estimate:** 1 hour

## Task 2.5 — Smoke test retrieval
**What:** Quick CLI script that takes a query and prints top-5 chunks. Verifies the index works before building UI.
**Files:** `knowledge/pipelines/query.py` (one-off utility, not a pipeline)
**Acceptance:**
- `python -m knowledge.pipelines.query "What did Buffett say about float in 2022?"` returns 5 relevant chunks from the 2022 Berkshire letter
- `python -m knowledge.pipelines.query "ROIC vs ROE"` returns Wikipedia + Damodaran chunks
- `python -m knowledge.pipelines.query "VN-Index"` returns VN news articles (hybrid retrieval working — sparse catches the proper noun)
**Estimate:** 30 min

**Phase 2 total estimate: ~5-7 hours of focused work, all in one session.**

---

# Phase 3a — MCP search tool 📋 (after Phase 2)

Goal: Claude Code can query the knowledge base via MCP, with the user's subscription handling synthesis.

## Task 3a.1 — Add `knowledge_search` tool to server.py
**What:** New MCP tool that takes a query + optional filters, returns top-k chunks with citations.
**Files:** `server.py` (add tool definition, routing, implementation)
**Signature:**
```python
knowledge_search(
    query: str,
    top_k: int = 5,
    category: str | None = None,  # filter by source category
    authors: list[str] | None = None,  # filter by author
    language: str | None = None,  # "en", "vi"
    tickers: list[str] | None = None,  # filter to chunks mentioning these tickers
) -> str  # markdown with retrieved chunks + citations
```
**Output format:**
```markdown
## Knowledge Search — "what did Buffett say about float"

### 1. Berkshire Hathaway Letters — 2022 (chunk 7/23)
*Author: Warren Buffett | Source: knowledge/sources/books/...md*

> Float is money we hold but don't own...

### 2. ...
```
**Acceptance:**
- Test via Claude Code: ask "What does Buffett say about float in 2022?" — Claude calls `knowledge_search`, gets chunks, synthesizes answer
- Filters work: query with `language="vi"` only returns Vietnamese chunks
- Performance: <500ms response time for typical query
**Estimate:** 2 hours

## Task 3a.2 — Update CLAUDE.md
**What:** Add `knowledge_search` to the tool table + skill routing.
**Files:** `CLAUDE.md`, possibly new skill `vn-knowledge-librarian`
**Estimate:** 30 min

## Task 3a.3 — Add to vn-equity-analyst workflow
**What:** When analyzing a stock, the equity-analyst skill should optionally call `knowledge_search` to surface relevant essays/letters/papers ("what Mauboussin says about quality businesses", "Buffett's framework for moat assessment").
**Files:** `.agents/skills/vn-equity-analyst/SKILL.md`
**Estimate:** 30 min

**Phase 3a total: ~3 hours. Unlocks "ask anything about my knowledge base" inside Claude Code at zero marginal cost.**

---

# Phase 3b — Web `/ask` page 📋 (after Phase 3a)

Goal: same Q&A capability in the web UI for browser use.

❓ **Open decision:** synthesis path
- **Option A** — chunks-only, no synthesis (free, ship fast). Users see retrieved sources, read them themselves.
- **Option B** — Anthropic API for synthesis (~$0.001/query with Haiku 4.5)
- **Option C** — embedded Claude widget on Anthropic side (not yet available for self-hosted apps)

**Recommendation:** ship A first, layer B on later if usage justifies.

## Task 3b.1 — FastAPI search endpoint
**What:** Mirrors `knowledge_search` but as HTTP.
**Files:** `api.py`
**Acceptance:** `curl /api/knowledge/search -d '{"query": "..."}'` returns JSON with chunks + metadata
**Estimate:** 30 min

## Task 3b.2 — Build `/ask` Next.js page (Option A)
**What:** Chat-like UI: input box at top, retrieved chunks below with source links.
**Files:** `web/app/ask/page.tsx`, nav link in `web/components/NavBar.tsx`
**Acceptance:**
- Ask "What is ROIC?" — see 5 chunks ranked by relevance, each with source title + URL + content
- Click a chunk to expand and read the full source
- Filter sidebar: language, category, author
**Estimate:** 3-4 hours

## Task 3b.3 — Optional: add synthesis (Option B)
**What:** If Anthropic API key is configured, add a synthesized answer at the top of results.
**Files:** `api.py` (new endpoint with synthesis), `web/app/ask/page.tsx`
**Acceptance:** Synthesized answer cites which chunks it used (e.g. "[1]", "[3]") with click-through to source
**Estimate:** 2-3 hours
**Cost:** ~$0.001/query at Haiku 4.5, ~$1-3/month for moderate personal use

---

# Phase 4 — Curated wiki MDX pages 📋

Goal: Hand-curated concept pages that pull together definitions + examples + citations. The "encyclopedia" surface.

## Task 4.1 — Set up MDX rendering in Next.js
**What:** Configure `web/` to render `.mdx` files as pages.
**Files:** `web/next.config.ts`, install `@next/mdx`
**Acceptance:** A test `.mdx` file at `web/app/wiki/test/page.mdx` renders correctly with code blocks and React components
**Estimate:** 1 hour

## Task 4.2 — Wiki index + nav
**What:** `/wiki` shows a categorized list of all pages. Search bar at top.
**Files:** `web/app/wiki/page.tsx`, `web/app/wiki/layout.tsx`
**Acceptance:** Lists all topics grouped by category (concepts, VN-specific, sectors, tools)
**Estimate:** 2 hours

## Task 4.3 — Draft first 10 starter pages
**What:** Most-impactful first batch. Use Claude Code to draft, you review for VN accuracy.
**Suggested starting topics:**
1. `roic.mdx` — what it is, formula, why it matters, link to `/screener`
2. `dcf.mdx` — bull/base/bear scenarios, sensitivity, link to `/position-sizer`'s DCF
3. `earnings-quality.mdx` — FCF/NI, accruals, working capital
4. `position-sizing.mdx` — Kelly, fixed-fractional, ATR stops
5. `falsification-criteria.mdx` — what they are, how to write good ones
6. `foreign-room.mdx` — VN-specific 49%/30% caps + how to compute
7. `parent-vs-consolidated.mdx` — VN reporting quirk that bites readers of broker reports
8. `t-plus-2-5-settlement.mdx` — VN-specific settlement
9. `vn-bank-sector.mdx` — sector dynamics, key tickers, valuation bands
10. `vn-real-estate-sector.mdx` — sector dynamics

**For each page** include:
- 2-3 paragraph definition
- Formula/example where applicable
- 2-3 citations to ingested sources (`<Citation source_id="..."/>` component)
- "Try it" CTA linking to a live tool

**Acceptance:** Each page renders, citations link to ingested sources, CTAs work
**Estimate:** 1-2 hours per page = 10-20 hours total (can split across multiple sessions)

## Task 4.4 — Wiki ↔ Tool cross-linking
**What:** Each tool page in the UI links to the relevant wiki page ("Learn more about ROIC →"). Each wiki page links to the live tool ("Try it on your portfolio →").
**Files:** Updates to existing pages + new wiki pages
**Acceptance:** Every tool has at least one inbound wiki link, every wiki concept page has at least one outbound tool link
**Estimate:** 2 hours

---

# Phase 5 — More ingest pipelines 📋

Goal: expand the raw sources beyond RSS + URL. Each pipeline ships independently.

## Task 5.1 — `ingest_pdf.py` (local files) ✅ DONE
**Shipped:** 2026-06-04
**What works:**
- Single file, glob results, or recursive directory ingestion
- Smart title fallback: PDF metadata → first heading-like body line → filename
- File-path-based dedup hash (re-running same file is idempotent)
- Source files store `original_path` in frontmatter for provenance
- Detects scanned PDFs (low chars/page ratio) and warns instead of silently producing empty content
**Deferred to future:** OCR fallback for scanned PDFs (would need Tesseract); table extraction for financial statements.

## Task 5.1b — `ingest_epub.py` (local EPUB books) ✅ DONE
**Shipped:** 2026-06-04. Reads EPUB → preserves chapter structure as markdown `##` sections.

## Task 5.1c — `ingest_md.py` (local markdown files) ✅ DONE
**Shipped:** 2026-06-04. Parses existing YAML frontmatter (Obsidian/Jekyll) and merges with our standard fields.

## Task 5.1d — `ingest_image.py` (OCR via Tesseract) ✅ DONE
**Shipped:** 2026-06-04. Tesseract-backed OCR for screenshots, scanned pages. Supports `eng+vie` for mixed-language content. Fails gracefully if Tesseract not installed.

## Task 5.1e — `ingest_folder.py` (unified dispatcher) ✅ DONE
**Shipped:** 2026-06-04. Walks a directory, classifies files by extension (PDF, EPUB, MD, image), dispatches each to the right pipeline. Supports `--dry-run`, `--only <types>`, `--ocr` flag. The one-command bootstrap for mixed-format folders.

## Task 5.2 — `ingest_youtube.py` (podcasts/interviews)
**What:** yt-dlp downloads audio, Whisper transcribes, normalize → markdown.
**Files:** `knowledge/pipelines/ingest_youtube.py`
**Dependencies:** `yt-dlp`, `openai-whisper` (or `faster-whisper` for speed)
**Acceptance:**
- `python -m knowledge.pipelines.ingest_youtube <url> --category transcripts --source "Vietnam Innovators"` produces a markdown transcript with timestamps as section headers
- Vietnamese audio transcribes acceptably (Whisper large-v3 handles VN)
**Estimate:** 3-4 hours

## Task 5.3 — `ingest_substack.py` (blogs via RSS)
**What:** Most Substacks expose `/feed` — variant of `ingest_rss.py` but for individual authors.
**Files:** `knowledge/pipelines/ingest_substack.py`
**Acceptance:** `python -m knowledge.pipelines.ingest_substack https://netinterest.substack.com/feed --authors "Marc Rubinstein"` ingests recent posts
**Estimate:** 1-2 hours

## Task 5.4 — `ingest_epub.py` (books)
**What:** EPUB → chapter-split markdown.
**Files:** `knowledge/pipelines/ingest_epub.py`
**Dependencies:** `ebooklib`, `beautifulsoup4`
**Acceptance:** A test EPUB produces one markdown file per chapter under `knowledge/sources/books/<book-slug>/`
**Estimate:** 2-3 hours

---

# Dependencies between phases

```
Phase 1 (raw sources)
   │
   ▼
Phase 2 (embed + index)        ── blocks Phases 3 + 4
   │
   ├──▶ Phase 3a (MCP search tool)    ── self-contained, ships first
   ├──▶ Phase 3b (web /ask page)      ── independent from 3a
   └──▶ Phase 4 (curated wiki)        ── benefits from but doesn't require 3

Phase 5 (more pipelines)       ── independent, ship anytime
```

**Critical path: Phase 2 → Phase 3a.** Everything else is parallelizable after Phase 2.

---

# Open decisions to resolve before starting

## D1 — Embedding model
**Choice:** Local `BAAI/bge-m3` ✅ decided
**Why:** Free, multilingual, runs on Apple Silicon, dense+sparse in one. No Anthropic embeddings API exists.

## D2 — Vector store
**Leaning:** LanceDB
**Alternatives considered:** sqlite-vec (simpler single file), Qdrant (overkill for local)
**Recommendation:** Go with LanceDB unless single-file portability matters more

## D3 — Chunking strategy
**Leaning:** Markdown-header-aware split, 500-token chunks, 50-token overlap
**Tradeoff:** Markdown-header preserves document structure; pure character-split is simpler but cuts mid-paragraph

## D4 — Web UI synthesis path
**Leaning:** Option A first (chunks-only, free)
**Upgrade path:** Add Haiku synthesis when usage justifies (~$0.001/query)

## D5 — When to start Phase 4 (curated wiki)
Two ways:
- **Eager:** Start drafting wiki pages now (Phase 4 in parallel with Phase 2)
- **Lazy:** Wait until search works, then write wiki pages with citations that link to ingested chunks
**Leaning:** Lazy — citations work better when retrieval is already proven

---

# Maintenance and growth

After all phases ship, the steady-state work is:

- **Daily** (auto): RSS ingest cron runs, indexes new VN articles
- **Weekly**: Drop new PDFs into `sources/filings/`, run `refresh.py`
- **Monthly**: Add curated paywalled articles via `ingest_paste.py` (Money Stuff, Net Interest)
- **Quarterly**: Re-evaluate sources — drop low-signal feeds, add new high-signal ones

The compounding value: each new source improves every future query, and the wiki gets denser over time.

---

# Quick-start template for the next session

If you (or an agent) resume cold:

```bash
# 1. Read this file + GUIDE.md
# 2. Verify current state
.venv/bin/python -m knowledge.pipelines.ingest_rss --stats
ls knowledge/sources/{articles,books,blogs,papers,regulatory}/ | wc -l

# 3. Start Phase 2 Task 2.1
.venv/bin/pip install sentence-transformers lancedb pylance
.venv/bin/python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3'); print('OK')"

# 4. Then Tasks 2.2 → 2.5 in order, working off this file
```

---

*Last updated: 2026-06-04*
