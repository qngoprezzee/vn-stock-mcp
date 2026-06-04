"""K6 — Daily Morning Brief: gather inputs for Claude Code to synthesize.

Two-stage pattern:
  Stage 1 (this script): pulls today's articles + watchlist scan + market overview
                          + a historical principle matched to today's themes,
                          writes a `_pending` markdown bundle
  Stage 2 (Claude Code): the `vn-morning-brief` skill reads the bundle and writes
                          a 2-3 paragraph briefing to `knowledge/briefs/<date>.md`

Usage:
    .venv/bin/python -m knowledge.pipelines.daily_brief

Then in Claude Code:
    /morning-brief

To re-gather for a specific day (e.g. yesterday):
    .venv/bin/python -m knowledge.pipelines.daily_brief --date 2026-06-03
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import server  # for _check_watchlist, _get_market_overview
from knowledge.lib.corpus import find_passages, iter_sources

VN_TZ = timezone(timedelta(hours=7))
BRIEFS_DIR = REPO_ROOT / "knowledge" / "briefs"


# Common headline themes mapped to investing principle keywords
# Used in MVP to keyword-match today's headlines to a historical passage
_THEME_KEYWORDS: dict[str, list[str]] = {
    "real_estate":  ["real estate", "bất động sản", "BĐS", "property", "housing"],
    "banking":      ["bank", "ngân hàng", "credit", "tín dụng"],
    "cyclical":     ["cycle", "cyclical", "boom", "bust", "downturn", "recession"],
    "macro":        ["GDP", "inflation", "CPI", "rate", "interest", "lãi suất", "FX"],
    "earnings":     ["earnings", "Q1", "Q2", "Q3", "Q4", "quý", "revenue", "doanh thu", "lợi nhuận"],
    "foreign":      ["foreign", "nước ngoài", "FII", "FDI", "capital flow"],
    "valuation":    ["valuation", "P/E", "P/B", "định giá", "intrinsic", "DCF"],
    "moat":         ["moat", "competitive advantage", "pricing power", "switching cost"],
    "leverage":     ["debt", "leverage", "credit risk", "default", "bond"],
    "ipo":          ["IPO", "listing", "niêm yết"],
}


def _text_to_themes(text: str) -> list[str]:
    text_lower = text.lower()
    themes: list[str] = []
    for theme, words in _THEME_KEYWORDS.items():
        if any(w.lower() in text_lower for w in words):
            themes.append(theme)
    return themes


def _gather_articles(target_date: datetime, max_n: int = 10) -> list[dict]:
    """Pull articles ingested/published within ~24h of target_date."""
    articles = list(iter_sources(category="articles", since_days=2, limit=200))

    # Filter by closeness to target date
    target_str = target_date.strftime("%Y-%m-%d")
    today = [s for s in articles if (s.ingested_at or "").startswith(target_str)]

    # Fall back to most recent if not enough on target day
    if len(today) < max_n:
        sorted_by_recency = sorted(articles, key=lambda s: s.ingested_at or "", reverse=True)
        today = sorted_by_recency[:max_n]
    else:
        today = today[:max_n]

    return [{
        "id":          s.id,
        "title":       s.title,
        "source":      s.source_name,
        "url":         s.url,
        "pub_date":    s.pub_date,
        "tickers":     s.tickers,
        "snippet":     s.body[:280].replace("\n", " ").strip(),
    } for s in today]


def _find_principle(themes: list[str], headline_text: str) -> dict | None:
    """Pick one historical passage from books/blogs matching the day's dominant theme."""
    if not themes:
        return None

    # For each theme, try the matching keywords. Take first match across all books/blogs.
    book_sources = list(iter_sources(category="books"))
    blog_sources = list(iter_sources(category="blogs"))
    paper_sources = list(iter_sources(category="papers"))
    pool = book_sources + blog_sources + paper_sources

    if not pool:
        return None

    for theme in themes:
        keywords = _THEME_KEYWORDS.get(theme, [])
        if not keywords:
            continue
        matches = find_passages(pool, keywords, context_paragraphs=1, max_matches_per_source=1)
        if matches:
            m = matches[0]
            return {
                "source_id":   m["source_id"],
                "title":       m["title"],
                "authors":     m["authors"],
                "passage":     m["passage"][:1200],
                "theme":       theme,
                "keyword":     m["keyword"],
            }
    return None


async def _gather_market() -> str:
    """Call the existing tool function to get today's market overview as text."""
    try:
        result = await server._get_market_overview({})
        return result[0].text if result else ""
    except Exception as e:
        return f"(market overview unavailable: {e})"


async def _gather_watchlist() -> str:
    try:
        result = await server._check_watchlist({})
        return result[0].text if result else ""
    except Exception as e:
        return f"(watchlist scan unavailable: {e})"


