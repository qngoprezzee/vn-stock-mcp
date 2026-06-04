# Knowledge Layer

A Karpathy-style layered knowledge system for Vietnamese stock investing. Raw sources are immutable and version-controlled; everything downstream is regenerable.

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 5 — Wiki UI                                              │
│  Next.js MDX pages + /ask endpoint for grounded Q&A             │
└─────────────────────────────────────────────────────────────────┘
              ▲                              ▲
              │ synthesis                    │ retrieve
┌──────────────────────────┐   ┌─────────────────────────────────┐
│  Layer 4 — Retrieval     │   │  Layer 4b — LLM synthesis       │
│  Hybrid BM25 + vector    │   │  with citations to source IDs   │
└──────────────────────────┘   └─────────────────────────────────┘
              ▲
┌─────────────────────────────────────────────────────────────────┐
│  Layer 3 — Vector Index (gitignored, regeneratable)             │
│  store/ → LanceDB or sqlite-vec                                 │
└─────────────────────────────────────────────────────────────────┘
              ▲
┌─────────────────────────────────────────────────────────────────┐
│  Layer 2 — Processed (gitignored, regeneratable)                │
│  processed/chunks.jsonl + processed/index.db                    │
└─────────────────────────────────────────────────────────────────┘
              ▲
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1 — Raw Sources (committed, append-only, immutable)      │
│  sources/{articles,books,blogs,filings,transcripts,papers,...}  │
└─────────────────────────────────────────────────────────────────┘
```

## Source types

| Folder | Type | Ingest method |
|---|---|---|
| `sources/articles/` | News articles (CafeF, VnEconomy, etc.) | `pipelines/ingest_rss.py` (auto, daily) |
| `sources/blogs/` | Substacks, blog posts (Dragon Capital, Indochina, etc.) | RSS or manual drop |
| `sources/books/` | Investing classics (Damodaran, Graham, Mauboussin) | Manual chapter excerpts |
| `sources/filings/` | Annual reports, broker research PDFs | `pipelines/ingest_pdf.py` (manual trigger) |
| `sources/transcripts/` | Podcast/interview transcripts | `pipelines/ingest_youtube.py` (yt-dlp + Whisper) |
| `sources/papers/` | Academic papers on VN markets, EM finance | Manual drop + PDF extract |
| `sources/regulatory/` | SBV decisions, GSO statistics, SSC disclosures | Manual + scheduled scrape |

## Frontmatter schema

Every source file is a markdown document with YAML frontmatter:

```markdown
---
id: cafef_2026-06-04_abc12345
source: "CafeF - Thị trường CK"
source_url: https://cafef.vn
url: https://cafef.vn/...
title: "VN-Index giảm 7 phiên liên tiếp"
pub_date: 2026-06-04T08:00:00+07:00
ingested_at: 2026-06-04T20:00:00+07:00
content_hash: abc12345...
language: vi
type: article
tickers_mentioned: [FPT, VCB]
authors: []
full_text_fetched: false
---

Article body in markdown...
```

Fields like `tickers_mentioned`, `authors`, `language` are used as metadata filters at retrieval time.

## Manifest

`manifest.json` is the index of everything ingested. Used to dedupe and track ingest runs.

```json
{
  "version": 1,
  "last_run": "2026-06-04T20:00:00+07:00",
  "stats": { "total_sources": 0, "by_type": {} },
  "ingested": {
    "cafef_2026-06-04_abc12345": {
      "source": "CafeF - Thị trường CK",
      "url": "...",
      "ingested_at": "..."
    }
  }
}
```

## Karpathy-style invariants

1. **Layer 1 is immutable**. Source files never change after ingestion. To improve chunking or embeddings, regenerate Layer 2+ from Layer 1.
2. **Citations preserved**. Every chunk knows its source ID. Every wiki page and every Q&A answer cites source IDs.
3. **Curate ruthlessly**. Better to ingest 50 articles from 5 high-signal authors than 5000 articles scraped wholesale.
4. **Reference vs. synthesis**. Deterministic facts (P/E by sector, settlement rules) live in lookup tables, not LLM context. Only fuzzy reasoning goes through the LLM.
5. **Hybrid retrieval**. BM25 catches proper nouns (ticker symbols, person names) that vector search misses. Combine both.

## What's gitignored

- `processed/` — chunked + embedded representations (regen from sources)
- `store/` — vector index files (regen)
- `*.embedding` files (regen)

What's committed: everything in `sources/`, the `manifest.json`, all pipeline scripts, and curated `wiki/` content.

## Phased build

- **Phase 1** ✅ — Raw sources layer + RSS ingest pipeline
- **Phase 2** — Chunk + embed + LanceDB vector store
- **Phase 3** — `/ask` endpoint with hybrid retrieval + LLM synthesis
- **Phase 4** — Curated wiki MDX pages with citations
- **Phase 5** — Add PDF, YouTube, manual book pipelines

## Running

```bash
# Ingest VN RSS feeds (uses feeds defined in server.py)
.venv/bin/python -m knowledge.pipelines.ingest_rss

# Ingest a URL or curated registry
.venv/bin/python -m knowledge.pipelines.ingest_url https://... --category blogs
.venv/bin/python -m knowledge.pipelines.ingest_url --registry knowledge/registry.json

# Ingest a local PDF (book, broker report, paper, etc.)
.venv/bin/python -m knowledge.pipelines.ingest_pdf ~/Downloads/intelligent_investor.pdf \
    --category books --source "The Intelligent Investor" --authors "Benjamin Graham" --pub-date 1949

# Ingest a local EPUB
.venv/bin/python -m knowledge.pipelines.ingest_epub ~/Downloads/the-outsiders.epub --category books

# Ingest local markdown files (single file or directory recursively)
.venv/bin/python -m knowledge.pipelines.ingest_md ~/Obsidian/Investing/ --category articles

# Ingest images via OCR (requires Tesseract: brew install tesseract tesseract-lang)
.venv/bin/python -m knowledge.pipelines.ingest_image ~/Downloads/screenshot.png \
    --category articles --source "Bloomberg screenshot" --lang eng

# ★ Unified folder dispatcher — handles PDF + EPUB + MD + images in one walk
.venv/bin/python -m knowledge.pipelines.ingest_folder ~/Documents/investing-library/ \
    --category books --ocr --ocr-lang "eng+vie"
.venv/bin/python -m knowledge.pipelines.ingest_folder ~/Documents/library/ --dry-run

# Ingest paywalled article via copy-paste
pbpaste | .venv/bin/python -m knowledge.pipelines.ingest_paste \
    --title "..." --source "Bloomberg — Matt Levine" --url "https://..." --authors "Matt Levine"

# Stats across all sources
.venv/bin/python -m knowledge.pipelines.ingest_rss --stats
```
