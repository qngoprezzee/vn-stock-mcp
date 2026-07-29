"""FastAPI HTTP wrapper exposing the same tool functions as the MCP server.

The MCP server (server.py) talks to Claude Code; this module exposes the same
underlying functions over HTTP so the Next.js web UI can use them too.
Both surfaces share the cache, watchlist, theses, and decision log.
"""
from __future__ import annotations

import asyncio
import json as _json
import time
from contextlib import asynccontextmanager
from typing import Any

import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import server  # reuse all existing tool implementations + cache + storage paths

# ── Market movers universe — 150 liquid stocks across all HOSE/HNX sectors ──
_MOVERS_UNIVERSE = [
    # Banking (VN30)
    "VCB", "BID", "CTG", "TCB", "MBB", "VPB", "ACB", "STB", "SHB", "LPB",
    "MSB", "HDB", "VIB", "EIB", "OCB", "TPB", "NVB", "BAB", "SSB", "PGB",
    # Real Estate
    "VIC", "VHM", "NLG", "KDH", "DXG", "VRE", "DIG", "BCM", "NVL", "PDR",
    "HDG", "TDH", "CEO", "IJC", "SCR", "AGG", "HBC", "TC6", "LDG", "SJS",
    # Technology
    "FPT", "CMG", "ELC", "VGI", "CTR", "SAM", "VTK", "ITD", "TST", "ST8",
    # Steel / Materials
    "HPG", "HSG", "NKG", "TLH", "SMC", "TVN", "POM", "VIS",
    # Consumer Staples
    "VNM", "SAB", "MSN", "KDC", "MCH", "MML", "ANV", "CII",
    # Consumer Discretionary
    "MWG", "FRT", "PNJ", "SVC", "DGW", "PET",
    # Energy / Oil & Gas
    "GAS", "PLX", "POW", "PVD", "PVS", "BSR", "DCM", "DPM", "PVT", "OIL",
    # Aviation / Transport
    "VJC", "HVN", "ACV", "GMD", "VTP", "HAH", "STG", "TMS",
    # Industrial / Rubber
    "GVR", "PHR", "DPR", "TRC",
    # Power / Utilities
    "REE", "NT2", "PC1", "PPC", "SBA", "GEG", "TGG",
    # Insurance / Securities
    "BVH", "VND", "SSI", "HCM", "VIX", "SHS", "BSI", "AGR",
    # Pharma / Healthcare
    "IMP", "DHG", "TRA", "DBD", "DMC", "VMD",
    # Construction / Infrastructure
    "CTD", "VCG", "HHV", "C4G", "FCN", "LCG", "DCC",
    # Food / Agri
    "HAG", "HNG", "BAF", "LSS", "SBT", "SEC",
]


# ── In-memory cache ───────────────────────────────────────────────────────────
_movers_cache: dict[str, Any] = {}
_movers_lock  = asyncio.Lock()
_REFRESH_SEC  = 300   # refresh every 5 minutes


async def _compute_movers() -> dict[str, Any]:
    """Fetch recent quote_history for the full universe, compute % change, return top movers."""
    from datetime import date, timedelta
    import json as _j

    start = (date.today() - timedelta(days=5)).isoformat()
    end   = date.today().isoformat()

    results = await asyncio.gather(
        *[server._vnstock_subprocess("quote_history", {"ticker": s, "start": start, "end": end})
          for s in _MOVERS_UNIVERSE],
        return_exceptions=True,
    )

    movers: list[dict] = []
    for i, raw in enumerate(results):
        if isinstance(raw, Exception):
            continue
        try:
            rows = _j.loads(raw)
            if not isinstance(rows, list) or len(rows) < 2:
                continue
            price = float(rows[-1].get("close", 0)) * 1000
            prev  = float(rows[-2].get("close", 0)) * 1000
            vol   = int(rows[-1].get("volume", 0))
            if not prev or not price:
                continue
            pct = round((price - prev) / prev * 100, 2)
            movers.append({"ticker": _MOVERS_UNIVERSE[i], "value": round(price), "change_pct": pct, "volume": vol})
        except Exception:
            continue

    movers.sort(key=lambda x: x["change_pct"], reverse=True)
    gainers = [m for m in movers if m["change_pct"] > 0][:10]
    losers  = [m for m in reversed(movers) if m["change_pct"] < 0][:10]

    return {"gainers": gainers, "losers": losers, "refreshed_at": time.time(), "universe_size": len(movers)}


async def _background_refresh() -> None:
    """Background loop: refresh market movers cache every _REFRESH_SEC seconds."""
    while True:
        try:
            data = await _compute_movers()
            async with _movers_lock:
                _movers_cache.update(data)
        except Exception:
            pass
        await asyncio.sleep(_REFRESH_SEC)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Trigger first refresh immediately, then keep looping in background
    asyncio.create_task(_background_refresh())
    yield


app = FastAPI(title="VN Stock MCP — HTTP API", version="1.0.0", lifespan=lifespan)

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


class MoneyFlowRequest(BaseModel):
    ticker: str
    days: int = 180


class PortfolioActionRequest(BaseModel):
    action: str
    ticker: str = ""
    shares: float | None = None
    avg_cost: float | None = None
    target_weight: float | None = None
    cash_vnd: float | None = None
    notes: str = ""


class RebalanceRequest(BaseModel):
    threshold_pct: float = 3.0


class PortfolioReturnsRequest(BaseModel):
    risk_free_rate: float = 4.0


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


@app.post("/api/stock/money-flow-price-action")
async def money_flow_price_action(req: MoneyFlowRequest) -> dict[str, str]:
    result = await server._get_money_flow_price_action(req.model_dump())
    return {"text": _text(result)}