def _format_pending(*, target_date: datetime, articles: list[dict], market: str,
                    watchlist: str, principle: dict | None) -> str:
    date_str = target_date.strftime("%Y-%m-%d")
    iso_now = datetime.now(VN_TZ).isoformat(timespec="seconds")

    headlines_text = "\n".join(a["title"] for a in articles)
    themes = _text_to_themes(headlines_text)
    citations = [a["id"] for a in articles[:5]]
    if principle:
        citations.append(principle["source_id"])

    lines = [
        "---",
        f"date: {date_str}",
        f"brief_type: morning",
        f"status: pending_synthesis",
        f"generated_at: {iso_now}",
        f"themes_detected: [{', '.join(repr(t) for t in themes)}]",
        f"citations: [{', '.join(repr(c) for c in citations)}]",
        "---",
        "",
        f"# Morning Brief — Pending Synthesis ({date_str})",
        "",
        "**Status:** This file contains the raw inputs gathered by the daily-brief pipeline.",
        "Invoke the `vn-morning-brief` skill (or `/morning-brief` slash command) in Claude Code",
        f"to synthesize the final brief at `knowledge/briefs/{date_str}.md`.",
        "",
        "---",
        "",
        "## Inputs",
        "",
        "### Market snapshot",
        "",
        market,
        "",
        "### Watchlist scan",
        "",
        watchlist,
        "",
        f"### Today's articles (top {len(articles)})",
        "",
        "| # | Source | Title | Tickers | Snippet |",
        "|---|---|---|---|---|",
    ]

    for i, a in enumerate(articles, 1):
        tickers_str = ", ".join(a["tickers"][:5]) if a["tickers"] else "—"
        snippet = a["snippet"][:140].replace("|", "\\|")
        title = a["title"][:80].replace("|", "\\|")
        lines.append(f"| {i} | {a['source'][:25]} | [{title}]({a['url']}) | {tickers_str} | {snippet}... |")

    lines.append("")
    lines.append("### Detected themes (from headline keyword scan)")
    lines.append("")
    if themes:
        for t in themes:
            lines.append(f"- **{t}**")
    else:
        lines.append("- (no themes matched in keyword map)")
    lines.append("")

    lines.append("### Historical principle (matched by theme)")
    lines.append("")
    if principle:
        author = ", ".join(principle["authors"]) if principle["authors"] else "unknown"
        lines.append(f"**Source:** *{principle['title']}* — {author}  ")
        lines.append(f"**Theme:** {principle['theme']} (matched on keyword: `{principle['keyword']}`)")
        lines.append(f"**Source ID:** `{principle['source_id']}`")
        lines.append("")
        lines.append("> " + principle["passage"].replace("\n", "\n> "))
    else:
        lines.append("*(no historical principle matched — keyword map didn't find a relevant theme)*")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## Synthesis instructions for Claude Code")
    lines.append("")
    lines.append(f"Write the final brief to `knowledge/briefs/{date_str}.md`. Target format:")
    lines.append("")
    lines.append("```")
    lines.append("Paragraph 1: market state — index moves, foreign flow, top movers, the one")
    lines.append("              thing that moved the most and what it implies")
    lines.append("Paragraph 2: watchlist alerts and one stock-specific narrative from today's news")
    lines.append("Paragraph 3: connect today's theme to the historical principle above; 1-2 sentence")
    lines.append("              direct quote, then a sentence on what to remember")
    lines.append("```")
    lines.append("")
    lines.append("Then a short bulleted **Today's reading list** (3-5 articles, each with 1-sentence \"why care\").")
    lines.append("")
    lines.append("Cite source IDs in the body using wikilinks: `[[<source_id>]]`.")

    return "\n".join(lines)


async def _run(target_date: datetime) -> Path:
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

    articles  = _gather_articles(target_date)
    print(f"  Articles gathered: {len(articles)}")

    market_task    = asyncio.create_task(_gather_market())
    watchlist_task = asyncio.create_task(_gather_watchlist())

    market, watchlist = await asyncio.gather(market_task, watchlist_task)
    print(f"  Market overview: {len(market)} chars")
    print(f"  Watchlist scan:  {len(watchlist)} chars")

    headlines_text = "\n".join(a["title"] for a in articles)
    themes = _text_to_themes(headlines_text)
    principle = _find_principle(themes, headlines_text) if articles else None
    print(f"  Themes detected: {themes or '(none)'}")
    print(f"  Historical principle: {principle['title'] if principle else '(none matched)'}")

    pending = _format_pending(
        target_date=target_date, articles=articles, market=market,
        watchlist=watchlist, principle=principle,
    )

    date_str = target_date.strftime("%Y-%m-%d")
    out_path = BRIEFS_DIR / f"_pending_{date_str}.md"
    out_path.write_text(pending, encoding="utf-8")
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--date", default="", help="Target date in YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    if args.date:
        target = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=VN_TZ)
    else:
        target = datetime.now(VN_TZ)

    print(f"Gathering daily brief inputs for {target.strftime('%Y-%m-%d')}...")
    out_path = asyncio.run(_run(target))

    print(f"\n✓ Pending brief written: {out_path.relative_to(REPO_ROOT)}")
    print(f"\nNext: invoke the `vn-morning-brief` skill in Claude Code to synthesize the final brief.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
