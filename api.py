"""FastAPI HTTP wrapper exposing the same tool functions as the MCP server.

The MCP server (server.py) talks to Claude Code; this module exposes the same
underlying functions over HTTP so the Next.js web UI can use them too.
Both surfaces share the cache, watchlist, theses, and decision log.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import server  # reuse all existing tool implementations + cache + storage paths


app = FastAPI(title="VN Stock MCP — HTTP API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _text(result: list) -> str:
    """Tool results are lists of TextContent; we return the joined text."""
    parts: list[str] = []
    for item in result:
        text = getattr(item, "text", None)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


# ── Request schemas ──────────────────────────────────────────────────────────

class TickerRequest(BaseModel):
    ticker: str


class CompareRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=2, max_length=8)
    period: str = "year"


class DCFRequest(BaseModel):
    ticker: str
    discount_rate: float = 12.0
    terminal_growth: float = 5.0
    bull_growth: float = 20.0
    base_growth: float = 12.0
    bear_growth: float = 5.0
    projection_years: int = 5


class PositionSizingRequest(BaseModel):
    ticker: str
    portfolio_value: float
    risk_per_trade_pct: float = 2.0
    conviction: str = "medium"
    atr_multiplier: float = 2.0


class Holding(BaseModel):
    ticker: str
    shares: float
    avg_cost: float


class StressTestRequest(BaseModel):
    holdings: list[Holding]


class ThesisRequest(BaseModel):
    ticker: str
    thesis: str
    buy_price: float
    target_price: float
    stop_price: float
    falsification_criteria: str
    conviction: str = "Medium"
    catalysts: str = ""
    strongest_bias: str = ""
    premortem_reason: str = ""


class DecisionLogRequest(BaseModel):
    ticker: str
    action: str
    price: float
    rationale: str
    quantity: int = 0
    outcome: str = ""


class WatchlistRequest(BaseModel):
    action: str
    ticker: str = ""


class ReviewRequest(BaseModel):
    lookback_days: int = 365


# ── Health + index ───────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tools")
async def tools() -> dict[str, list[str]]:
    """Return the list of registered MCP tools so the UI can introspect."""
    listed = await server.list_tools()
    return {"tools": [t.name for t in listed]}


# ── Data & analysis endpoints ───────────────────────────────────────────────

@app.post("/api/stock/overview")
async def stock_overview(req: TickerRequest) -> dict[str, str]:
    result = await server._get_stock_overview({"ticker": req.ticker})
    return {"text": _text(result)}


@app.post("/api/stock/quality-score")
async def quality_score(req: TickerRequest) -> dict[str, str]:
    result = await server._get_quality_score({"ticker": req.ticker})
    return {"text": _text(result)}


@app.post("/api/stock/earnings-quality")
async def earnings_quality(req: TickerRequest) -> dict[str, str]:
    result = await server._get_earnings_quality({"ticker": req.ticker})
    return {"text": _text(result)}


@app.post("/api/stock/foreign-flow")
async def foreign_flow(req: TickerRequest) -> dict[str, str]:
    result = await server._get_foreign_flow({"ticker": req.ticker})
    return {"text": _text(result)}


@app.post("/api/stock/technical")
async def technical(req: TickerRequest) -> dict[str, str]:
    result = await server._get_technical_analysis({"ticker": req.ticker})
    return {"text": _text(result)}


@app.post("/api/stock/dcf")
async def dcf(req: DCFRequest) -> dict[str, str]:
    result = await server._get_dcf_valuation(req.model_dump())
    return {"text": _text(result)}


@app.post("/api/compare")
async def compare(req: CompareRequest) -> dict[str, str]:
    result = await server._compare_stocks(req.model_dump())
    return {"text": _text(result)}


# ── Market context ──────────────────────────────────────────────────────────

@app.get("/api/market/overview")
async def market_overview() -> dict[str, str]:
    result = await server._get_market_overview({})
    return {"text": _text(result)}


@app.get("/api/market/economy-news")
async def economy_news(limit: int = 20) -> dict[str, str]:
    result = await server._get_economy_news({"limit": limit})
    return {"text": _text(result)}


@app.get("/api/market/macro-data")
async def macro_data() -> dict[str, str]:
    result = await server._get_macro_data({})
    return {"text": _text(result)}


@app.get("/api/market/macro-indicators")
async def macro_indicators() -> dict[str, str]:
    result = await server._get_vn_macro_indicators({})
    return {"text": _text(result)}


@app.get("/api/market/commodities")
async def commodities() -> dict[str, str]:
    result = await server._get_commodity_prices({})
    return {"text": _text(result)}


# ── Risk & portfolio ────────────────────────────────────────────────────────

@app.post("/api/risk/position-sizing")
async def position_sizing(req: PositionSizingRequest) -> dict[str, str]:
    result = await server._get_position_sizing(req.model_dump())
    return {"text": _text(result)}


@app.post("/api/risk/stress-test")
async def stress_test(req: StressTestRequest) -> dict[str, str]:
    result = await server._stress_test_portfolio({"holdings": [h.model_dump() for h in req.holdings]})
    return {"text": _text(result)}


# ── Knowledge (K6-K9) ──────────────────────────────────────────────────────

class ThesisContextRequest(BaseModel):
    ticker: str
    lookback_days: int = 30
    max_articles: int = 15
    include_sector_principles: bool = True


class CompareAuthorsRequest(BaseModel):
    topic: str
    authors: list[str] = Field(..., min_length=1)
    keywords: list[str] = Field(default_factory=list)
    context_paragraphs: int = 2
    max_per_author: int = 5


class DailyBriefRequest(BaseModel):
    date: str = ""  # YYYY-MM-DD; empty = today


@app.post("/api/knowledge/thesis-context")
async def knowledge_thesis_context(req: ThesisContextRequest) -> dict[str, str]:
    result = await server._thesis_context(req.model_dump())
    return {"text": _text(result)}


@app.post("/api/knowledge/compare-authors")
async def knowledge_compare_authors(req: CompareAuthorsRequest) -> dict[str, str]:
    result = await server._compare_authors_on(req.model_dump())
    return {"text": _text(result)}


@app.post("/api/knowledge/daily-brief/gather")
async def knowledge_daily_brief_gather(req: DailyBriefRequest) -> dict[str, Any]:
    """Run the daily-brief input gatherer and return the pending file content."""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from pathlib import Path as _P

    vn_tz = _tz(_td(hours=7))
    target = (_dt.strptime(req.date, "%Y-%m-%d").replace(tzinfo=vn_tz)
              if req.date else _dt.now(vn_tz))

    # Run the pipeline
    from knowledge.pipelines.daily_brief import _run as _gather_run
    pending_path = await _gather_run(target)

    return {
        "pending_path": str(pending_path.relative_to(server.Path(__file__).parent)),
        "date":         target.strftime("%Y-%m-%d"),
        "content":      pending_path.read_text(encoding="utf-8"),
    }


@app.get("/api/knowledge/daily-brief/{date}")
async def knowledge_daily_brief_read(date: str) -> dict[str, Any]:
    """Read the synthesized brief for a date, or the pending file if not yet synthesized."""
    from pathlib import Path as _P
    briefs_dir = _P(__file__).parent / "knowledge" / "briefs"

    synthesized = briefs_dir / f"{date}.md"
    pending     = briefs_dir / f"_pending_{date}.md"

    if synthesized.exists():
        return {
            "status":  "synthesized",
            "path":    str(synthesized.relative_to(_P(__file__).parent)),
            "content": synthesized.read_text(encoding="utf-8"),
        }
    if pending.exists():
        return {
            "status":  "pending",
            "path":    str(pending.relative_to(_P(__file__).parent)),
            "content": pending.read_text(encoding="utf-8"),
        }
    return {"status": "missing", "path": "", "content": ""}


class NewsCorrelationRequest(BaseModel):
    ticker: str
    lookback_days: int = 90


@app.post("/api/stock/news-correlation")
async def news_correlation(req: NewsCorrelationRequest) -> dict[str, str]:
    result = await server._correlate_news_to_price(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/knowledge/glossary")
async def knowledge_glossary() -> dict[str, Any]:
    """Return the curated concept glossary for valuation terms (DCF, WACC, P/E, etc.)."""
    import json as _json
    from pathlib import Path as _P
    path = _P(__file__).parent / "knowledge" / "wiki" / "concepts" / "glossary.json"
    if not path.exists():
        return {"version": 0, "concepts": {}}
    return _json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/knowledge/corpus-stats")
async def knowledge_corpus_stats() -> dict[str, Any]:
    """Snapshot of the corpus — count by category, top tickers, latest ingest."""
    import json as _json
    from pathlib import Path as _P
    manifest = _json.loads((_P(__file__).parent / "knowledge" / "manifest.json").read_text(encoding="utf-8"))

    by_category: dict[str, int] = {}
    by_source:   dict[str, int] = {}
    for entry in manifest.get("ingested", {}).values():
        by_category[entry.get("category", "?")] = by_category.get(entry.get("category", "?"), 0) + 1
        by_source[entry.get("source", "?")] = by_source.get(entry.get("source", "?"), 0) + 1

    return {
        "total":        len(manifest.get("ingested", {})),
        "last_run":     manifest.get("last_run"),
        "by_category":  by_category,
        "top_sources":  dict(sorted(by_source.items(), key=lambda x: -x[1])[:10]),
    }


# ── Watchlist ───────────────────────────────────────────────────────────────

@app.post("/api/watchlist/manage")
async def watchlist_manage(req: WatchlistRequest) -> dict[str, str]:
    result = await server._manage_watchlist(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/watchlist/check")
async def watchlist_check() -> dict[str, str]:
    result = await server._check_watchlist({})
    return {"text": _text(result)}


@app.get("/api/watchlist/raw")
async def watchlist_raw() -> dict[str, list[str]]:
    """Return the raw watchlist JSON for UI use (vs. the markdown view)."""
    return {"tickers": server._load_watchlist()}


# ── Journaling & review ─────────────────────────────────────────────────────

@app.post("/api/journal/thesis")
async def journal_thesis(req: ThesisRequest) -> dict[str, str]:
    result = await server._save_investment_thesis(req.model_dump())
    return {"text": _text(result)}


@app.post("/api/journal/decision")
async def journal_decision(req: DecisionLogRequest) -> dict[str, str]:
    result = await server._save_decision_log(req.model_dump())
    return {"text": _text(result)}


@app.post("/api/journal/review")
async def journal_review(req: ReviewRequest) -> dict[str, str]:
    result = await server._review_performance(req.model_dump())
    return {"text": _text(result)}


# ── Structured endpoints (for charts) ───────────────────────────────────────

@app.get("/api/journal/decisions-raw")
async def decisions_raw() -> dict[str, Any]:
    """Return parsed decisions + computed metrics as JSON so the UI can chart them."""
    log_path = server.DECISIONS_DIR / "LOG.md"
    if not log_path.exists():
        return {"decisions": [], "closed_trades": [], "open_positions": {}, "metrics": {}}

    decisions = server._parse_decision_log(log_path.read_text(encoding="utf-8"))
    closed, open_pos = server._pair_trades(decisions)
    metrics = server._compute_performance_metrics(closed)
    clusters = server._cluster_losses(closed)

    # Pydantic / FastAPI handles date serialization automatically via default_handler
    return {
        "decisions":       [{**d, "date": d["date"].isoformat()} for d in decisions],
        "closed_trades":   [{**t, "buy_date": t["buy_date"].isoformat(), "sell_date": t["sell_date"].isoformat()} for t in closed],
        "open_positions":  {k: {**v, "first_buy": v["first_buy"].isoformat()} for k, v in open_pos.items()},
        "metrics":         metrics,
        "clusters":        clusters,
    }


@app.get("/api/journal/theses-raw")
async def theses_raw() -> dict[str, Any]:
    """Return list of saved theses from the theses/ folder."""
    index_path = server.THESES_DIR / "INDEX.md"
    if not index_path.exists():
        return {"theses": []}
    return {"theses_index": index_path.read_text(encoding="utf-8")}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