class BrokerNewsRequest(BaseModel):
    ticker: str
    limit: int = 15


@app.post("/api/stock/broker-news")
async def broker_news_endpoint(req: BrokerNewsRequest) -> dict[str, str]:
    result = await server._fetch_broker_news(req.model_dump())
    # Combine any text items into a single markdown blob
    parts: list[str] = []
    for item in result:
        if hasattr(item, "text"):
            parts.append(item.text)
    return {"text": "\n\n---\n\n".join(parts)}


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


@app.get("/api/market/news-data")
async def market_news_data(limit: int = 50) -> dict:
    from datetime import datetime, timezone, timedelta
    articles = await server._get_economy_news_articles(limit)
    vn_tz = timezone(timedelta(hours=7))
    return {
        "articles": articles,
        "total": len(articles),
        "sources_total": len(server._ECONOMY_FEEDS),
        "generated_at": datetime.now(vn_tz).strftime("%Y-%m-%d %H:%M VNT"),
    }


_news_digest_cache: dict[str, Any] = {}


@app.post("/api/market/news-digest")
async def market_news_digest(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize today's headlines into a one-page AI digest."""
    import os as _os
    from datetime import datetime, timezone, timedelta

    api_key = _os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not set")

    body = body or {}
    limit = int(body.get("limit", 60))
    force = bool(body.get("force", False))

    articles = await server._get_economy_news_articles(limit)
    if not articles:
        raise HTTPException(status_code=503, detail="No articles available to digest")

    cache_key = "news_digest_" + str(hash(tuple((a["source"], a["title"]) for a in articles)))
    cached = _news_digest_cache.get(cache_key)
    if not force and cached and time.time() - cached.get("_ts", 0) < 1800:
        return cached

    headline_lines = [
        f"- [{a['source']}] {a['date']}: {a['title']}"
        for a in articles
    ]
    headlines_blob = "\n".join(headline_lines)

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=900,
            messages=[
                {"role": "system", "content": (
                    "You are a Vietnamese equity market analyst writing a one-page daily news digest. "
                    "Group the supplied headlines into clear themes and explain what an investor should take away. "
                    "Output MUST be Markdown with these sections, in order:\n\n"
                    "## TL;DR\n"
                    "One short paragraph (3-4 sentences) capturing the overall market mood and the biggest "
                    "single story of the day.\n\n"
                    "## Top Themes\n"
                    "3-5 bullets. Each bullet starts with a **bold theme name** then 1-2 sentences explaining "
                    "what's happening, which tickers/sectors are involved, and why it matters. Cite source names "
                    "in brackets when relevant, e.g. [CafeF].\n\n"
                    "## Stocks to Watch\n"
                    "Bulleted list of specific tickers that appeared in the headlines with a one-line angle each. "
                    "Skip this section if no individual tickers stand out.\n\n"
                    "## Macro & Policy\n"
                    "1-3 bullets on monetary, FX, regulation, or international macro stories that could affect "
                    "VN equities. Skip if nothing relevant.\n\n"
                    "## Risks / Watch-outs\n"
                    "1-3 bullets on negative or cautionary stories.\n\n"
                    "Write in concise, professional English. Vietnamese headlines should be translated/summarized "
                    "in English. Do not invent facts not present in the headlines."
                )},
                {"role": "user", "content": (
                    f"Today is {datetime.now(timezone(timedelta(hours=7))).strftime('%Y-%m-%d')} (Vietnam time).\n"
                    f"Here are {len(articles)} latest headlines from {len(server._ECONOMY_FEEDS)} Vietnamese & "
                    f"English financial news sources. Write the digest.\n\n"
                    f"{headlines_blob}"
                )},
            ],
        )
        digest_md = resp.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    vn_tz = timezone(timedelta(hours=7))
    result = {
        "digest": digest_md,
        "article_count": len(articles),
        "sources_total": len(server._ECONOMY_FEEDS),
        "generated_at": datetime.now(vn_tz).strftime("%Y-%m-%d %H:%M VNT"),
        "model": "gpt-4o-mini",
        "_ts": time.time(),
    }
    _news_digest_cache[cache_key] = result
    return result


@app.get("/api/market/macro-data")
async def macro_data() -> dict[str, str]:
    result = await server._get_macro_data({})
    return {"text": _text(result)}


@app.get("/api/market/macro-indicators")
async def macro_indicators() -> dict[str, str]:
    result = await server._get_vn_macro_indicators({})
    return {"text": _text(result)}


@app.get("/api/market/money-supply")
async def money_supply() -> dict[str, str]:
    result = await server._get_money_supply({})
    return {"text": _text(result)}


class M2SeriesActionRequest(BaseModel):
    action: str
    date: str = ""
    value_trillion_vnd: float | None = None
    source: str = ""
    note: str = ""


