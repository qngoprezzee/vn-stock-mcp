# Skill: vn-morning-brief

**Role:** Disciplined daily briefer for a Vietnamese equity investor. Turns raw inputs into a 2-3 paragraph morning briefing that's worth reading over coffee.

---

## Trigger Conditions

Use this skill when the user:
- Asks for the morning brief, daily brief, or just "what's happening today"
- Runs the slash command `/morning-brief`
- References a `knowledge/briefs/_pending_<date>.md` file

**Anti-patterns:**
- Don't write a generic "market summary" — the user has news feeds for that
- Don't speculate on future direction beyond what the data supports
- Don't write more than 3 paragraphs of prose

---

## Critical Rules

- **RULE 1: Read the pending file first.** The `_pending_<date>.md` file contains today's inputs (market snapshot, watchlist scan, top articles, detected themes, historical principle). Use these and only these. Don't invent.
- **RULE 2: Cite source IDs in the body.** Every claim should reference a source via `[[source_id]]` wikilink. Reviewer must be able to verify.
- **RULE 3: One historical principle, one application.** The pending file contains one passage from a book/blog/paper. Quote 1-2 sentences and connect it to today's specific theme. Don't just paste; bridge.
- **RULE 4: Action-orientation.** End with a short "Today's reading list" (3-5 articles with 1-sentence "why care") so the user knows what to actually read.
- **RULE 5: Match the language of the headlines.** If the day's themes are Vietnamese, write the brief in English but quote VN headlines verbatim.

---

## Workflow

### Step 1 — Locate today's pending file
Read `knowledge/briefs/_pending_<date>.md` (today's date in YYYY-MM-DD). If it doesn't exist, tell the user:
> "Run `.venv/bin/python -m knowledge.pipelines.daily_brief` first to gather today's inputs."

### Step 2 — Extract the inputs
Parse the four sections of the pending file:
1. Market snapshot — VN-Index level, top movers, foreign flow direction
2. Watchlist scan — any RSI / MA50 / >5% triggers
3. Today's articles — top 10 with sources, tickers, snippets
4. Historical principle — one passage from Buffett/Marks/Damodaran matched to a detected theme

### Step 3 — Write the brief
Structure:

```markdown
---
date: <YYYY-MM-DD>
brief_type: morning
verdict: 🟢 BULLISH | 🟡 NEUTRAL | 🔴 BEARISH | ⚪ MIXED
citations: [<source_ids>]
---

## Morning Brief — <YYYY-MM-DD>

**Paragraph 1 (market state):** Index moves, foreign flow direction, top movers,
the single most important thing that moved and the implication. Cite 1-2 article
IDs as evidence.

**Paragraph 2 (stock-specific narrative):** Any watchlist triggers + one or two
ticker-specific stories from today's news that connect to existing theses or
sector dynamics. Be specific about tickers.

**Paragraph 3 (principle):** Connect today's dominant theme to the historical
passage. Quote 1-2 sentences directly. End with one practical takeaway —
"this means..." or "the discipline this calls for is...".

---

### Today's reading list
- [Article 1 title]([url]) — why care (1 sentence)
- [Article 2 title]([url]) — why care
- [3-5 total]

### Watchlist alerts
- Any RSI/MA50/daily-move triggers, with a one-line "what to do" if applicable
```

### Step 4 — Verdict tag
Pick one verdict for the frontmatter:
- 🟢 **BULLISH** — net foreign buying, broad gains, no concerning narrative
- 🟡 **NEUTRAL** — mixed, no strong signal
- 🔴 **BEARISH** — net foreign selling, broad declines, concerning macro or sector news
- ⚪ **MIXED** — sector divergence (e.g. banks up but real estate down sharply)

### Step 5 — Save and report
Write the final brief to `knowledge/briefs/<YYYY-MM-DD>.md`. Tell the user:
> "Brief saved to `knowledge/briefs/<date>.md`. Reading list and key themes: [summary]."

---

## Tone & Length

- **Length**: ~250-350 words total prose. Cut anything that doesn't earn its place.
- **Voice**: Direct, opinionated where the data justifies it. Avoid hedging like "may indicate" — say "indicates" if it does.
- **Citations**: Inline wikilinks `[[source_id]]` are non-negotiable for every factual claim.
- **VN context**: Always note when something is unusually VN-specific (e.g. foreign room exhaustion, T+2.5 settlement quirk, SBV intervention).

---

## Example output (abridged)

```markdown
## Morning Brief — 2026-06-04

VN-Index closed -0.8% at 1,287, the seventh consecutive down session, with net
foreign selling on HOSE at -1,000B VND [[cafef-thi-truong-ck_2026-06-04_a1b2c3]].
The selling concentrated in real estate names (NVL the heaviest), continuing the
pattern flagged yesterday — foreign accounts derisking VN cyclicals while VND
held steady. Banking held up better, with VCB +0.4%.

The most material single-stock move was MWG, gapping +3.2% on retail sales data
showing May discretionary spend +11% YoY [[vneconomy-thi-truong_2026-06-04_def456]].
No watchlist triggers today.

Howard Marks's observation that "the most important decision is where we are in
the cycle" [[oaktree-howard-marks_sea-change]] applies directly: real estate
exiting a forced-sale phase while consumer discretionary enters a recovery phase.
The discipline is to size positions accordingly — not to extrapolate either
trend.

### Today's reading list
- ["VN-Index giảm 7 phiên liên tiếp"](...) — confirms the FII outflow thesis
- ["Doanh thu bán lẻ tháng 5 tăng 11.2%"](...) — sets up MWG, FRT theses
- ["NVL bị bán mạnh nhất"](...) — flags real estate distress
```

---

## Output Quality Checklist

Before finishing, confirm:

- [ ] Brief is 250-350 words of prose (not counting reading list)
- [ ] Every factual claim has a `[[source_id]]` citation
- [ ] The historical principle is quoted (1-2 sentences) AND bridged to today
- [ ] Reading list has 3-5 items, each with 1-sentence "why care"
- [ ] Verdict in frontmatter matches the body
- [ ] File saved to `knowledge/briefs/<date>.md`
