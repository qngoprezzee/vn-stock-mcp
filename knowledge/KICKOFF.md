# Knowledge Layer — Kickoff Projects

What to actually *do* with the ingested corpus. Pick any one when you have a free session.

`PLAN.md` covers building the infrastructure (embeddings, search, more pipelines). This file is about extracting value from the data we already have. Most ideas here don't need Phase 2 — they work on the raw frontmatter and markdown bodies we've already ingested.

---

## Context (as of 2026-06-04)

**Corpus on hand:** 478 sources
- 465 Vietnamese articles (RSS — daily refresh)
- 5 Berkshire Hathaway letters (Buffett, 2019-2023, ~10k words each)
- 3 Howard Marks Oaktree memos
- 1 Damodaran paper (Valuing Young Growth Companies, 24k words)
- 3 Wikipedia entries (FCF, DCF, Margin of Safety)
- 1 World Bank Vietnam overview

**Ticker coverage** (auto-detected from text): FPT 7, VIC 6, VCB 6, HCM 6, OCB 4, TCB 4, CTG 4, BID 4, MBB 3, VPB 3, VND 3, GMD 2, NLG 2, HOSE 11, HNX 10

**Pipelines available to add more:** `ingest_rss`, `ingest_url`, `ingest_pdf`, `ingest_epub`, `ingest_md`, `ingest_image` (OCR), `ingest_folder` (dispatcher), `ingest_paste` (paywalled content)

---

## Status legend

- ✅ Done
- ⏳ Active (in progress)
- 📋 Queued (ready to pick up)
- 💡 Idea (specced but not committed to)

---

# Tier 1 — Immediate, no LLM needed

These run on raw metadata/text. No Claude API, no Claude Code synthesis. Pure Python on what's already on disk.