@app.post("/api/market/m2-series")
async def m2_series_manage(req: M2SeriesActionRequest) -> dict[str, str]:
    result = await server._manage_m2_series(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/market/m2-series/raw")
async def m2_series_raw() -> dict:
    return {"observations": server._load_m2_series()}


class CpiSeriesActionRequest(BaseModel):
    action: str
    date: str = ""
    cpi_yoy: float | None = None
    cpi_mom: float | None = None
    source: str = ""
    note: str = ""


@app.post("/api/market/cpi-series")
async def cpi_series_manage(req: CpiSeriesActionRequest) -> dict[str, str]:
    result = await server._manage_cpi_series(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/market/cpi-series/raw")
async def cpi_series_raw() -> dict:
    return {"observations": server._load_cpi_series()}


class RateSeriesActionRequest(BaseModel):
    action: str
    date: str = ""
    refinance: float | None = None
    interbank_on: float | None = None
    deposit_12m: float | None = None
    source: str = ""
    note: str = ""


@app.post("/api/market/rate-series")
async def rate_series_manage(req: RateSeriesActionRequest) -> dict[str, str]:
    result = await server._manage_rate_series(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/market/rate-series/raw")
async def rate_series_raw() -> dict:
    return {"observations": server._load_rate_series()}


@app.get("/api/market/pillars")
async def macro_pillars() -> dict[str, str]:
    result = await server._get_macro_pillars({})
    return {"text": _text(result)}


@app.get("/api/market/sector-rotation")
async def sector_rotation(rank_by: str = "3M") -> dict[str, str]:
    result = await server._get_sector_rotation({"rank_by": rank_by})
    return {"text": _text(result)}


@app.get("/api/market/top-tickers-by-sector")
async def top_tickers_by_sector(
    rank_by: str = "3M",
    top_sectors: int = 3,
    top_tickers: int = 5,
) -> dict:
    return await server._get_top_tickers_by_sector({
        "rank_by": rank_by,
        "top_sectors": top_sectors,
        "top_tickers": top_tickers,
    })


@app.get("/api/market/cycle")
async def market_cycle() -> dict[str, str]:
    result = await server._get_market_cycle({})
    return {"text": _text(result)}


class MacroReportRequest(BaseModel):
    source: str
    save: bool = False
    broker: str = ""
    title: str = ""
    language: str = "vi"


@app.post("/api/macro/report")
async def macro_report_load(req: MacroReportRequest) -> dict[str, str]:
    result = await server._load_macro_report(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/macro/reports")
async def macro_reports_list(limit: int = 20) -> dict[str, str]:
    result = await server._list_macro_reports({"limit": limit})
    return {"text": _text(result)}


@app.post("/api/macro/report/upload")
async def macro_report_upload(
    file:     UploadFile = File(...),
    save:     bool = Form(False),
    broker:   str = Form(""),
    title:    str = Form(""),
    language: str = Form("vi"),
) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Must be a .pdf file")
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        result = await server._load_macro_report({
            "source": tmp_path,
            "save": save,
            "broker": broker,
            "title": title or file.filename,
            "language": language,
        })
        return {"text": _text(result)}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


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


@app.post("/api/portfolio/manage")
async def portfolio_manage(req: PortfolioActionRequest) -> dict[str, str]:
    result = await server._manage_portfolio(req.model_dump(exclude_none=True))
    return {"text": _text(result)}


@app.get("/api/portfolio/overview")
async def portfolio_overview() -> dict[str, str]:
    result = await server._get_portfolio_overview({})
    return {"text": _text(result)}


@app.get("/api/portfolio/risk")
async def portfolio_risk() -> dict[str, str]:
    result = await server._get_portfolio_risk({})
    return {"text": _text(result)}


@app.post("/api/portfolio/rebalance")
async def portfolio_rebalance(req: RebalanceRequest) -> dict[str, str]:
    result = await server._get_rebalancing_suggestions(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/portfolio/raw")
async def portfolio_raw() -> dict:
    return server._load_portfolio()


@app.post("/api/portfolio/returns")
async def portfolio_returns(req: PortfolioReturnsRequest) -> dict[str, str]:
    result = await server._get_portfolio_returns(req.model_dump())
    return {"text": _text(result)}


@app.get("/api/portfolio/snapshots")
async def portfolio_snapshots() -> dict:
    return {"snapshots": server._load_snapshots()}


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


# ── Chart data (structured JSON for Recharts) ────────────────────────────────

import json as _json


@app.get("/api/market/dashboard-data")
async def market_dashboard_data() -> dict[str, Any]:
    """Structured market data: indices (live) + top movers (from background cache)."""
    import json as _j
    from datetime import date, timedelta

    start = (date.today() - timedelta(days=7)).isoformat()
    end   = date.today().isoformat()

    index_syms = ["VNINDEX", "HNXINDEX", "UPCOMINDEX"]
    results = await asyncio.gather(
        *[server._vnstock_subprocess("quote_history", {"ticker": s, "start": start, "end": end})
          for s in index_syms],
        return_exceptions=True,
    )

    def _parse(raw) -> list:
        if isinstance(raw, Exception): return []
        try:
            r = _j.loads(raw)
            return r if isinstance(r, list) else []
        except Exception: return []

    def _last(rows, scale=1):
        if len(rows) < 2: return None
        price = float(rows[-1].get("close", 0)) * scale
        prev  = float(rows[-2].get("close", 0)) * scale
        vol   = float(rows[-1].get("volume", 0))
        return {
            "value":      round(price, 2),
            "change":     round(price - prev, 2),
            "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
            "volume":     round(vol),
        }

    data = {s: _parse(results[i]) for i, s in enumerate(index_syms)}
    indices = {
        "vnindex": _last(data["VNINDEX"])   or {},
        "hnx":     _last(data["HNXINDEX"])  or {},
        "upcom":   _last(data["UPCOMINDEX"]) or {},
    }

    # Movers from background cache (instant); fall back to empty if not yet populated
    async with _movers_lock:
        gainers = _movers_cache.get("gainers", [])
        losers  = _movers_cache.get("losers",  [])
        refreshed_at = _movers_cache.get("refreshed_at")
        universe_size = _movers_cache.get("universe_size", 0)

    return {
        "indices":       indices,
        "gainers":       gainers,
        "losers":        losers,
        "universe_size": universe_size,
        "movers_age_s":  round(time.time() - refreshed_at) if refreshed_at else None,
    }


@app.get("/api/market/index-chart")
async def market_index_chart(index: str = "VNINDEX", days: int = 365) -> dict[str, Any]:
    """VN-Index (or HNX/UPCOM) price history for charting. Scale=1 (already in index points)."""
    import json as _j
    raw = await server._vnstock_subprocess("quote_history_full", {"ticker": index.upper(), "days": days})
    rows = _j.loads(raw)
    if not rows or isinstance(rows, dict):
        raise HTTPException(status_code=404, detail=f"No data for {index}")
    prices = [
        {"date": r["time"][:10], "value": round(float(r.get("close", 0)), 2), "volume": int(r.get("volume", 0))}
        for r in rows if r.get("time")
    ]
    return {"index": index.upper(), "prices": prices}


_simplize_cache: dict[str, Any] = {}
_SIMPLIZE_TTL = 3600  # 1 hour


@app.get("/api/stock/executive-summary")
async def stock_executive_summary(ticker: str) -> dict[str, Any]:
    """Fetch Simplize executive summary: rewards, risks, scoring, TA signal, downside risk."""
    import httpx as _httpx

    ticker = ticker.upper()
    cache_key = f"exec_{ticker}"
    cached = _simplize_cache.get(cache_key)
    if cached and time.time() - cached["_ts"] < _SIMPLIZE_TTL:
        return cached

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://simplize.vn",
        "Origin": "https://simplize.vn",
    }
    try:
        async with _httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                f"https://api2.simplize.vn/api/company/executive-summary/{ticker}",
                headers=headers, timeout=10,
            )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=f"Simplize returned {r.status_code}")
        data = r.json().get("data", {})
        data["_ts"] = time.time()
        _simplize_cache[cache_key] = data
        return data
    except _httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Simplize API timeout")


@app.get("/api/stock/chart-data")
async def stock_chart_data(ticker: str, days: int = 90) -> dict[str, Any]:
    """OHLCV price history for a ticker, suitable for Recharts."""
    import json as _j
    raw = await server._vnstock_subprocess("quote_history_full", {"ticker": ticker.upper(), "days": days})
    rows = _j.loads(raw)
    if not rows or isinstance(rows, dict):
        raise HTTPException(status_code=404, detail=f"No price data for {ticker}")
    prices = [
        {
            "date":   r["time"][:10],
            "open":   round(float(r.get("open", 0)) * 1000),
            "high":   round(float(r.get("high", 0)) * 1000),
            "low":    round(float(r.get("low", 0)) * 1000),
            "close":  round(float(r.get("close", 0)) * 1000),
            "volume": int(r.get("volume", 0)),
        }
        for r in rows
        if r.get("time")
    ]
    return {"ticker": ticker.upper(), "prices": prices}


_foreign_flow_cache: dict[str, Any] = {}
_FOREIGN_FLOW_TTL = 300  # 5 minutes
_FLOW_STORE_DIR   = server.Path(__file__).parent / "analyses" / "foreign_flow"
_SIMPLIZE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://simplize.vn",
    "Origin": "https://simplize.vn",
}


def _flow_store_path(key: str) -> server.Path:
    _FLOW_STORE_DIR.mkdir(parents=True, exist_ok=True)
    return _FLOW_STORE_DIR / f"{key}.json"


def _load_flow_store(key: str) -> dict[str, Any]:
    path = _flow_store_path(key)
    if not path.exists():
        return {}
    try:
        return _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_flow_store(key: str, points: list[dict]) -> None:
    existing = _load_flow_store(key)
    by_date: dict[str, dict] = {p["date"]: p for p in existing.get("points", [])}
    for p in points:
        by_date[p["date"]] = p
    merged = sorted(by_date.values(), key=lambda x: x["date"])
    _flow_store_path(key).write_text(
        _json.dumps({"points": merged}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _parse_simplize_items(items: list) -> list[dict]:
    from datetime import datetime as _dt
    points = []
    for item in items:
        try:
            date_str  = _dt.strptime(item["date"], "%d/%m/%Y").strftime("%Y-%m-%d")
            net_val_b = round(float(item["totalBuySellValue"]) / 1e9, 2)
            net_vol   = round(float(item["buyQtt"]) - float(item["sellQtt"]))
            points.append({"date": date_str, "net_vol": net_vol, "net_val_b": net_val_b})
        except (KeyError, ValueError):
            continue
    return points


_VCI_IQ = "https://iq.vietcap.com.vn/api/iq-insight-service"
_annual_flow_cache: dict[str, Any] = {}
_ANNUAL_FLOW_TTL = 3600  # 1 hour
_VCI_IQ_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
}


@app.get("/api/stock/foreign-net-annual")
async def foreign_net_annual(ticker: str) -> dict[str, Any]:
    """Annual net foreign buy/sell (B VND) from VCI IQ insight service, 2019 → present."""
    import httpx as _httpx

    ticker = ticker.upper()
    cached = _annual_flow_cache.get(ticker)
    if cached and time.time() - cached["_ts"] < _ANNUAL_FLOW_TTL:
        return cached

    async with _httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(
            f"{_VCI_IQ}/v1/company/{ticker}/foreign-net",
            headers=_VCI_IQ_HEADERS, timeout=10,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"VCI IQ returned {r.status_code}")

    rows = r.json().get("data") or []
    points = []
    for row in rows:
        try:
            points.append({
                "year":       int(row["year"]),
                "net_val_b":  round(float(row["foreignNet"]) / 1e9, 1),
                "cum_val_b":  round(float(row["cumulativeNet"]) / 1e9, 1),
            })
        except (KeyError, ValueError):
            continue

    points.sort(key=lambda x: x["year"])
    result = {"ticker": ticker, "points": points, "_ts": time.time()}
    _annual_flow_cache[ticker] = result
    return result


@app.get("/api/market/foreign-net-annual")
async def market_foreign_net_annual() -> dict[str, Any]:
    """Annual net foreign buy/sell (B VND) aggregated across VN30 basket from VCI IQ, 2019 → present."""
    import httpx as _httpx
    from collections import defaultdict as _dd

    cached = _annual_flow_cache.get("__market_annual__")
    if cached and time.time() - cached["_ts"] < _ANNUAL_FLOW_TTL:
        return cached

    async def _fetch(client: _httpx.AsyncClient, ticker: str) -> list:
        try:
            r = await client.get(
                f"{_VCI_IQ}/v1/company/{ticker}/foreign-net",
                headers=_VCI_IQ_HEADERS, timeout=8,
            )
            return r.json().get("data") or [] if r.status_code == 200 else []
        except Exception:
            return []

    async with _httpx.AsyncClient(follow_redirects=True) as client:
        all_rows = await asyncio.gather(*[_fetch(client, t) for t in _VN30_BASKET])

    agg: dict[int, float] = _dd(float)
    for rows in all_rows:
        for row in rows:
            try:
                agg[int(row["year"])] += float(row["foreignNet"])
            except (KeyError, ValueError):
                continue

    # Build cumulative
    points = []
    cum = 0.0
    for year in sorted(agg):
        net = round(agg[year] / 1e9, 1)
        cum += net
        points.append({"year": year, "net_val_b": net, "cum_val_b": round(cum, 1)})

    result = {"ticker": "VN30-AGG", "points": points, "_ts": time.time()}
    _annual_flow_cache["__market_annual__"] = result
    return result


@app.get("/api/stock/foreign-flow-chart")
async def foreign_flow_chart(ticker: str) -> dict[str, Any]:
    """Historical net foreign buy/sell value (B VND). Fetches latest 10 sessions from Simplize
    and merges into a persistent local store so history accumulates across calls."""
    import httpx as _httpx

    ticker = ticker.upper()
    cached = _foreign_flow_cache.get(ticker)
    if cached and time.time() - cached["_ts"] < _FOREIGN_FLOW_TTL:
        return cached

    async with _httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(
            f"https://api2.simplize.vn/api/historical/foreign/trade/{ticker}",
            headers=_SIMPLIZE_HEADERS, timeout=10,
        )
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"Simplize returned {r.status_code}")

    payload    = r.json().get("data", {})
    fresh      = _parse_simplize_items(payload.get("items", []))
    statements = [{"text": s["description"], "isPass": s["isPass"]} for s in payload.get("statements", [])]

    _save_flow_store(ticker, fresh)
    all_points = _load_flow_store(ticker).get("points", fresh)

    result = {"ticker": ticker, "points": all_points, "statements": statements, "_ts": time.time()}
    _foreign_flow_cache[ticker] = result
    return result


_VN30_BASKET = [
    "VCB","BID","CTG","TCB","MBB","VPB","ACB","STB","SHB","HDB",
    "VIC","VHM","VRE","HPG","GAS","PLX","FPT","MWG","VNM","SAB",
    "MSN","VJC","REE","BVH","VND","SSI","HCM","NVL","PDR","POW",
]

_mkt_flow_cache: dict[str, Any] = {}


@app.get("/api/market/foreign-flow-chart")
async def market_foreign_flow_chart() -> dict[str, Any]:
    """Net foreign flow aggregated across VN30 basket (B VND). Fetches latest ~10 sessions and
    merges into a persistent store so history accumulates across calls."""
    import httpx as _httpx
    from collections import defaultdict as _dd

    cached = _mkt_flow_cache.get("market")
    if cached and time.time() - cached["_ts"] < _FOREIGN_FLOW_TTL:
        return cached

    async def _fetch(client: _httpx.AsyncClient, ticker: str) -> list:
        try:
            r = await client.get(
                f"https://api2.simplize.vn/api/historical/foreign/trade/{ticker}",
                headers=_SIMPLIZE_HEADERS, timeout=8,
            )
            return r.json().get("data", {}).get("items", []) if r.status_code == 200 else []
        except Exception:
            return []

    async with _httpx.AsyncClient(follow_redirects=True) as client:
        all_items = await asyncio.gather(*[_fetch(client, t) for t in _VN30_BASKET])

    # Aggregate fresh data by date across all tickers
    agg: dict[str, float] = _dd(float)
    for items in all_items:
        for p in _parse_simplize_items(items):
            agg[p["date"]] += p["net_val_b"]

    fresh = [{"date": d, "net_vol": 0, "net_val_b": round(v, 2)} for d, v in agg.items()]
    _save_flow_store("__market__", fresh)
    all_points = _load_flow_store("__market__").get("points", fresh)

    statements: list[dict] = []
    if all_points:
        last      = all_points[-1]
        total     = sum(p["net_val_b"] for p in all_points)
        direction = "net bought" if last["net_val_b"] >= 0 else "net sold"
        statements.append({
            "text": f"Foreigners {direction} {abs(last['net_val_b']):.1f}B VND across VN30 in the last session ({last['date']})",
            "isPass": last["net_val_b"] >= 0,
        })
        n         = len(all_points)
        total_dir = "net bought" if total >= 0 else "net sold"
        statements.append({
            "text": f"Cumulative over {n} sessions tracked: foreigners {total_dir} {abs(total):.1f}B VND across VN30",
            "isPass": total >= 0,
        })

    result = {"ticker": "VN30-AGG", "points": all_points, "statements": statements, "_ts": time.time()}
    _mkt_flow_cache["market"] = result
    return result


@app.get("/api/stock/overview-data")
async def stock_overview_data(ticker: str) -> dict[str, Any]:
    """Company overview as structured metrics — name, price, market cap, 52W range, etc."""
    import json as _j
    from datetime import date
    ov_raw, hist_raw = await asyncio.gather(
        server._vnstock_subprocess("company_overview", {"ticker": ticker.upper()}),
        server._vnstock_subprocess("quote_history_full", {"ticker": ticker.upper(), "days": 5}),
    )
    ov_rows = _j.loads(ov_raw)
    hist_rows = _j.loads(hist_raw)

    if not ov_rows or isinstance(ov_rows, dict):
        raise HTTPException(status_code=404, detail=f"No overview data for {ticker}")

    row = ov_rows[0]

    def _f(v, d=0.0):
        try: return float(v) if v is not None else d
        except: return d

    close = _f(hist_rows[-1].get("close")) * 1000 if hist_rows and not isinstance(hist_rows, dict) else 0.0
    prev  = _f(hist_rows[-2].get("close")) * 1000 if hist_rows and not isinstance(hist_rows, dict) and len(hist_rows) >= 2 else close
    change_pct = round((close - prev) / prev * 100, 2) if prev else 0.0

    return {
        "ticker":        ticker.upper(),
        "name":          row.get("organ_name", ticker),
        "sector":        row.get("sector", ""),
        "price":         round(close),
        "change_pct":    change_pct,
        "market_cap_t":  round(_f(row.get("market_cap")) / 1e12, 2),
        "high_52w":      round(_f(row.get("highest_price1_year"))),
        "low_52w":       round(_f(row.get("lowest_price1_year"))),
        "rating":        row.get("rating", ""),
        "target_price":  round(_f(row.get("target_price"))),
        "foreign_pct":   round(_f(row.get("foreigner_percentage")) * 100, 1),
    }


@app.get("/api/stock/income-trend")
async def stock_income_trend(ticker: str) -> dict[str, Any]:
    """Annual revenue and net income as parallel arrays for bar-charting."""
    import json as _j
    import pandas as _pd
    raw = await server._vnstock_subprocess("income_statement", {"ticker": ticker.upper(), "period": "year"})
    if not raw.strip().startswith("["):
        raise HTTPException(status_code=404, detail=f"No income data for {ticker}")

    df = _pd.DataFrame(_j.loads(raw))
    if df.empty:
        raise HTTPException(status_code=404, detail="Empty income statement")

    year_cols = sorted([c for c in df.columns if str(c).isdigit() or (isinstance(c, int) and c > 2000)], key=str)

    def _extract(item_id: str) -> list[float | None]:
        rows = df[df["item_id"] == item_id] if "item_id" in df.columns else _pd.DataFrame()
        if rows.empty:
            return [None] * len(year_cols)
        r = rows.iloc[0]
        return [round(float(r[c]) / 1e9, 1) if r.get(c) is not None else None for c in year_cols]

    return {
        "ticker":     ticker.upper(),
        "years":      [str(y) for y in year_cols],
        "revenue":    _extract("net_sales"),
        "net_income": _extract("net_profit_loss_after_tax"),
        "gross_profit": _extract("gross_profit"),
    }


@app.get("/api/stock/technical-data")
async def stock_technical_data(ticker: str) -> dict[str, Any]:
    """Full technical analysis as structured JSON — all indicator values, no markdown."""
    import json as _j
    import pandas as _pd
    try:
        import pandas_ta as _ta
    except ImportError:
        raise HTTPException(status_code=500, detail="pandas_ta not installed")

    raw = await server._vnstock_subprocess("quote_history_full", {"ticker": ticker.upper(), "days": 365})
    rows = _j.loads(raw)
    if not rows or isinstance(rows, dict):
        raise HTTPException(status_code=404, detail=f"No price data for {ticker}")

    df = _pd.DataFrame(rows)
    df["close"]  = df["close"].astype(float) * 1000
    df["open"]   = df["open"].astype(float) * 1000
    df["high"]   = df["high"].astype(float) * 1000
    df["low"]    = df["low"].astype(float) * 1000
    df["volume"] = df["volume"].astype(float)
    df = df.sort_values("time").reset_index(drop=True)

    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]
    n     = len(df)
    price = float(close.iloc[-1])

    def _f(s): return float(s.iloc[-1]) if s is not None and not s.empty else None

    ma20  = float(close.rolling(20).mean().iloc[-1])  if n >= 20  else None
    ma50  = float(close.rolling(50).mean().iloc[-1])  if n >= 50  else None
    ma200 = float(close.rolling(200).mean().iloc[-1]) if n >= 200 else None

    rsi_val  = _f(_ta.rsi(close, length=14))
    macd_df  = _ta.macd(close, fast=12, slow=26, signal=9)
    macd_val = float(macd_df.iloc[-1, 0]) if macd_df is not None and not macd_df.empty else None
    macd_sig = float(macd_df.iloc[-1, 1]) if macd_df is not None and not macd_df.empty else None
    macd_hist= float(macd_df.iloc[-1, 2]) if macd_df is not None and not macd_df.empty else None

    bb = _ta.bbands(close, length=20, std=2)
    bb_lower = float(bb.iloc[-1, 0]) if bb is not None and not bb.empty else None
    bb_mid   = float(bb.iloc[-1, 1]) if bb is not None and not bb.empty else None
    bb_upper = float(bb.iloc[-1, 2]) if bb is not None and not bb.empty else None
    bb_pct   = (price - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper and bb_lower and bb_upper != bb_lower else None

    atr_val   = _f(_ta.atr(high, low, close, length=14))
    vol_ma20  = float(vol.rolling(20).mean().iloc[-1]) if n >= 20 else float(vol.mean())
    vol_last  = float(vol.iloc[-1])
    vol_ratio = vol_last / vol_ma20 if vol_ma20 else 1.0

    recent   = df.tail(60)
    resist   = float(recent["high"].max())
    support  = float(recent["low"].min())
    pivot    = (resist + support + float(recent["close"].iloc[-1])) / 3
    w52_high = float(high.max())
    w52_low  = float(low.min())

    # Score
    signals = []
    signals.append(1 if ma20 and price > ma20 else -1)
    signals.append(1 if ma50 and price > ma50 else -1)
    signals.append(1 if ma200 and price > ma200 else -1)
    if rsi_val:
        signals.append(2 if rsi_val < 30 else -2 if rsi_val > 70 else 0)
    if macd_hist is not None:
        signals.append(1 if macd_hist > 0 else -1)
    score = sum(signals)
    if score >= 3:    verdict = "BULLISH"
    elif score >= 1:  verdict = "MILD_BULLISH"
    elif score == 0:  verdict = "NEUTRAL"
    elif score >= -2: verdict = "MILD_BEARISH"
    else:             verdict = "BEARISH"

    return {
        "ticker":  ticker.upper(),
        "n_days":  n,
        "verdict": verdict,
        "score":   score,
        "max_score": len(signals),
        "price":   round(price),
        "mas": {
            "ma20":  round(ma20)  if ma20  else None,
            "ma50":  round(ma50)  if ma50  else None,
            "ma200": round(ma200) if ma200 else None,
            "pct_from_ma20":  round((price - ma20)  / ma20  * 100, 1) if ma20  else None,
            "pct_from_ma50":  round((price - ma50)  / ma50  * 100, 1) if ma50  else None,
            "pct_from_ma200": round((price - ma200) / ma200 * 100, 1) if ma200 else None,
        },
        "rsi":  round(rsi_val, 1) if rsi_val else None,
        "macd": {
            "macd":   round(macd_val,  1) if macd_val  is not None else None,
            "signal": round(macd_sig,  1) if macd_sig  is not None else None,
            "hist":   round(macd_hist, 1) if macd_hist is not None else None,
        },
        "bb": {
            "upper": round(bb_upper) if bb_upper else None,
            "mid":   round(bb_mid)   if bb_mid   else None,
            "lower": round(bb_lower) if bb_lower else None,
            "pct_b": round(bb_pct, 1) if bb_pct is not None else None,
        },
        "atr":  round(atr_val) if atr_val else None,
        "atr_pct": round(atr_val / price * 100, 2) if atr_val else None,
        "volume": {
            "last":   round(vol_last),
            "avg20":  round(vol_ma20),
            "ratio":  round(vol_ratio, 2),
        },
        "levels": {
            "resistance": round(resist),
            "pivot":      round(pivot),
            "support":    round(support),
            "w52_high":   round(w52_high),
            "w52_low":    round(w52_low),
            "pct_from_high": round((price - w52_high) / w52_high * 100, 1),
            "pct_from_low":  round((price - w52_low)  / w52_low  * 100, 1),
        },
    }


# ── Reports & knowledge corpus ───────────────────────────────────────────────

from pathlib import Path
from fastapi import UploadFile, File, Form
import tempfile, shutil

ANALYSES_DIR = server.ANALYSES_DIR


MASVN_CATEGORIES = {
    "weekly": 23,
    "daily":  22,
    "macro":  28,
}
_broker_feed_cache: dict[str, Any] = {}
_BROKER_FEED_TTL = 1800  # 30 min


@app.get("/api/broker/feed")
async def broker_feed(broker: str = "masvn", category: str = "weekly", limit: int = 20) -> dict[str, Any]:
    """Fetch latest broker research reports from public broker APIs."""
    import httpx as _httpx
    import json as _j

    cache_key = f"{broker}_{category}_{limit}"
    cached = _broker_feed_cache.get(cache_key)
    if cached and time.time() - cached.get("_ts", 0) < _BROKER_FEED_TTL:
        return cached

    if broker == "masvn":
        cat_id = MASVN_CATEGORIES.get(category, 23)
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.masvn.com",
        }
        async with _httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(
                f"https://masvn.com/api/categories/fe/{cat_id}/article"
                f"?paging=1&sort=published_at&direction=desc&active=1&page=1&limit={limit}",
                headers=headers, timeout=12,
            )
        if r.status_code != 200:
            raise HTTPException(status_code=r.status_code, detail=f"MASVN API error {r.status_code}")

        items = r.json().get("data", [])
        total = r.json().get("total", 0)

        def _parse_title(raw: Any) -> dict:
            if isinstance(raw, str):
                try: return _j.loads(raw)
                except: return {"vi": raw}
            return raw or {}

        reports = []
        for item in items:
            t = _parse_title(item.get("title", {}))
            s = _parse_title(item.get("slug", {}))
            slug_vi = s.get("vi", "")
            reports.append({
                "id":          item.get("id"),
                "title_vi":    t.get("vi", ""),
                "title_en":    t.get("en", ""),
                "description": item.get("description") or "",
                "date":        (item.get("published_at") or "")[:10],
                "pdf_url":     f"https://www.masvn.com{item['file_path']}" if item.get("file_path") else "",
                "page_url":    f"https://www.masvn.com/vi/post/{item['id']}",
                "thumbnail":   f"https://www.masvn.com{item['thumbnail']}" if item.get("thumbnail") else "",
                "broker":      "Mirae Asset Securities Vietnam",
                "broker_short": "MASVN",
            })

        result = {"broker": "masvn", "category": category, "total": total, "reports": reports, "_ts": time.time()}
        _broker_feed_cache[cache_key] = result
        return result

    raise HTTPException(status_code=400, detail=f"Unsupported broker: {broker}")


@app.post("/api/broker/summarize")
async def broker_summarize(body: dict[str, Any]) -> dict[str, Any]:
    """Use OpenAI to generate a brief English summary of a broker report from its title."""
    import os as _os
    api_key = _os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY not set")

    title    = body.get("title", "")
    broker   = body.get("broker", "")
    date     = body.get("date", "")
    category = body.get("category", "")

    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    cache_key = f"summary_{hash(title)}"
    cached = _broker_feed_cache.get(cache_key)
    if cached and time.time() - cached.get("_ts", 0) < 86400:
        return cached

    try:
        import openai as _openai
        client = _openai.OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=200,
            messages=[
                {"role": "system", "content": (
                    "You are a Vietnamese equity research analyst. Given a broker report title (in Vietnamese), "
                    "write a concise 2-3 sentence English summary of what the report is likely about: "
                    "the market context, key theme, and likely recommendation. Be specific and actionable. "
                    "Do not say 'the report discusses' — just state the insights directly."
                )},
                {"role": "user", "content": (
                    f"Broker: {broker}\n"
                    f"Date: {date}\n"
                    f"Category: {category}\n"
                    f"Report title: {title}"
                )},
            ],
        )
        summary = resp.choices[0].message.content.strip()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    result = {"summary": summary, "_ts": time.time()}
    _broker_feed_cache[cache_key] = result
    return result