## K1 — Per-ticker hub notes 📋 RECOMMENDED FIRST
**What:** For every ticker found in any source's `tickers_mentioned`, generate `knowledge/hubs/<TICKER>.md` listing all related sources, grouped by category, sorted by recency.
**Why:** Makes the corpus instantly navigable in Obsidian. Click `[[FPT]]` → see all articles + any Buffett mentions + analyses. Foundation for per-ticker analysis pages.
**Files:**
- `knowledge/pipelines/build_hubs.py` — generates one md per ticker
- `knowledge/hubs/` — output directory (gitignored except a stub README)
**Acceptance:**
- Running the script produces ~50 hub files for tickers in the corpus
- Each hub has frontmatter (so it's part of the knowledge layer), a summary header, and tables grouped by source category
- Re-running is idempotent (overwrites cleanly)
- In Obsidian, `[[FPT]]` opens the FPT hub and shows backlinks from every source mentioning it
**Effort:** ~30 min
**Depends on:** Nothing — works on existing manifest + frontmatter
**Bonus:** Add wikilink enrichment in `_common.py` so ingested sources auto-link to ticker hubs

## K2 — Coverage / gap report 📋
**What:** Analyze the corpus, surface what's well-covered vs. gaps. Example output: "5 Buffett letters / 0 Lynch / 0 Klarman / 0 Munger; 99% news / 1% analysis; Sectors: banking 14×, real estate 8×, steel 0×."
**Why:** Tells you what to ingest next. Should run as a quick sanity check before each big ingest session.
**Files:** `knowledge/pipelines/corpus_report.py`
**Acceptance:**
- `python -m knowledge.pipelines.corpus_report` prints a structured report
- Sections: by author, by source, by category, by date, by sector (inferred from tickers), top gaps (named-author counts vs an aspirational list)
- Optional `--markdown` flag writes the report to `knowledge/reports/coverage_<date>.md`
**Effort:** ~45 min
**Depends on:** Nothing

## K3 — Author + source distribution analysis 📋
**What:** Who do you actually read? Which sources dominate by word count? Visualize concentration (mostly VN news? Buffett-heavy?). 
**Why:** Reveals reading bias — e.g. if 95% of your knowledge base is news headlines, you have macro-overweight bias and not enough deep analysis.
**Files:** Folded into K2's `corpus_report.py` (sub-section)
**Effort:** ~15 min once K2 exists

## K4 — Reading queue / unread tracker 💡
**What:** Mark sources as read/unread. Show "you have 12 unread Howard Marks memos."
**Approach:** Add `read: true|false` to frontmatter. DataView plugin in Obsidian renders the queue automatically.
**Files:**
- Modify `write_source()` in `_common.py` to add `read: false` default
- Document the Obsidian DataView query in `knowledge/README.md`
**Effort:** ~30 min
**Notes:** Obsidian-native solution — no script needed if DataView is installed

## K5 — Concept frequency map 💡
**What:** Count how often investing concepts appear across the corpus: "earnings quality" 12×, "moat" 47×, "intrinsic value" 89×, "free cash flow" 134×. Surfaces what's well-discussed vs. underweighted.
**Files:** `knowledge/pipelines/concept_frequency.py`
**Effort:** ~30 min

---

# Tier 2 — With Claude Code synthesis (free via subscription)

These use Claude Code's LLM to synthesize across the corpus. Each one is a chat prompt + maybe a small MCP tool to feed Claude relevant chunks.

## K6 — Daily Morning Brief ✅ SHIPPED 2026-06-04

**What:** Each morning at ~7:00 VNT, a script gathers today's signal and produces a 2-3 paragraph briefing in `knowledge/briefs/<date>.md`. Open it on phone (via Obsidian Sync) or desktop to start the day.

**Why:** Highest-leverage daily habit. Forces the corpus into your morning routine. Demonstrates the full pipeline: ingest → curate → synthesize. The "wow" output.

**Inputs gathered by the script:**
1. **Today's articles** — last 24h from `knowledge/sources/articles/` (filter by `pub_date` or `ingested_at`), keep top 10 by recency + source rank
2. **Watchlist scan** — call existing `_check_watchlist()` function, capture any RSI/MA50/>5% triggers
3. **Market snapshot** — VN-Index, top movers, foreign net flow (call `_get_market_overview`)
4. **Historical principle** — find one passage from `sources/books/` or `sources/blogs/` (Buffett, Marks, Damodaran) whose theme matches today's dominant headline (simple keyword match in MVP; semantic match after Phase 2)

**Synthesis approach (Claude Code, free):**
The script writes a structured prompt file at `knowledge/briefs/_pending_<date>.md` containing the gathered inputs. Run `/morning-brief` slash command (or invoke `vn-morning-brief` skill) and Claude Code synthesizes the briefing into `knowledge/briefs/<date>.md`.

**Files to build:**
| File | Purpose |
|---|---|
| `knowledge/pipelines/daily_brief.py` | Gathers inputs, writes the prompt skeleton |
| `.agents/skills/vn-morning-brief/SKILL.md` | Synthesis rules (tone, length, citation format) |
| `server.py` | Optional: new MCP tool `gather_daily_brief_inputs()` returns the JSON package |
| `knowledge/briefs/` | Output directory (gitignored except a stub README) |

**CLI:**
```bash
# Gather + render skeleton (~10s)
.venv/bin/python -m knowledge.pipelines.daily_brief
# Output: knowledge/briefs/_pending_2026-06-04.md (raw inputs ready for synthesis)

# Then in Claude Code: /morning-brief
# → reads the _pending file, writes knowledge/briefs/2026-06-04.md
```

**Output schema** (`knowledge/briefs/<date>.md`):
```markdown
---
date: 2026-06-04
brief_type: morning
verdict: 🟢 BULLISH | 🟡 NEUTRAL | 🔴 BEARISH
citations: [<source_id>, <source_id>, ...]
---

## Today's Market — 2026-06-04

[Paragraph 1: market state — index moves, foreign flow, top movers, why it matters]

[Paragraph 2: watchlist triggers + one stock-specific narrative thread from today's news]

[Paragraph 3: one historical principle (Buffett/Marks/Damodaran) connecting to today's theme,
with a 1-2 sentence quote]

---
**Today's reading list:** [3-5 article links with one-sentence "why care"]
**Watchlist alerts:** [trigger summary]
```

**Acceptance criteria:**
- [ ] Running the script produces a `_pending_<date>.md` with all 4 input categories populated
- [ ] After Claude Code synthesis, `knowledge/briefs/<date>.md` exists with 2-3 paragraphs + citations
- [ ] Each cited article links back to its source file with a wikilink
- [ ] The "historical principle" actually relates thematically (manual review on 3 days; if hit rate < 60%, switch keyword matching for semantic — needs Phase 2)
- [ ] Total runtime <30 seconds for input-gathering phase

**Effort:** ~3 hours (input gathering ~1hr, skill design ~1hr, polish ~1hr)
**Depends on:** Nothing (gracefully degrades if Phase 2 not done — keyword match instead of semantic)
**Schedule:** macOS launchd plist or `cron` for 7:00 VNT daily. Could also live as a `manage_watchlist`-style MCP tool you trigger manually.

---

## K7 — Concept extraction across Buffett letters ✅ SHIPPED 2026-06-04

**What:** For a given concept (e.g. "intrinsic value"), scan all 5 ingested Berkshire letters, extract every passage where Buffett discusses it, and produce a curated `knowledge/wiki/buffett-concepts/<concept>.md` page with verbatim quotes, year references, and a synthesized takeaway.

**Why:**
- Builds the foundation for the wiki MDX pages (Phase 4) — you ship a Buffett concept page per session
- Creates your personal "Best of Buffett" topical index — far more useful than re-reading 50k words to find one idea
- Pattern extends: same approach for Damodaran, Marks, Mauboussin once their corpus is large enough

**Inputs:** 
- `knowledge/sources/books/*berkshire*.md` (5 files, ~50k words combined)
- A concept name + synonym list (e.g. `intrinsic value` + ["intrinsic worth", "intrinsic business value"])

**Approach (no embeddings needed):**
1. Script greps each letter for the concept + synonyms with 3 paragraphs of surrounding context
2. Writes matched passages to a temp file (`knowledge/wiki/buffett-concepts/_pending_<concept>.md`)
3. Claude Code reads the temp file via `vn-concept-extractor` skill and synthesizes:
   - 1 paragraph: "Buffett's definition of <concept>"
   - 1 paragraph: "How his thinking evolved across the letters" (if observable)
   - 4-6 verbatim quotes with letter year + context
   - 1 sentence: "How to apply this to VN equity research"

**Files to build:**
| File | Purpose |
|---|---|
| `knowledge/pipelines/extract_concept.py` | grep + context window + temp file write |
| `.agents/skills/vn-concept-extractor/SKILL.md` | Synthesis rules |
| `knowledge/wiki/buffett-concepts/<concept>.md` | Output per concept |

**CLI:**
```bash
# Gather passages
.venv/bin/python -m knowledge.pipelines.extract_concept \
    --author buffett \
    --concept "intrinsic value" \
    --synonyms "intrinsic worth,intrinsic business value"
# Output: knowledge/wiki/buffett-concepts/_pending_intrinsic-value.md

# In Claude Code: /extract-concept intrinsic-value
# → Writes knowledge/wiki/buffett-concepts/intrinsic-value.md
```

**Starter concepts (priority order):**
1. Intrinsic value
2. Owner earnings
3. Float
4. Capital allocation
5. Margin of safety
6. Look-through earnings
7. Moat / competitive advantage
8. Share repurchases
9. Retained earnings
10. GAAP vs economic reality

**Output schema** (`knowledge/wiki/buffett-concepts/<concept>.md`):
```markdown
---
concept: intrinsic value
synthesized_by: claude-code
source_ids: [<buffett_2019_id>, <buffett_2020_id>, ...]
years_covered: [2019, 2020, 2021, 2022, 2023]
---

# Intrinsic Value — Buffett's View

## Definition
[1 paragraph synthesized from quotes]

## Evolution across letters
[1 paragraph, if observable]

## Selected passages

### 2019 Letter
> "[verbatim quote]"

### 2022 Letter
> "[verbatim quote]"

[... 4-6 total]

## Applied to VN equity research
[1-2 sentence bridge — e.g. "For VN banks, intrinsic value tracks book value × ROE/cost-of-equity ratio more cleanly than P/E because..."]

## See also
- [[buffett-concepts/owner-earnings]]
- [[wiki/dcf]]
```

**Acceptance criteria:**
- [ ] Running `extract_concept` for "intrinsic value" produces a `_pending_*.md` with all relevant passages from all 5 letters
- [ ] After Claude Code synthesis, the final MDX page has at least 4 verbatim quotes with year refs
- [ ] Total of 10 starter concepts produced over 5-10 sessions
- [ ] Each concept page links to related concepts (cross-linking)

**Effort:** ~30 min per concept × 10 concepts = ~5 hours total, spread across sessions
**Depends on:** Nothing
**Bonus:** Same approach scales to Damodaran (1 paper but big), Marks (3 memos), Mauboussin (when ingested).

---

## K8 — Cross-reference engine ✅ SHIPPED 2026-06-04

**What:** Given a topic and a list of authors, retrieve passages from each author's corpus on that topic and synthesize a comparative analysis. "What does Buffett vs Marks vs Damodaran say about cyclicality?"

**Why:** Teaches the *meta-skill* — recognizing when investing legends disagree. Most beginner content presents them as a unified canon. The truth: they emphasize different things. Knowing where they differ (e.g. Marks emphasizes cycles where Buffett emphasizes durability) makes you a sharper thinker.

**Inputs:**
- Topic string (e.g. "cyclicality")
- Author list (e.g. ["Warren Buffett", "Howard Marks", "Aswath Damodaran"])
- Optional synonyms / keyword variants

**Approach:**
1. MCP tool `compare_authors_on(topic, authors)` retrieves passages from each author's corpus (grep-based MVP, semantic search after Phase 2)
2. Returns a structured JSON with passages grouped by author
3. Claude Code synthesizes comparison with explicit "where they agree", "where they differ", "implications for your thesis"

**Files to build:**
| File | Purpose |
|---|---|
| `server.py` | New MCP tool `compare_authors_on` |
| `.agents/skills/vn-comparative-research/SKILL.md` | Synthesis rules + output format |
| `knowledge/pipelines/find_passages.py` | Reusable: search corpus by author + keyword |
| `knowledge/wiki/comparisons/<topic>.md` | Output per comparison |

**MCP tool signature:**
```python
compare_authors_on(
    topic: str,                          # "cyclicality"
    authors: list[str],                  # ["Warren Buffett", "Howard Marks", ...]
    keywords: list[str] | None = None,   # Synonyms / variants; auto-derived if None
    context_lines: int = 5,              # Paragraphs of context around each match
) -> str  # markdown with passages grouped by author + citations
```

**Output schema** (`knowledge/wiki/comparisons/<topic>.md`):
```markdown
---
topic: cyclicality
authors: [Warren Buffett, Howard Marks, Aswath Damodaran]
source_ids: [...]
---

# Cyclicality — A Cross-Reference

## Where they agree
- All three accept that economic cycles exist and matter
- All three reject pure top-down macro forecasting

## Where they disagree
- **Marks**: cycles are the *primary* mental model — your edge is reading where you are in the cycle
- **Buffett**: cycles matter only at extremes; in normal times, focus on the underlying business
- **Damodaran**: cycles are inputs to valuation (cost of equity, growth rates), not the master variable

## Selected passages

### Howard Marks (Sea Change, 2023)
> "[verbatim]"

### Warren Buffett (2022 Letter)
> "[verbatim]"

### Aswath Damodaran
> "[verbatim]"

## What this means for your VN equity research
[1 paragraph: how to reconcile these views when analyzing VN cyclicals like steel, real estate]
```

**Acceptance criteria:**
- [ ] `compare_authors_on(topic="cyclicality", authors=["Warren Buffett", "Howard Marks"])` returns a markdown block with passages from both
- [ ] Each passage cites its source file with a wikilink
- [ ] Claude Code can synthesize a comparison page in one prompt
- [ ] Synonyms auto-derived if not provided (e.g. for "cyclicality" → ["cycle", "cyclical", "boom-bust"])

**Effort:** ~3 hours (MCP tool 1.5hr, skill + prompts 1hr, polish 0.5hr)
**Depends on:** Nothing for MVP. Phase 2 makes it semantic instead of keyword.
**Output target:** 5-10 comparisons in `knowledge/wiki/comparisons/` over a few months.

---

## K9 — Per-ticker thesis context ✅ SHIPPED 2026-06-04

**What:** Before writing a thesis for FPT (or any ticker), automatically assemble a context bundle: every news article mentioning FPT in the last 30 days + your saved analyses/theses on FPT + the most relevant universal principle (e.g. for tech-services: Buffett on "moat", Mauboussin on "scale economies"). Hand the bundle to Claude Code as the starting context for the thesis-writing workflow.

**Why:** Reduces overlooked considerations. The corpus already contains relevant context; you just don't see it when you're typing into the thesis form. Pre-loading saves 30-60 min of "what did I read last month about this?" recall.

**Inputs:**
- Ticker symbol (e.g. "FPT")
- Optional: lookback days (default 30)

**Approach:**
1. MCP tool `thesis_context(ticker)` builds a JSON bundle:
   - `recent_news`: articles in last N days with `tickers_mentioned` including the ticker
   - `existing_research`: any analysis/thesis file in `analyses/` or `theses/` for this ticker
   - `sector_principles`: 2-3 passages from books/blogs tagged or topically matching the company's sector
2. Returns a formatted markdown briefing that Claude Code can read at the start of `vn-equity-analyst` workflow

**Files to build:**
| File | Purpose |
|---|---|
| `server.py` | New MCP tool `thesis_context(ticker, lookback_days)` |
| `.agents/skills/vn-equity-analyst/SKILL.md` | Update RULE 0: call `thesis_context` FIRST before deep dive |
| Optional: `knowledge/pipelines/build_ticker_index.py` | Pre-build per-ticker indexes for faster lookup |

**MCP tool signature:**
```python
thesis_context(
    ticker: str,
    lookback_days: int = 30,
    include_sector_principles: bool = True,
    max_articles: int = 15,
) -> str  # markdown briefing
```

**Output schema** (returned to the LLM, NOT saved to disk by default):
```markdown
## Context Bundle — FPT

### Recent News (last 30 days, 12 articles)
| Date | Source | Headline |
|---|---|---|
| 2026-05-30 | CafeF | FPT Q1 revenue +18% YoY... |
| ...

### Your Existing Research
- analyses/FPT_Q1-2026_2026-05-27.md (5 days ago)
- theses/FPT_thesis_2026-06-03.md (1 day ago)
   - Falsification: revenue growth <10% for 2Q
   - Strongest bias: confirmation bias

### Sector Principles (IT services, technology)
> Buffett (2022 Letter): "Quality businesses earn high returns on capital without much capital..."
> Mauboussin (Base Rates): "Software companies with switching costs..."

### Open Questions Surfaced
- Have you reconciled today's headline (revenue +18%) with your falsification criterion (>10% growth)?
- Your thesis is 1 day old — any material news since?
```

**Acceptance criteria:**
- [ ] `thesis_context("FPT")` returns a briefing in <2 seconds
- [ ] Picks up all articles where `tickers_mentioned` includes FPT (works on existing frontmatter — no embeddings)
- [ ] Reads `theses/INDEX.md` and surfaces any existing thesis on the ticker
- [ ] The `vn-equity-analyst` skill is updated to call this FIRST
- [ ] When the briefing detects a falsification criterion crossing a recent data point, flags it explicitly

**Effort:** ~2 hours (MCP tool 1hr, skill update 0.5hr, sector principle matching 0.5hr — MVP uses static sector→principle map; Phase 2 uses semantic match)
**Depends on:** Nothing for the recency/existing-research parts. Sector principles work better with semantic search (Phase 2) but a manual `sector_principle_map.json` works for MVP.

**Synergy with K1 (ticker hubs):** Once K1 ships, this tool just reads the hub file — instant context bundle assembly. Build K1 first.

## K10 — VN market narrative timeline 💡
**What:** Group ingested articles by week. Ask Claude Code: "what was the dominant theme each week?" Produces a timeline of VN market narrative over the past months.
**Why:** Shows you the *story arc* of the market, not just the noise. Reveals when narrative shifts (e.g. when sentiment flipped on real estate).
**Effort:** ~2 hours

---

# Tier 3 — After Phase 2 (requires embeddings)

These need the search infrastructure from PLAN.md Phase 2-3.

## K11 — `knowledge_search` MCP tool 📋 (in PLAN as Phase 3a)
Free semantic search across all 478 sources via Claude Code subscription. Cross-references VN news with Buffett principles automatically.

## K12 — `/ask` page in web UI 📋 (in PLAN as Phase 3b)
Same RAG capability via browser. Useful for sharing with non-technical users.

## K13 — Smart Connections in Obsidian 💡
If you adopt Obsidian, the Smart Connections plugin handles local embedding + semantic search inside the vault. Replaces some need for our Phase 2 if you only use Obsidian.
**Effort:** 10 min to install + index. **Cost:** $0. **Caveat:** doesn't integrate with web UI.

---

# Tier 4 — Higher-investment, longer-horizon ideas 💡

Specced for completeness. Don't commit unless they're high-value to you specifically.

## K14 — Concept Wiki (Phase 4 in PLAN)
Curated MDX pages: "What is ROIC?" with synthesized definitions + citations into corpus + links to live tools. 30 pages × 1-2 hours each. Big undertaking but compounding value.

## K15 — Investor Vocabulary Builder
Auto-extract investing terms from corpus, define each with a quote from a primary source. Output is a `glossary.md` you actually read. ~3 hours.

## K16 — Sentiment / theme tracking over time
For each ticker or sector, compute a rolling sentiment + theme score from news articles. Chart over time. Useful for sector rotation signals. ~5 hours.

## K17 — Onboarding curriculum
Auto-generate a reading path: "VN investing for beginners — read these 15 sources in this order." Maps the 4-phase curriculum to specific ingested sources. ~3 hours.

## K18 — Mobile capture flow (Obsidian Mobile + Git)
Take a note on phone → Obsidian Git syncs to repo → next ingest run pulls it into the knowledge base. Lets you capture ideas wherever. ~1 hour setup, ongoing usage.

---

# Recommended sequence

Pick whatever matches your immediate use case, but my default ordering:

```
K1 (hubs) — 30 min                ──┐
K2 (coverage report) — 45 min       │  Build foundation while
K6 (daily brief) — 2 hrs            │  embeddings aren't ready
                                  ──┘

[ wait for Phase 2 to ship — embeddings ]

K11 (knowledge_search MCP) — 2 hrs ──┐  Unlock cross-source
K9 (thesis context) — 1 hr             │  intelligence
K8 (cross-reference engine) — 3 hrs ──┘

[ ongoing ]

K7 (Buffett concepts) — author 1 wiki page per week
K14 (concept wiki) — 1 page per session
K16 (sentiment tracking) — when corpus is dense enough
```

---

# Quick-start template

If you resume cold and want to pick this up:

```bash
# 1. Read this file + PLAN.md
# 2. Verify the corpus is intact
.venv/bin/python -m knowledge.pipelines.ingest_rss --stats

# 3. Pick a kickoff ID from above (K1-K18) and start building

# Most likely first session:
#   Build K1 (build_hubs.py) — turns the existing 478 sources into a navigable graph
```

---

## How this file relates to others

- **`PLAN.md`** — Infrastructure roadmap: embed, search, wiki UI, more pipelines (Phases 2-5)
- **`KICKOFF.md`** (this file) — What to *do* with the data
- **`../ROADMAP.md`** — Whole-project view across MCP, web UI, knowledge, and ideas

When you finish a kickoff item, update the status here and consider whether the resulting feature deserves its own entry in `ROADMAP.md`.

---

*Last updated: 2026-06-04*