@app.get("/api/reports/analyses")
async def list_analyses() -> dict[str, Any]:
    """List all saved markdown analyses from the analyses/ folder."""
    import re
    files = sorted(ANALYSES_DIR.glob("*.md"), reverse=True)
    result = []
    for f in files:
        if f.name == "INDEX.md":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        # Extract first heading as title
        m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = m.group(1) if m else f.stem
        # Extract period and date from filename: TICKER_PERIOD_DATE.md
        parts = f.stem.split("_")
        ticker = parts[0] if parts else ""
        date   = parts[-1] if len(parts) >= 2 else ""
        result.append({
            "filename": f.name,
            "title":    title,
            "ticker":   ticker,
            "date":     date,
            "size_kb":  round(f.stat().st_size / 1024, 1),
        })
    return {"analyses": result}


@app.get("/api/reports/analyses/{filename}")
async def get_analysis(filename: str) -> dict[str, Any]:
    """Return full markdown content of a saved analysis."""
    path = ANALYSES_DIR / filename
    if not path.exists() or not path.suffix == ".md" or ".." in filename:
        raise HTTPException(status_code=404, detail="Not found")
    return {"filename": filename, "content": path.read_text(encoding="utf-8", errors="replace")}


@app.get("/api/reports/corpus")
async def list_corpus_reports() -> dict[str, Any]:
    """List ingested filings and papers from the knowledge corpus."""
    from knowledge.lib.corpus import iter_sources
    sources = list(iter_sources(category="filings")) + list(iter_sources(category="papers"))
    sources.sort(key=lambda s: s.ingested_at or "", reverse=True)
    return {
        "reports": [
            {
                "id":           s.id,
                "title":        s.title,
                "source":       s.source_name,
                "url":          s.url,
                "pub_date":     s.pub_date,
                "ingested_at":  s.ingested_at,
                "tickers":      s.tickers,
                "category":     s.category,
                "language":     s.language,
            }
            for s in sources[:100]
        ],
        "total": len(sources),
    }


@app.post("/api/reports/import-url")
async def import_url_report(body: dict[str, Any]) -> dict[str, Any]:
    """Ingest a broker report URL into the knowledge corpus."""
    url      = (body.get("url") or "").strip()
    source   = (body.get("source") or "").strip()
    ticker   = (body.get("ticker") or "").strip().upper()
    category = body.get("category", "filings")

    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    import subprocess as _sp
    cmd = [
        str(Path(__file__).resolve().parent / ".venv" / "bin" / "python"),
        "-m", "knowledge.pipelines.ingest_url",
        url,
        "--category", category,
    ]
    if source: cmd += ["--source", source]
    if ticker: cmd += ["--tickers", ticker]

    try:
        proc = _sp.run(cmd, capture_output=True, text=True, timeout=60,
                       cwd=str(Path(__file__).resolve().parent))
        return {
            "ok":     proc.returncode == 0,
            "stdout": proc.stdout[-2000:],
            "stderr": proc.stderr[-500:],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/reports/import-pdf")
async def import_pdf_report(
    file:     UploadFile = File(...),
    source:   str = Form(""),
    ticker:   str = Form(""),
    category: str = Form("filings"),
    language: str = Form("vi"),
) -> dict[str, Any]:
    """Upload a PDF broker report and ingest it into the knowledge corpus."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Must be a .pdf file")

    import subprocess as _sp

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name

    try:
        cmd = [
            str(Path(__file__).resolve().parent / ".venv" / "bin" / "python"),
            "-m", "knowledge.pipelines.ingest_pdf",
            tmp_path,
            "--category", category,
            "--language", language,
        ]
        if source: cmd += ["--source", source]
        if ticker: cmd += ["--tickers", ticker]

        proc = _sp.run(cmd, capture_output=True, text=True, timeout=120,
                       cwd=str(Path(__file__).resolve().parent))
        return {
            "ok":       proc.returncode == 0,
            "filename": file.filename,
            "stdout":   proc.stdout[-2000:],
            "stderr":   proc.stderr[-500:],
        }
    finally:
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="127.0.0.1", port=8000, reload=True)
