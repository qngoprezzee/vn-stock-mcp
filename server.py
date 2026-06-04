import asyncio
import base64
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import fitz  # pymupdf
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

app = Server("vn-stock-mcp")

# vnstock's quota library calls sys.exit() on rate limit, killing the whole process.
# We isolate every vnstock call in a child subprocess so exits are contained.
# Each subprocess runs a small JSON-in / JSON-out helper script.

_VNSTOCK_HELPER = Path(__file__).parent / "_vnstock_worker.py"

# Limit concurrent vnstock subprocess spawns to avoid system overload and rate limits.
_SUBPROCESS_SEM = asyncio.Semaphore(6)

# File-based cache for vnstock responses — cuts latency and rate-limit risk.
_CACHE_DIR = Path(__file__).parent / ".cache"
_CACHE_TTL: dict[str, int] = {
    "company_overview":   3600,
    "company_news":       1800,
    "company_events":     3600,
    "quote_history":       300,
    "quote_history_full": 3600,
    "income_statement":  86400,
    "balance_sheet":     86400,
    "cash_flow":         86400,
    "price_board":         60,
}
_DEFAULT_TTL = 3600


def _cache_key(func_name: str, kwargs: dict) -> str:
    payload = func_name + "|" + json.dumps(kwargs, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _cache_get(func_name: str, kwargs: dict) -> str | None:
    path = _CACHE_DIR / f"{_cache_key(func_name, kwargs)}.json"
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    ttl = _CACHE_TTL.get(func_name, _DEFAULT_TTL)
    if time.time() - entry.get("timestamp", 0) > ttl:
        return None
    return entry.get("data")


def _cache_set(func_name: str, kwargs: dict, data: str) -> None:
    # Never cache error payloads
    if data.lstrip().startswith("{") and '"error"' in data[:100]:
        return
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _CACHE_DIR / f"{_cache_key(func_name, kwargs)}.json"
    try:
        path.write_text(
            json.dumps({"timestamp": time.time(), "data": data}),
            encoding="utf-8",
        )
    except OSError:
        pass


async def _vnstock_subprocess(func_name: str, kwargs: dict, retries: int = 3) -> str:
    """Run a named vnstock function in an isolated subprocess. Returns JSON string. Cached."""
    cached = _cache_get(func_name, kwargs)
    if cached is not None:
        return cached

    async with _SUBPROCESS_SEM:
        for attempt in range(retries):
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(_VNSTOCK_HELPER), func_name, json.dumps(kwargs),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                lines = stdout.decode(errors="ignore").splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith("[") or line.startswith("{"):
                        _cache_set(func_name, kwargs, line)
                        return line
                return "[]"
            err = stderr.decode()
            if "Rate limit" in err or "RateLimit" in err:
                wait = 65 if attempt == 0 else 30
                await asyncio.sleep(wait)
            else:
                return json.dumps({"error": err[:300]})
    return json.dumps({"error": "Rate limit persisted after retries"})


def pdf_to_images(pdf_bytes: bytes, max_pages: int = 20) -> list[dict]:
    """Convert PDF bytes to a list of base64-encoded PNG images."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    images = []
    total = min(len(doc), max_pages)
    for i in range(total):
        page = doc[i]
        mat = fitz.Matrix(2.0, 2.0)  # 2x zoom for legibility
        pix = page.get_pixmap(matrix=mat)
        png_bytes = pix.tobytes("png")
        images.append({
            "page": i + 1,
            "total_pages": len(doc),
            "data": base64.standard_b64encode(png_bytes).decode("utf-8"),
        })
    doc.close()
    return images


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="load_financial_pdf",
            description=(
                "Load a financial statement PDF from a local file path or URL. "
                "Returns each page as an image so you can visually read and analyze "
                "the financial data (income statement, balance sheet, cash flow, etc.). "
                "Use this for VN company annual reports and quarterly financial statements."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Absolute local file path (e.g. /Users/you/fpt_2024.pdf) or HTTPS URL to the PDF.",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum number of pages to return (default 20, max 40).",
                        "default": 20,
                    },
                },
                "required": ["source"],
            },
        ),
        types.Tool(
            name="get_stock_overview",
            description=(
                "Get a quick overview of a Vietnam-listed stock: current price, "
                "market cap, P/E, P/B, 52-week range, and exchange (HOSE/HNX/UPCOM). "
                "Ticker examples: VIC, FPT, HPG, VNM, MWG."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    }
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_financial_data",
            description=(
                "Fetch structured financial statements for a VN-listed company: "
                "income statement, balance sheet, and cash flow statement. "
                "Returns multiple periods so you can spot trends."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "period": {
                        "type": "string",
                        "enum": ["year", "quarter"],
                        "description": "Annual or quarterly data (default: year).",
                        "default": "year",
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="save_analysis",
            description=(
                "Save a completed stock analysis as a Markdown file in the project's analyses/ folder. "
                "Call this AFTER finishing the analysis to persist it as memory for future sessions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full analysis in Markdown format.",
                    },
                    "period": {
                        "type": "string",
                        "description": "Report period label, e.g. 'Q1-2026' or '2025-annual'. Used in filename.",
                        "default": "",
                    },
                },
                "required": ["ticker", "content"],
            },
        ),
        types.Tool(
            name="get_analysis_prompt",
            description=(
                "Returns a structured analysis framework to guide a deep-dive of a VN stock. "
                "Call this FIRST when the user asks to 'analyze' a stock, then use the other "
                "tools to gather data and follow the framework step by step."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["full", "quick", "pdf"],
                        "description": (
                            "full = structured data + PDF report (default); "
                            "quick = structured data only, no PDF; "
                            "pdf = PDF report only."
                        ),
                        "default": "full",
                    },
                    "pdf_path": {
                        "type": "string",
                        "description": "Optional path or URL to a financial statement PDF for pdf/full modes.",
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_technical_analysis",
            description=(
                "Compute technical analysis for a VN-listed stock using up to 1 year of daily price data. "
                "Returns trend, moving averages (MA20/50/200), RSI, MACD, Bollinger Bands, ATR, "
                "volume profile, support/resistance levels, and an overall technical signal."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "days": {
                        "type": "integer",
                        "description": "Number of trading days of history to use (default 365).",
                        "default": 365,
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="fetch_broker_news",
            description=(
                "Fetch recent news, corporate events, insider trades, and analyst consensus "
                "for a VN-listed stock. Aggregates from FiinGroup (via vnstock) which covers "
                "disclosures from SSI, TCBS, Mirae Asset, VCBS and other local brokers. "
                "Optionally load a broker research report PDF by providing its URL or local path."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of recent news items to return (default 15).",
                        "default": 15,
                    },
                    "broker_pdf_url": {
                        "type": "string",
                        "description": "Optional: URL or local path to a broker research report PDF to load alongside the news.",
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="compare_stocks",
            description=(
                "Fetch and compare key financial metrics side-by-side for multiple VN-listed stocks. "
                "Use this to rank peers by valuation, profitability, growth, and financial health. "
                "Returns a structured comparison table ready for expert analysis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "tickers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of VN stock tickers to compare (e.g. ['FPT', 'CMG', 'VGI']).",
                        "minItems": 2,
                        "maxItems": 8,
                    },
                    "period": {
                        "type": "string",
                        "enum": ["year", "quarter"],
                        "default": "year",
                        "description": "Annual or most-recent-quarter comparison.",
                    },
                },
                "required": ["tickers"],
            },
        ),
        types.Tool(
            name="get_macro_data",
            description=(
                "Fetch live Vietnamese macroeconomic data: USD/VND and major currency exchange rates "
                "from Vietcombank, plus the SBV base interest rate context. "
                "Use this when analyzing currency risk, import/export companies, or macro environment."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="get_commodity_prices",
            description=(
                "Fetch live commodity prices relevant to Vietnam: SJC gold (miếng), BTMC gold, "
                "silver, and key precious metals in VND per lượng. "
                "Use this for gold-related stocks (PNJ, SJC), inflation analysis, or macro context."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="get_market_news",
            description=(
                "Crawl Vietnamese financial news sites (CafeF, Tin Nhanh Chứng Khoán, VnExpress, "
                "Vietnam Investment Review, VietStock) via RSS and return recent articles that mention "
                "the stock ticker. Complements fetch_broker_news (which pulls from vnstock/FiinGroup) "
                "with broader editorial coverage from independent news outlets. "
                "Use this to gauge media sentiment, spot breaking news, or find analyst commentary "
                "not covered by broker disclosures."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of articles to return (default 20).",
                        "default": 20,
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_market_overview",
            description=(
                "Show how the Vietnamese stock market is performing today. "
                "Returns VN-Index, HNX-Index, and UPCOM index levels with today's change (points and %), "
                "plus top gainers and losers from major large-cap stocks. "
                "Use this for a quick market pulse check before or after a session."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        types.Tool(
            name="get_economy_news",
            description=(
                "Fetch today's top economic and financial headlines from high-signal Vietnamese sources: "
                "VnEconomy (tạp chí Kinh tế Việt Nam), Báo Đầu tư, CafeF, Tin Nhanh Chứng Khoán, "
                "VnExpress Business, and Vietnam Investment Review. "
                "Returns a balanced feed of general market-moving news — macro policy, banking, "
                "corporate events, FDI, interest rates — not filtered by ticker. "
                "Use this for a broad economic pulse or when the user asks 'what's happening in the economy today'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max headlines to return (default 20).",
                        "default": 20,
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="get_dcf_valuation",
            description=(
                "Triangulated intrinsic value for a VN-listed stock combining (1) DCF with "
                "bull/base/bear scenarios, (2) peer-relative valuation via median P/E + P/B + EV/EBITDA, "
                "and (3) a 5×5 WACC × terminal-growth sensitivity grid. Returns a blended implied "
                "price weighted by sector (e.g. banks lean relative-heavy; staples lean DCF-heavy) "
                "plus an opinionated UNDER/FAIR/OVERVALUED verdict. "
                "Defaults: 12% WACC, 5% terminal growth, default peer set per sector."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "discount_rate": {
                        "type": "number",
                        "description": "WACC / required return in % (default 12).",
                        "default": 12.0,
                    },
                    "terminal_growth": {
                        "type": "number",
                        "description": "Long-term terminal growth rate in % (default 5).",
                        "default": 5.0,
                    },
                    "bull_growth": {
                        "type": "number",
                        "description": "Annual FCF growth in % for bull scenario (default 20).",
                        "default": 20.0,
                    },
                    "base_growth": {
                        "type": "number",
                        "description": "Annual FCF growth in % for base scenario (default 12).",
                        "default": 12.0,
                    },
                    "bear_growth": {
                        "type": "number",
                        "description": "Annual FCF growth in % for bear scenario (default 5).",
                        "default": 5.0,
                    },
                    "projection_years": {
                        "type": "integer",
                        "description": "Years to project FCF (default 5).",
                        "default": 5,
                    },
                    "peers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional explicit peer tickers for relative valuation. If omitted, default peer set for the sector is used.",
                    },
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_position_sizing",
            description=(
                "Calculate optimal position size for a VN stock trade using ATR-based stop-loss "
                "and fixed-fractional risk management. Returns max shares, position value, "
                "portfolio weight, stop-loss level, and risk/reward at key targets. "
                "Use this (Phase 3) before entering any new position to enforce capital discipline."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "portfolio_value": {
                        "type": "number",
                        "description": "Total portfolio value in VND (e.g. 500000000 for 500M VND).",
                    },
                    "risk_per_trade_pct": {
                        "type": "number",
                        "description": "Max % of portfolio to risk on this trade (default 2.0).",
                        "default": 2.0,
                    },
                    "conviction": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "description": "Conviction level — scales risk: low 0.5x, medium 1x, high 1.5x.",
                        "default": "medium",
                    },
                    "atr_multiplier": {
                        "type": "number",
                        "description": "ATR multiplier for stop-loss distance from entry (default 2.0).",
                        "default": 2.0,
                    },
                },
                "required": ["ticker", "portfolio_value"],
            },
        ),
        types.Tool(
            name="save_investment_thesis",
            description=(
                "Save a structured investment thesis for a VN stock to the theses/ folder. "
                "Captures the investment rationale, price targets, stop-loss, conviction level, "
                "and — critically — falsification criteria: the specific conditions that would break the thesis. "
                "Phase 4 discipline: always write the thesis before entering a position, "
                "and review it before adding to or exiting a position."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "thesis": {
                        "type": "string",
                        "description": "Core investment thesis — why you're buying, the moat, and what must remain true.",
                    },
                    "buy_price": {
                        "type": "number",
                        "description": "Entry price or range in VND.",
                    },
                    "target_price": {
                        "type": "number",
                        "description": "12-month price target in VND.",
                    },
                    "stop_price": {
                        "type": "number",
                        "description": "Stop-loss / exit price in VND — the line where the thesis is broken.",
                    },
                    "conviction": {
                        "type": "string",
                        "enum": ["Low", "Medium", "High"],
                        "description": "Conviction level (default Medium).",
                        "default": "Medium",
                    },
                    "falsification_criteria": {
                        "type": "string",
                        "description": "Specific, testable conditions that invalidate this thesis (e.g. 'ROE drops below 15%', 'revenue growth < 10% for 2 consecutive quarters').",
                    },
                    "catalysts": {
                        "type": "string",
                        "description": "2-3 upcoming events that could prove the thesis right.",
                        "default": "",
                    },
                    "strongest_bias": {
                        "type": "string",
                        "description": "Pre-mortem: which cognitive bias is most likely affecting this thesis? (e.g. 'recency bias from recent rally', 'confirmation bias — I want this to work', 'anchoring on prior target').",
                        "default": "",
                    },
                    "premortem_reason": {
                        "type": "string",
                        "description": "Pre-mortem: if this thesis is wrong 12 months from now, what is the SINGLE most likely reason? Be specific.",
                        "default": "",
                    },
                },
                "required": ["ticker", "thesis", "buy_price", "target_price", "stop_price", "falsification_criteria"],
            },
        ),
        types.Tool(
            name="get_earnings_quality",
            description=(
                "Score earnings quality for a VN-listed stock on five dimensions: FCF/NI ratio, "
                "accruals ratio (Sloan), OCF margin, working capital trend, and OCF coverage. "
                "Returns a 0-100 quality score with verdict. Phase 2 tool — separates genuine cash earnings "
                "from accounting-driven profit. Lower accruals = higher quality."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "VN ticker symbol (e.g. FPT)."},
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_foreign_flow",
            description=(
                "Show foreign investor activity for a VN-listed stock: current ownership %, "
                "foreign room remaining, and today's foreign buy/sell snapshot from price_board. "
                "Foreign net flow is one of the strongest leading signals on HOSE — "
                "sustained foreign accumulation in large-caps often precedes price moves."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "VN ticker symbol (e.g. FPT)."},
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="get_vn_macro_indicators",
            description=(
                "Fetch Vietnam macroeconomic indicators from the World Bank API: GDP growth rate, "
                "CPI inflation, real interest rate, and unemployment for the last ~10 years. "
                "Phase 1 macro context — use to spot regime shifts (inflation rising, growth slowing) "
                "before they show up in stock prices."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="get_quality_score",
            description=(
                "Compute a single 0-100 quality score for a VN stock from: ROIC, FCF/NI, debt/equity, "
                "revenue CAGR, and gross margin stability. Phase 4 pattern recognition tool — use to "
                "screen for compounders quickly and rank watchlist candidates."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "VN ticker symbol (e.g. FPT)."},
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="stress_test_portfolio",
            description=(
                "Simulate portfolio P&L under three market shock scenarios: -10%, -20%, -30% VN-Index decline. "
                "Applies sector beta proxies (banking 1.1, real estate 1.4, tech 1.2, staples 0.7) to each holding. "
                "Returns total loss in VND, by-position breakdown, and triggers drawdown rule warnings."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "holdings": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "ticker":    {"type": "string"},
                                "shares":    {"type": "number"},
                                "avg_cost":  {"type": "number"},
                            },
                            "required": ["ticker", "shares", "avg_cost"],
                        },
                        "description": "List of holdings, e.g. [{\"ticker\":\"FPT\",\"shares\":1000,\"avg_cost\":130000}].",
                    },
                },
                "required": ["holdings"],
            },
        ),
        types.Tool(
            name="manage_watchlist",
            description=(
                "Add, remove, or list tickers in your personal watchlist (stored in .watchlist.json). "
                "Use `check_watchlist` afterwards to scan all tickers for technical triggers."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "remove", "list", "clear"]},
                    "ticker": {"type": "string", "description": "Required for add/remove. Ignored for list/clear."},
                },
                "required": ["action"],
            },
        ),
        types.Tool(
            name="check_watchlist",
            description=(
                "Scan every ticker in your watchlist for actionable technical triggers: RSI <30 (oversold), "
                "RSI >70 (overbought), MA50 break (above or below), and >5% daily moves. "
                "Run at the start of every session to surface what's worth attention."
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="thesis_context",
            description=(
                "Bundle pre-thesis context for a VN ticker: recent news mentioning it, your existing "
                "analyses/theses, and matching sector principles from the knowledge base (Buffett, Marks, "
                "Damodaran). Call this FIRST when writing a new thesis or revisiting an existing position — "
                "it surfaces what you already know and what the corpus says about similar businesses, "
                "saving you 30-60 min of recall."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {"type": "string", "description": "VN ticker symbol (uppercase, e.g. FPT)."},
                    "lookback_days": {"type": "integer", "default": 30, "description": "How far back to pull news (default 30)."},
                    "max_articles": {"type": "integer", "default": 15, "description": "Cap on recent articles included (default 15)."},
                    "include_sector_principles": {"type": "boolean", "default": True,
                                                  "description": "Include matching passages from books/blogs (Buffett, Marks, etc.)."},
                },
                "required": ["ticker"],
            },
        ),
        types.Tool(
            name="compare_authors_on",
            description=(
                "Cross-reference engine: for a topic and a list of authors, pull every passage from each "
                "author's corpus discussing that topic. Use this to learn where investing legends actually "
                "DISAGREE — Marks vs Buffett on cyclicality, Damodaran vs Mauboussin on growth, etc. "
                "Returns a structured markdown block with passages grouped by author and ready for synthesis."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Topic to compare, e.g. 'cyclicality' or 'intrinsic value'."},
                    "authors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of author names (substring match, e.g. ['Warren Buffett', 'Howard Marks']).",
                        "minItems": 1,
                    },
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional explicit keyword list. If omitted, the topic plus naive synonyms are used.",
                    },
                    "context_paragraphs": {"type": "integer", "default": 2,
                                           "description": "Paragraphs of surrounding context per match (default 2)."},
                    "max_per_author": {"type": "integer", "default": 5,
                                       "description": "Cap on passages per author (default 5)."},
                },
                "required": ["topic", "authors"],
            },
        ),
        types.Tool(
            name="review_performance",
            description=(
                "Audit your decision journal: parse decisions/LOG.md, pair buys with sells to compute "
                "realized P&L, calculate win rate / expectancy / max consecutive losses, surface stale "
                "pending decisions (>90 days), cluster losses by ticker and hold period, and output "
                "an opinionated triage verdict (e.g. 'holding losers too long', 'low hit rate'). "
                "Phase 4 performance review — call this monthly or after every 10 closed trades."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "lookback_days": {
                        "type": "integer",
                        "description": "How many days back to include in the review (default 365).",
                        "default": 365,
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="save_decision_log",
            description=(
                "Log a buy/sell/add/trim/hold decision to decisions/LOG.md. "
                "Recording decisions with rationale at execution time is the foundation of Phase 4 "
                "performance review — it lets you audit your thinking vs. what actually happened. "
                "Call this every time you act on a position. Update the outcome field later when resolved."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "ticker": {
                        "type": "string",
                        "description": "VN stock ticker symbol (uppercase, e.g. FPT).",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["BUY", "SELL", "ADD", "TRIM", "HOLD"],
                        "description": "Action taken.",
                    },
                    "price": {
                        "type": "number",
                        "description": "Execution price in VND.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why you're taking this action right now — cite the specific evidence.",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "Number of shares (optional).",
                        "default": 0,
                    },
                    "outcome": {
                        "type": "string",
                        "description": "Leave blank for new entries. Fill in later: 'Correct — stock rose 25%' or 'Wrong — thesis broken at Q3 earnings'.",
                        "default": "",
                    },
                },
                "required": ["ticker", "action", "price", "rationale"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent | types.ImageContent]:
    if name == "load_financial_pdf":
        return await _load_financial_pdf(arguments)
    elif name == "get_stock_overview":
        return await _get_stock_overview(arguments)
    elif name == "get_financial_data":
        return await _get_financial_data(arguments)
    elif name == "get_analysis_prompt":
        return await _get_analysis_prompt(arguments)
    elif name == "save_analysis":
        return await _save_analysis(arguments)
    elif name == "get_technical_analysis":
        return await _get_technical_analysis(arguments)
    elif name == "fetch_broker_news":
        return await _fetch_broker_news(arguments)
    elif name == "compare_stocks":
        return await _compare_stocks(arguments)
    elif name == "get_macro_data":
        return await _get_macro_data(arguments)
    elif name == "get_commodity_prices":
        return await _get_commodity_prices(arguments)
    elif name == "get_market_news":
        return await _get_market_news(arguments)
    elif name == "get_market_overview":
        return await _get_market_overview(arguments)
    elif name == "get_economy_news":
        return await _get_economy_news(arguments)
    elif name == "get_dcf_valuation":
        return await _get_dcf_valuation(arguments)
    elif name == "get_position_sizing":
        return await _get_position_sizing(arguments)
    elif name == "save_investment_thesis":
        return await _save_investment_thesis(arguments)
    elif name == "save_decision_log":
        return await _save_decision_log(arguments)
    elif name == "review_performance":
        return await _review_performance(arguments)
    elif name == "get_earnings_quality":
        return await _get_earnings_quality(arguments)
    elif name == "get_foreign_flow":
        return await _get_foreign_flow(arguments)
    elif name == "get_vn_macro_indicators":
        return await _get_vn_macro_indicators(arguments)
    elif name == "get_quality_score":
        return await _get_quality_score(arguments)
    elif name == "stress_test_portfolio":
        return await _stress_test_portfolio(arguments)
    elif name == "manage_watchlist":
        return await _manage_watchlist(arguments)
    elif name == "check_watchlist":
        return await _check_watchlist(arguments)
    elif name == "thesis_context":
        return await _thesis_context(arguments)
    elif name == "compare_authors_on":
        return await _compare_authors_on(arguments)
    else:
        raise ValueError(f"Unknown tool: {name}")


async def _load_financial_pdf(args: dict) -> list:
    source: str = args["source"]
    max_pages: int = min(int(args.get("max_pages", 20)), 40)

    if source.startswith("http://") or source.startswith("https://"):
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            resp = await client.get(source)
            resp.raise_for_status()
            pdf_bytes = resp.content
    else:
        path = Path(source)
        if not path.exists():
            return [types.TextContent(type="text", text=f"File not found: {source}")]
        pdf_bytes = path.read_bytes()

    images = pdf_to_images(pdf_bytes, max_pages)

    result: list = [
        types.TextContent(
            type="text",
            text=f"Loaded PDF with {images[0]['total_pages']} total pages. Showing {len(images)} page(s). Read each page image carefully to extract financial figures.",
        )
    ]
    for img in images:
        result.append(
            types.ImageContent(
                type="image",
                data=img["data"],
                mimeType="image/png",
            )
        )
    return result


async def _get_stock_overview(args: dict) -> list[types.TextContent]:
    ticker: str = args["ticker"].upper()
    try:
        from datetime import date
        ov_json, hist_json = await asyncio.gather(
            _vnstock_subprocess("company_overview", {"ticker": ticker}),
            _vnstock_subprocess("quote_history", {"ticker": ticker, "start": "2026-01-01", "end": date.today().isoformat()}),
        )
        ov_rows = json.loads(ov_json)
        hist_rows = json.loads(hist_json)
        if not ov_rows or isinstance(ov_rows, dict):
            return [types.TextContent(type="text", text=f"Could not fetch overview for {ticker}: {ov_rows}")]

        row = ov_rows[0]
        def _f(v, d=0.0):
            try: return float(v) if v is not None else d
            except: return d

        if hist_rows and not isinstance(hist_rows, dict):
            latest_close = _f(hist_rows[-1].get("close")) * 1000
            prev_close   = _f(hist_rows[-2].get("close")) * 1000 if len(hist_rows) >= 2 else None
            change_pct   = (latest_close - prev_close) / prev_close * 100 if prev_close else None
        else:
            latest_close = _f(row.get("current_price"))
            change_pct   = None

        text = f"""Stock Overview: {ticker}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Company       : {row.get('organ_name', 'N/A')}
Sector        : {row.get('sector', 'N/A')}
Exchange      : HOSE

Price         : {latest_close:,.0f} VND{f'  ({change_pct:+.2f}%)' if change_pct is not None else ''}
52W High      : {_f(row.get('highest_price1_year')):,.0f} VND
52W Low       : {_f(row.get('lowest_price1_year')):,.0f} VND
Market Cap    : {_f(row.get('market_cap')) / 1e12:.2f} trillion VND

Analyst Rating: {row.get('rating', 'N/A')}
Target Price  : {_f(row.get('target_price')):,.0f} VND
Foreign Own % : {_f(row.get('foreigner_percentage')) * 100:.2f}%
"""
    except Exception as e:
        text = f"Could not fetch overview for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


INCOME_ITEMS = [
    "net_sales", "gross_profit", "operating_profit_loss",
    "net_profit_loss_after_tax", "attributable_to_parent_company",
    "eps_basic_vnd",
]
BALANCE_ITEMS = [
    "total_assets", "current_assets", "cash_and_cash_equivalents",
    "short_term_investments", "accounts_receivable", "inventories_net",
    "long_term_assets", "liabilities", "current_liabilities",
    "short_term_borrowings", "long_term_borrowings", "owners_equity",
]
CASHFLOW_ITEMS = [
    "net_cash_from_operating_activities", "net_cash_from_investing_activities",
    "net_cash_from_financing_activities", "net_increase_decrease_in_cash",
]


def _format_statement(df, key_items: list[str]) -> str:
    if df is None or df.empty:
        return "No data"

    year_cols = [c for c in df.columns if str(c).isdigit() or (isinstance(c, int))]
    if not year_cols:
        year_cols = [c for c in df.columns if c not in ("item", "item_en", "item_id", "period")]

    filtered = df[df["item_id"].isin(key_items)] if "item_id" in df.columns else df
    if filtered.empty:
        filtered = df

    rows = []
    for _, row in filtered.iterrows():
        label = str(row.get("item_en", row.get("item", ""))).strip()
        label = label[:35].ljust(36)
        values = []
        for col in year_cols:
            val = row.get(col, None)
            try:
                fval = float(val)
                if abs(fval) >= 1e9:
                    values.append(f"{fval/1e9:>12,.1f}B")
                elif "eps" in str(row.get("item_id", "")).lower():
                    values.append(f"{fval:>12,.0f}")
                else:
                    values.append(f"{fval/1e9:>12,.3f}B")
            except (TypeError, ValueError):
                values.append(f"{'N/A':>13}")
        rows.append(f"  {label} {'  '.join(values)}")

    header_cols = "  ".join(str(c)[:12].rjust(13) for c in year_cols)
    header = f"  {'Item':<36}  {header_cols}"
    divider = "  " + "-" * (36 + 15 * len(year_cols))
    return "\n".join([header, divider] + rows)


async def _get_financial_data(args: dict) -> list[types.TextContent]:
    ticker: str = args["ticker"].upper()
    period: str = args.get("period", "year")
    try:
        import pandas as pd
        kw = {"ticker": ticker, "period": period}
        inc_json, bal_json, cf_json = await asyncio.gather(
            _vnstock_subprocess("income_statement", kw),
            _vnstock_subprocess("balance_sheet", kw),
            _vnstock_subprocess("cash_flow", kw),
        )
        income   = pd.DataFrame(json.loads(inc_json)) if inc_json.strip().startswith("[") else pd.DataFrame()
        balance  = pd.DataFrame(json.loads(bal_json)) if bal_json.strip().startswith("[") else pd.DataFrame()
        cashflow = pd.DataFrame(json.loads(cf_json))  if cf_json.strip().startswith("[")  else pd.DataFrame()

        text = f"""Financial Statements: {ticker} ({period})  — figures in billions VND (B)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INCOME STATEMENT
{_format_statement(income, INCOME_ITEMS)}

BALANCE SHEET
{_format_statement(balance, BALANCE_ITEMS)}

CASH FLOW STATEMENT
{_format_statement(cashflow, CASHFLOW_ITEMS)}
"""
    except Exception as e:
        text = f"Could not fetch financial data for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


ANALYSES_DIR  = Path(__file__).parent / "analyses"
THESES_DIR    = Path(__file__).parent / "theses"
DECISIONS_DIR = Path(__file__).parent / "decisions"


async def _save_analysis(args: dict) -> list[types.TextContent]:
    ticker = args["ticker"].upper()
    content = args["content"]
    period = args.get("period", "").strip()

    ANALYSES_DIR.mkdir(exist_ok=True)

    from datetime import date
    date_str = date.today().isoformat()
    filename = f"{ticker}_{period}_{date_str}.md" if period else f"{ticker}_{date_str}.md"
    filepath = ANALYSES_DIR / filename

    header = f"# {ticker} Financial Analysis\n**Period:** {period or 'N/A'}  |  **Date:** {date_str}\n\n---\n\n"
    filepath.write_text(header + content, encoding="utf-8")

    # Update index file
    index_path = ANALYSES_DIR / "INDEX.md"
    index_line = f"- [{ticker} — {period or date_str}]({filename}) — saved {date_str}\n"
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
        if filename not in existing:
            index_path.write_text(existing + index_line, encoding="utf-8")
    else:
        index_path.write_text(f"# Analysis Index\n\n{index_line}", encoding="utf-8")

    return [types.TextContent(
        type="text",
        text=f"Analysis saved to {filepath}\nIndex updated at {index_path}",
    )]


async def _get_technical_analysis(args: dict) -> list[types.TextContent]:
    ticker = args["ticker"].upper()
    days = int(args.get("days", 365))
    try:
        import pandas as pd
        import pandas_ta as ta

        raw = await _vnstock_subprocess("quote_history_full", {"ticker": ticker, "days": days})
        rows = json.loads(raw)
        if not rows or isinstance(rows, dict):
            return [types.TextContent(type="text", text=f"No price data for {ticker}")]

        df = pd.DataFrame(rows)
        df["close"] = df["close"].astype(float) * 1000
        df["open"]  = df["open"].astype(float) * 1000
        df["high"]  = df["high"].astype(float) * 1000
        df["low"]   = df["low"].astype(float) * 1000
        df["volume"] = df["volume"].astype(float)
        df = df.sort_values("time").reset_index(drop=True)

        close = df["close"]
        high  = df["high"]
        low   = df["low"]
        vol   = df["volume"]
        n     = len(df)

        # ── Moving Averages ──────────────────────────────────────────────
        ma20  = close.rolling(20).mean().iloc[-1]  if n >= 20  else None
        ma50  = close.rolling(50).mean().iloc[-1]  if n >= 50  else None
        ma200 = close.rolling(200).mean().iloc[-1] if n >= 200 else None
        price = close.iloc[-1]

        def _ma_signal(p, ma, label):
            if ma is None: return f"{label}: N/A (insufficient data)"
            diff = (p - ma) / ma * 100
            sig = "↑ ABOVE" if p > ma else "↓ BELOW"
            return f"{label}: {ma:,.0f}  {sig}  ({diff:+.1f}%)"

        # ── RSI ──────────────────────────────────────────────────────────
        rsi_s = ta.rsi(close, length=14)
        rsi = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.empty else None

        # ── MACD ─────────────────────────────────────────────────────────
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            macd_val  = float(macd_df.iloc[-1, 0])
            macd_sig  = float(macd_df.iloc[-1, 1])
            macd_hist = float(macd_df.iloc[-1, 2])
        else:
            macd_val = macd_sig = macd_hist = None

        # ── Bollinger Bands ───────────────────────────────────────────────
        bb = ta.bbands(close, length=20, std=2)
        if bb is not None and not bb.empty:
            # pandas_ta returns: BBL (lower), BBM (mid), BBU (upper), BBB, BBP
            bb_lower = float(bb.iloc[-1, 0])
            bb_mid   = float(bb.iloc[-1, 1])
            bb_upper = float(bb.iloc[-1, 2])
            bb_pct   = (price - bb_lower) / (bb_upper - bb_lower) * 100 if bb_upper != bb_lower else 50
        else:
            bb_upper = bb_mid = bb_lower = bb_pct = None

        # ── ATR (volatility) ──────────────────────────────────────────────
        atr_s = ta.atr(high, low, close, length=14)
        atr   = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.empty else None

        # ── Volume ────────────────────────────────────────────────────────
        vol_ma20 = vol.rolling(20).mean().iloc[-1] if n >= 20 else vol.mean()
        vol_last = vol.iloc[-1]
        vol_ratio = vol_last / vol_ma20 if vol_ma20 else 1

        # ── Support / Resistance (simple pivot from last 20 days) ─────────
        recent = df.tail(60)
        resist = float(recent["high"].max())
        support = float(recent["low"].min())
        mid_pivot = (resist + support + float(recent["close"].iloc[-1])) / 3

        # ── 52-week hi/lo ──────────────────────────────────────────────────
        w52_high = float(high.max())
        w52_low  = float(low.min())
        pct_from_high = (price - w52_high) / w52_high * 100
        pct_from_low  = (price - w52_low)  / w52_low  * 100

        # ── Overall Signal ─────────────────────────────────────────────────
        signals = []
        if ma20 and price > ma20: signals.append(1)
        else: signals.append(-1)
        if ma50 and price > ma50: signals.append(1)
        else: signals.append(-1)
        if ma200 and price > ma200: signals.append(1)
        else: signals.append(-1)
        if rsi:
            if rsi < 30: signals.append(2)   # oversold — bullish
            elif rsi > 70: signals.append(-2) # overbought — bearish
            else: signals.append(0)
        if macd_hist is not None:
            signals.append(1 if macd_hist > 0 else -1)

        score = sum(signals)
        if score >= 3: overall = "🟢 BULLISH"
        elif score >= 1: overall = "🟡 MILD BULLISH"
        elif score == 0: overall = "⚪ NEUTRAL"
        elif score >= -2: overall = "🟠 MILD BEARISH"
        else: overall = "🔴 BEARISH"

        lines = [
            f"## Technical Analysis — {ticker}",
            f"**Overall Signal: {overall}** (score: {score:+d} / {len(signals)} factors)\n",

            "### Price vs Moving Averages",
            f"  Current Price : {price:,.0f} VND",
            f"  {_ma_signal(price, ma20,  'MA20 ')}",
            f"  {_ma_signal(price, ma50,  'MA50 ')}",
            f"  {_ma_signal(price, ma200, 'MA200')}",

            "\n### Momentum Indicators",
            f"  RSI (14)      : {f'{rsi:.1f}' if rsi else 'N/A'}"
            f"{'  ⚠️ OVERSOLD (<30)' if rsi and rsi < 30 else '  ⚠️ OVERBOUGHT (>70)' if rsi and rsi > 70 else '  (neutral 30-70)'}",
        ]

        if macd_val is not None:
            cross = "↑ Bullish crossover" if macd_val > macd_sig else "↓ Bearish crossover"
            lines.append(f"  MACD          : {macd_val:+.1f}  |  Signal: {macd_sig:+.1f}  |  Hist: {macd_hist:+.1f}  ({cross})")

        lines += [
            "\n### Bollinger Bands (20,2)",
        ]
        if bb_upper:
            squeeze = "🔴 Near upper band — overbought" if bb_pct > 80 else "🟢 Near lower band — oversold" if bb_pct < 20 else "Neutral"
            lines += [
                f"  Upper: {bb_upper:,.0f}  |  Mid: {bb_mid:,.0f}  |  Lower: {bb_lower:,.0f}",
                f"  %B   : {bb_pct:.1f}%  ({squeeze})",
            ]

        lines += [
            f"\n### Volatility",
            f"  ATR (14)      : {f'{atr:,.0f} VND' if atr else 'N/A'}  ({f'{atr/price*100:.1f}%' if atr else ''} of price)",
        ]

        lines += [
            "\n### Volume",
            f"  Last session  : {vol_last:,.0f}",
            f"  20-day avg    : {vol_ma20:,.0f}",
            f"  Relative vol  : {vol_ratio:.2f}x  {'📈 Above avg' if vol_ratio > 1.2 else '📉 Below avg' if vol_ratio < 0.8 else 'Normal'}",

            "\n### Key Levels",
            f"  Resistance (60d high) : {resist:,.0f} VND",
            f"  Pivot                 : {mid_pivot:,.0f} VND",
            f"  Support   (60d low)   : {support:,.0f} VND",
            f"\n  52W High : {w52_high:,.0f} VND  ({pct_from_high:.1f}% from current)",
            f"  52W Low  : {w52_low:,.0f} VND  ({pct_from_low:+.1f}% from current)",

            f"\n*Based on {n} trading days of data.*",
        ]

        text = "\n".join(lines)

    except Exception as e:
        text = f"Technical analysis failed for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


async def _fetch_broker_news(args: dict) -> list:
    ticker = args["ticker"].upper()
    limit = int(args.get("limit", 15))
    broker_pdf_url = args.get("broker_pdf_url", "").strip()

    sections: list = []

    # ── 1. ANALYST CONSENSUS (from overview) ──────────────────────────────
    try:
        ov_json = await _vnstock_subprocess("company_overview", {"ticker": ticker})
        ov_rows = json.loads(ov_json)
        ov = ov_rows[0] if ov_rows and isinstance(ov_rows, list) else {}
        prev_insight = ov.get("prev_insight") or {}
        def _ff(v, d=0.0):
            try: return float(v) if v is not None else d
            except: return d

        consensus = f"""## Analyst Consensus — {ticker}
| Field | Value |
|---|---|
| Rating | **{ov.get('rating', 'N/A')}** |
| Target Price | {_ff(ov.get('target_price')):,.0f} VND |
| Analyst | {ov.get('analyst') or prev_insight.get('analyst', 'N/A')} |
| Rating as of | {ov.get('rating_as_of') or prev_insight.get('ratingAsOf', 'N/A')} |
| Upside to Target | {_ff(ov.get('upside_to_target_percent')):.1%} |
| Projected TSR | {_ff(ov.get('projected_tsr_percentage')):.1%} |
| Dividend/Share (TSR) | {_ff(ov.get('dividend_per_share_tsr')):,.0f} VND |
"""
        sections.append(types.TextContent(type="text", text=consensus))
    except Exception as e:
        sections.append(types.TextContent(type="text", text=f"Analyst consensus unavailable: {e}"))

    # ── 2. CORPORATE EVENTS ───────────────────────────────────────────────
    try:
        import pandas as pd
        ev_json = await _vnstock_subprocess("company_events", {"ticker": ticker, "limit": 15})
        events = pd.DataFrame(json.loads(ev_json)) if ev_json.strip().startswith("[") else pd.DataFrame()
        if not events.empty:
            ev_lines = [f"## Corporate Events — {ticker} (recent 10)\n",
                        "| Date | Event | Detail | Value |",
                        "|---|---|---|---|"]
            for _, row in events.head(10).iterrows():
                date = str(row.get("display_date1", ""))[:10]
                name = str(row.get("event_name_en") or row.get("event_name_vi", ""))
                title = str(row.get("event_title_en") or row.get("event_title_vi", ""))[:80]
                val = row.get("value_per_share")
                val_str = f"{float(val):,.0f} VND" if val and str(val) != "nan" else ""
                ev_lines.append(f"| {date} | {name} | {title} | {val_str} |")
            sections.append(types.TextContent(type="text", text="\n".join(ev_lines)))
    except Exception as e:
        sections.append(types.TextContent(type="text", text=f"Events unavailable: {e}"))

    # ── 3. RECENT NEWS & DISCLOSURES ──────────────────────────────────────
    try:
        import pandas as pd
        news_json = await _vnstock_subprocess("company_news", {"ticker": ticker, "limit": limit})
        news = pd.DataFrame(json.loads(news_json)) if news_json.strip().startswith("[") else pd.DataFrame()
        if not news.empty:
            news_lines = [f"## Recent News & Disclosures — {ticker} (latest {limit})\n",
                          "| Date | Headline |",
                          "|---|---|"]
            for _, row in news.head(limit).iterrows():
                date = str(row.get("public_date", ""))[:10]
                title = str(row.get("news_title", "")).strip()
                link = str(row.get("news_source_link", "") or "")
                if link and link != "None":
                    title_md = f"[{title}]({link})"
                else:
                    title_md = title
                news_lines.append(f"| {date} | {title_md} |")
            sections.append(types.TextContent(type="text", text="\n".join(news_lines)))
    except Exception as e:
        sections.append(types.TextContent(type="text", text=f"News unavailable: {e}"))

    # ── 4. BROKER RESEARCH PDF (optional) ─────────────────────────────────
    if broker_pdf_url:
        sections.append(types.TextContent(
            type="text",
            text=f"## Broker Research Report\nLoading PDF: `{broker_pdf_url}`"
        ))
        pdf_result = await _load_financial_pdf({"source": broker_pdf_url, "max_pages": 15})
        sections.extend(pdf_result)
    else:
        sections.append(types.TextContent(
            type="text",
            text=(
                "## Broker Research Reports\n"
                "To include a specific broker report, call:\n"
                f'`fetch_broker_news(ticker="{ticker}", broker_pdf_url="<URL or /path/to/report.pdf>")`\n\n'
                "**Where to find public VN broker reports:**\n"
                "- **SSI Research:** ssi.com.vn → Research → Reports (login required for full PDF)\n"
                "- **TCBS:** tcbs.com.vn → Nghiên cứu (login required)\n"
                "- **Mirae Asset VN:** miraeasset.com.vn → Research\n"
                "- **VCBS:** vcbs.com.vn → Nghiên cứu & Phân tích\n"
                "- **VDSC (Rong Viet):** vdsc.com.vn → Research\n"
                "- **BSC:** bsc.com.vn → Nghiên cứu\n"
                "Once you have a PDF URL or downloaded file, pass it via `broker_pdf_url`."
            )
        ))

    return sections


def _latest_year_value(df, item_id: str) -> float | None:
    """Pull the most-recent year value for an item_id from a vnstock financial dataframe."""
    if df is None or df.empty or "item_id" not in df.columns:
        return None
    row = df[df["item_id"] == item_id]
    if row.empty:
        return None
    year_cols = [c for c in df.columns if str(c).isdigit() or isinstance(c, int)]
    if not year_cols:
        return None
    try:
        return float(row.iloc[0][year_cols[0]])
    except (TypeError, ValueError):
        return None


async def _fetch_metrics_for_ticker(ticker: str, period: str) -> dict:
    """Return a flat dict of key metrics for one ticker. All vnstock calls run via subprocess."""
    import pandas as pd
    from datetime import date

    result: dict = {"ticker": ticker}

    ov_json, hist_json, inc_json, bal_json, cf_json = await asyncio.gather(
        _vnstock_subprocess("company_overview",   {"ticker": ticker}),
        _vnstock_subprocess("quote_history",      {"ticker": ticker, "start": "2026-01-01", "end": date.today().isoformat()}),
        _vnstock_subprocess("income_statement",   {"ticker": ticker, "period": period}),
        _vnstock_subprocess("balance_sheet",      {"ticker": ticker, "period": period}),
        _vnstock_subprocess("cash_flow",          {"ticker": ticker, "period": period}),
    )

    def _f(v, default: float = 0.0) -> float:
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    # Overview block
    try:
        ov_rows = json.loads(ov_json)
        if ov_rows and isinstance(ov_rows, list):
            ov = ov_rows[0]
            result.update({
                "name":          str(ov.get("organ_short_name") or ov.get("organ_name", ticker)),
                "sector":        str(ov.get("sector", "N/A")),
                "market_cap_b":  _f(ov.get("market_cap")) / 1e9,
                "current_price": _f(ov.get("current_price")),
                "target_price":  _f(ov.get("target_price")),
                "rating":        str(ov.get("rating") or "N/A"),
                "52w_high":      _f(ov.get("highest_price1_year")),
                "52w_low":       _f(ov.get("lowest_price1_year")),
                "foreign_pct":   _f(ov.get("foreigner_percentage")) * 100,
            })
        elif isinstance(ov_rows, dict) and "error" in ov_rows:
            result["overview_error"] = ov_rows["error"][:200]
    except (json.JSONDecodeError, IndexError) as e:
        result["overview_error"] = str(e)

    # Latest price block
    try:
        hist_rows = json.loads(hist_json)
        if hist_rows and isinstance(hist_rows, list):
            result["latest_price"] = _f(hist_rows[-1].get("close")) * 1000
        elif "latest_price" not in result:
            result["latest_price"] = result.get("current_price", 0)
    except json.JSONDecodeError:
        result["latest_price"] = result.get("current_price", 0)

    # Financial statements block
    try:
        inc = pd.DataFrame(json.loads(inc_json)) if inc_json.strip().startswith("[") else pd.DataFrame()
        bal = pd.DataFrame(json.loads(bal_json)) if bal_json.strip().startswith("[") else pd.DataFrame()
        cf  = pd.DataFrame(json.loads(cf_json))  if cf_json.strip().startswith("[")  else pd.DataFrame()

        if inc.empty and bal.empty and cf.empty:
            result["finance_error"] = "All financial statements empty"
            return result

        net_sales         = _latest_year_value(inc, "net_sales")
        gross_profit      = _latest_year_value(inc, "gross_profit")
        op_profit         = _latest_year_value(inc, "operating_profit_loss")
        net_profit        = _latest_year_value(inc, "net_profit_loss_after_tax")
        net_profit_parent = _latest_year_value(inc, "attributable_to_parent_company")
        eps               = _latest_year_value(inc, "eps_basic_vnd")

        total_assets   = _latest_year_value(bal, "total_assets")
        current_assets = _latest_year_value(bal, "current_assets")
        current_liab   = _latest_year_value(bal, "current_liabilities")
        st_borrow      = _latest_year_value(bal, "short_term_borrowings")
        lt_borrow      = _latest_year_value(bal, "long_term_borrowings")
        equity         = _latest_year_value(bal, "owners_equity")
        cash           = _latest_year_value(bal, "cash_and_cash_equivalents")

        op_cf = _latest_year_value(cf, "net_cash_inflows_outflows_from_op") or _latest_year_value(cf, "net_cash_from_operating_activities")
        capex = _latest_year_value(cf, "purchases_of_fixed_assets_and_other")
        dep   = _latest_year_value(cf, "depreciation_and_amortization")

        price  = result.get("latest_price") or result.get("current_price", 0)
        mktcap = result.get("market_cap_b", 0) * 1e9

        gross_margin  = gross_profit / net_sales         if net_sales      else None
        op_margin     = op_profit / net_sales            if net_sales      else None
        net_margin    = net_profit_parent / net_sales    if net_sales      else None
        roe           = net_profit_parent / equity       if equity         else None
        roa           = net_profit / total_assets        if total_assets   else None
        current_ratio = current_assets / current_liab    if current_liab   else None
        de_ratio      = ((st_borrow or 0) + (lt_borrow or 0)) / equity if equity else None
        pe            = price / eps                      if eps and eps > 0 else None
        pb            = mktcap / equity                  if equity         else None
        ev            = mktcap + (st_borrow or 0) + (lt_borrow or 0) - (cash or 0)
        ebitda        = (op_profit + dep) if op_profit and dep else op_profit
        ev_ebitda     = ev / ebitda                      if ebitda and ebitda > 0 else None
        peg           = (pe / (roe * 100))               if pe and roe and roe > 0 else None
        fcf           = (op_cf + capex)                  if op_cf and capex else None

        net_debt         = (st_borrow or 0) + (lt_borrow or 0) - (cash or 0)
        invested_capital = (equity or 0) + net_debt
        nopat            = (op_profit or 0) * 0.80
        roic             = nopat / invested_capital      if invested_capital and invested_capital > 0 else None

        result.update({
            "net_sales_b":      net_sales / 1e9         if net_sales else None,
            "gross_margin_pct": gross_margin * 100      if gross_margin else None,
            "op_margin_pct":    op_margin * 100         if op_margin else None,
            "net_margin_pct":   net_margin * 100        if net_margin else None,
            "roe_pct":          roe * 100               if roe else None,
            "roa_pct":          roa * 100               if roa else None,
            "current_ratio":    current_ratio,
            "de_ratio":         de_ratio,
            "pe":               pe,
            "pb":               pb,
            "ev_ebitda":        ev_ebitda,
            "peg":              peg,
            "eps":              eps,
            "equity_b":         equity / 1e9            if equity else None,
            "fcf_b":            fcf / 1e9               if fcf else None,
            "roic_pct":         roic * 100              if roic else None,
        })
    except Exception as e:
        result["finance_error"] = str(e)

    return result


def _fmt(val, fmt=".1f", suffix="", na="N/A"):
    if val is None or (isinstance(val, float) and (val != val)):
        return na
    try:
        return f"{val:{fmt}}{suffix}"
    except Exception:
        return na


async def _compare_stocks(args: dict) -> list[types.TextContent]:
    tickers = [t.upper() for t in args["tickers"]]
    period = args.get("period", "year")

    metrics_list = await asyncio.gather(*[_fetch_metrics_for_ticker(t, period) for t in tickers])

    rows_def = [
        ("Company",         "name",             "s",    ""),
        ("Sector",          "sector",            "s",    ""),
        ("Price (VND)",     "latest_price",      ",.0f", ""),
        ("Market Cap (B)",  "market_cap_b",      ",.0f", "B"),
        ("52W High",        "52w_high",          ",.0f", ""),
        ("52W Low",         "52w_low",           ",.0f", ""),
        ("Target Price",    "target_price",      ",.0f", ""),
        ("Rating",          "rating",            "s",    ""),
        ("--- VALUATION",   None,                None,   None),
        ("P/E",             "pe",                ".1f",  "x"),
        ("P/B",             "pb",                ".1f",  "x"),
        ("EV/EBITDA",       "ev_ebitda",         ".1f",  "x"),
        ("PEG Ratio",       "peg",               ".2f",  ""),
        ("--- PROFITABILITY",None,               None,   None),
        ("Revenue (B VND)", "net_sales_b",       ",.1f", "B"),
        ("Gross Margin",    "gross_margin_pct",  ".1f",  "%"),
        ("Oper. Margin",    "op_margin_pct",     ".1f",  "%"),
        ("Net Margin",      "net_margin_pct",    ".1f",  "%"),
        ("ROE",             "roe_pct",           ".1f",  "%"),
        ("ROA",             "roa_pct",           ".1f",  "%"),
        ("ROIC",            "roic_pct",          ".1f",  "%"),
        ("FCF (B VND)",     "fcf_b",             ",.1f", "B"),
        ("--- HEALTH",      None,                None,   None),
        ("Current Ratio",   "current_ratio",     ".2f",  "x"),
        ("Debt/Equity",     "de_ratio",          ".2f",  "x"),
        ("Foreign Own %",   "foreign_pct",       ".1f",  "%"),
    ]

    col_w = 22
    ticker_w = max(10, *[len(t) for t in tickers])
    header = f"{'Metric':<{col_w}}" + "".join(f"{t:>{ticker_w}}" for t in tickers)
    divider = "-" * (col_w + ticker_w * len(tickers))

    lines = [f"Peer Comparison ({period}) — figures in billions VND", divider, header, divider]
    for label, key, fmt, suffix in rows_def:
        if key is None:
            lines.append(f"\n{label}")
            continue
        row = f"{label:<{col_w}}"
        for m in metrics_list:
            val = m.get(key)
            if fmt == "s":
                cell = str(val or "N/A")[:ticker_w]
            else:
                cell = _fmt(val, fmt, suffix)
            row += f"{cell:>{ticker_w}}"
        lines.append(row)

    lines.append(divider)
    lines.append("\nErrors (if any):")
    for m in metrics_list:
        for k in ("overview_error", "finance_error"):
            if k in m:
                lines.append(f"  {m['ticker']} {k}: {m[k]}")

    return [types.TextContent(type="text", text="\n".join(lines))]


_VCB_FX_URL  = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx?b=10"
_BTMC_URL    = "http://api.btmc.vn/api/BTMCAPI/getpricebtmc?key=3kd8ub1llcg9t45hnoh8hmn7t5kc2v"

_KEY_CURRENCIES = ["USD", "EUR", "JPY", "CNY", "GBP", "AUD", "SGD", "KRW", "THB", "HKD"]

# BTMC commodity names to display labels
_BTMC_PRODUCTS = {
    "VÀNG MIẾNG SJC":                  "Vàng SJC (miếng)",
    "VÀNG MIẾNG VRTL":                 "Vàng Rồng Thăng Long",
    "TRANG SỨC VÀNG RỒNG THĂNG LONG 999.9": "Vàng trang sức BTMC 999.9",
    "NHẪN TRÒN TRƠN":                  "Nhẫn tròn trơn",
    "VÀNG NGUYÊN LIỆU":                "Vàng nguyên liệu (thị trường)",
    "BẠC MIẾNG PHÚ QUÝ Ag 999 1 LƯỢNG": "Bạc Phú Quý (1 lượng)",
}


async def _get_macro_data(_args: dict) -> list[types.TextContent]:
    import xml.etree.ElementTree as ET

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as c:
            resp = await c.get(_VCB_FX_URL, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        return [types.TextContent(type="text", text=f"Failed to fetch exchange rates: {e}")]

    dt = root.findtext("DateTime", "N/A")
    rates = {}
    for node in root.findall("Exrate"):
        code = node.get("CurrencyCode", "").strip()
        rates[code] = {
            "name": node.get("CurrencyName", "").strip().title(),
            "buy":  node.get("Buy", "-").replace(",", ""),
            "transfer": node.get("Transfer", "-").replace(",", ""),
            "sell": node.get("Sell", "-").replace(",", ""),
        }

    lines = [
        f"## Tỷ Giá Ngoại Tệ — Vietcombank",
        f"*Cập nhật: {dt}*\n",
        "| Currency | Name | Buy | Transfer | Sell |",
        "|---|---|---:|---:|---:|",
    ]
    for code in _KEY_CURRENCIES:
        if code in rates:
            r = rates[code]
            def _fmt(v):
                try: return f"{float(v):,.2f}"
                except: return v
            lines.append(f"| **{code}** | {r['name']} | {_fmt(r['buy'])} | {_fmt(r['transfer'])} | {_fmt(r['sell'])} |")

    lines += [
        "",
        "**Đơn vị:** VND / 1 đơn vị ngoại tệ (trừ JPY, KRW = VND / 100 đơn vị)",
        "*Nguồn: Vietcombank — chỉ mang tính tham khảo*",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_commodity_prices(_args: dict) -> list[types.TextContent]:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as c:
            resp = await c.get(_BTMC_URL, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        items = resp.json()["DataList"]["Data"]
    except Exception as e:
        return [types.TextContent(type="text", text=f"Failed to fetch commodity prices: {e}")]

    # Keep only the first (most recent) occurrence of each product keyword
    seen: dict[str, dict] = {}
    for item in items:
        row = item["@row"]
        name = item.get(f"@n_{row}", "").strip()
        date = item.get(f"@d_{row}", "")
        buy  = item.get(f"@pb_{row}", "")
        sell = item.get(f"@ps_{row}", "")

        # Match against known products (partial name match)
        for key, label in _BTMC_PRODUCTS.items():
            if key in name.upper() or name.upper() in key:
                if label not in seen:
                    seen[label] = {"buy": buy, "sell": sell, "date": date, "name": name}
                break

    if not seen:
        return [types.TextContent(type="text", text="No commodity data returned from BTMC.")]

    # Use latest date from first item
    sample_date = next(iter(seen.values()))["date"]

    lines = [
        "## Giá Hàng Hóa — BTMC",
        f"*Cập nhật: {sample_date}*  |  Đơn vị: VND / lượng (37.5g)\n",
        "| Loại | Mua vào | Bán ra |",
        "|---|---:|---:|",
    ]

    def _fmt(v: str) -> str:
        try: return f"{int(v):,}"
        except: return v or "—"

    for label, data in seen.items():
        lines.append(f"| {label} | {_fmt(data['buy'])} | {_fmt(data['sell'])} |")

    lines += [
        "",
        "*Nguồn: BTMC (Bảo Tín Minh Châu)*",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


_RSS_FEEDS = [
    ("CafeF - Thị trường CK", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("CafeF - Doanh nghiệp",  "https://cafef.vn/doanh-nghiep.rss"),
    ("CafeF - Tài chính NH",  "https://cafef.vn/tai-chinh-ngan-hang.rss"),
    ("CafeF - Đầu tư",        "https://cafef.vn/dau-tu.rss"),
    ("VietStock",             "https://vietstock.vn/830/chung-khoan.rss"),
    ("Tin Nhanh CK",          "https://tinnhanhchungkhoan.vn/rss/"),
    ("VnExpress Business",    "https://e.vnexpress.net/rss/business.rss"),
    ("Vietnam Inv. Review",   "https://vir.com.vn/rss_feed/"),
]

# High-signal economic & investment feeds — used by get_economy_news
_ECONOMY_FEEDS = [
    ("VnEconomy - Chứng khoán",   "https://vneconomy.vn/chung-khoan.rss"),
    ("VnEconomy - Tài chính",     "https://vneconomy.vn/tai-chinh.rss"),
    ("VnEconomy - Tiêu điểm",     "https://vneconomy.vn/tieu-diem.rss"),
    ("VnEconomy - Thị trường",    "https://vneconomy.vn/thi-truong.rss"),
    ("Báo Đầu tư - Tài chính CK", "https://baodautu.vn/dau-tu-tai-chinh.rss"),
    ("Báo Đầu tư - Điểm nổi bật", "https://baodautu.vn/diem-tin-noi-bat.rss"),
    ("Báo Đầu tư - Kinh doanh",   "https://baodautu.vn/kinh-doanh.rss"),
    ("Báo Đầu tư - Ngân hàng",    "https://baodautu.vn/ngan-hang--bao-hiem.rss"),
    ("CafeF - Thị trường CK",     "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("Tin Nhanh CK",               "https://tinnhanhchungkhoan.vn/rss/"),
    ("VnExpress Business",         "https://e.vnexpress.net/rss/business.rss"),
    ("Vietnam Inv. Review",        "https://vir.com.vn/rss_feed/"),
]

_RSS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# Common ticker → Vietnamese company name keywords for broader matching
_TICKER_ALIASES: dict[str, list[str]] = {
    "VCB": ["Vietcombank"],
    "BID": ["BIDV"],
    "CTG": ["VietinBank"],
    "TCB": ["Techcombank"],
    "MBB": ["MB Bank", "Quân đội"],
    "VPB": ["VPBank"],
    "ACB": ["Á Châu"],
    "VIC": ["Vingroup"],
    "VHM": ["Vinhomes"],
    "VNM": ["Vinamilk"],
    "SAB": ["Sabeco"],
    "MSN": ["Masan"],
    "HPG": ["Hòa Phát"],
    "MWG": ["Thế Giới Di Động"],
    "FRT": ["FPT Retail"],
    "PNJ": ["Phú Nhuận"],
    "VJC": ["VietJet"],
    "HVN": ["Vietnam Airlines"],
    "GVR": ["Cao su Việt Nam"],
    "GAS": ["PetroVietnam Gas"],
    "PLX": ["Petrolimex"],
}


async def _fetch_one_rss(client: httpx.AsyncClient, source: str, url: str, ticker: str, keywords: list[str]) -> list[dict]:
    import xml.etree.ElementTree as ET
    import re

    try:
        resp = await client.get(url, timeout=10, headers=_RSS_HEADERS)
        resp.raise_for_status()
        # Strip non-XML control characters before parsing (some feeds have encoding issues)
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', resp.text).encode("utf-8", errors="replace")
        root = ET.fromstring(raw)
    except Exception:
        return []

    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link_el = item.find("link")
        link = (link_el.text or "").strip() if link_el is not None else ""
        if not link:
            link = item.findtext("guid", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        description = re.sub(r"<[^>]+>", " ", item.findtext("description", ""))

        haystack = f"{title} {description}".upper()
        if any(kw.upper() in haystack for kw in keywords):
            items.append({
                "source": source,
                "title": title,
                "link": link,
                "date": pub_date[:22] if pub_date else "",
            })
    return items


async def _get_market_news(args: dict) -> list[types.TextContent]:
    ticker = args["ticker"].upper()
    limit = int(args.get("limit", 20))

    keywords = [ticker] + _TICKER_ALIASES.get(ticker, [])

    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *[_fetch_one_rss(client, name, url, ticker, keywords) for name, url in _RSS_FEEDS],
            return_exceptions=True,
        )

    seen, articles = set(), []
    for batch in results:
        if isinstance(batch, list):
            for item in batch:
                key = item["title"].lower()
                if key not in seen:
                    seen.add(key)
                    articles.append(item)

    articles = articles[:limit]

    if not articles:
        return [types.TextContent(
            type="text",
            text=(
                f"## Market News — {ticker}\n\n"
                f"No articles mentioning **{ticker}** found across {len(_RSS_FEEDS)} RSS feeds.\n\n"
                "Possible reasons: ticker not in recent headlines, site blocked, or RSS temporarily unavailable.\n"
                "Try `fetch_broker_news` for vnstock-sourced disclosures instead."
            ),
        )]

    lines = [
        f"## Market News — {ticker}  ({len(articles)} articles from {len(_RSS_FEEDS)} sources)\n",
        "| Source | Date | Headline |",
        "|---|---|---|",
    ]
    for a in articles:
        title_md = f"[{a['title']}]({a['link']})" if a["link"] else a["title"]
        lines.append(f"| {a['source']} | {a['date']} | {title_md} |")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_analysis_prompt(args: dict) -> list[types.TextContent]:
    ticker = args["ticker"].upper()
    mode = args.get("mode", "full")
    pdf_path = args.get("pdf_path", "")

    data_steps = ""
    if mode in ("full", "quick"):
        data_steps = f"""
STEP 1 — GATHER DATA
Call these tools in parallel:
  • get_stock_overview(ticker="{ticker}")
  • get_financial_data(ticker="{ticker}", period="year")
  • get_financial_data(ticker="{ticker}", period="quarter")
  • fetch_broker_news(ticker="{ticker}")
  • get_technical_analysis(ticker="{ticker}")
"""
    if mode in ("full", "pdf") and pdf_path:
        data_steps += f"""
STEP 2 — LOAD PDF REPORT
  • load_financial_pdf(source="{pdf_path}", max_pages=20)
  Read every page carefully. Extract all tables with exact figures.
"""
    elif mode == "pdf" and not pdf_path:
        data_steps += "\nSTEP 2 — Ask the user to provide the PDF path or URL.\n"

    peers_hint = f'  compare_stocks(tickers=["{ticker}", "<peer1>", "<peer2>"])  ← suggest 2–3 sector peers'

    prompt = f"""You are a buy-side portfolio manager and CFA charterholder specialising in Vietnamese equities.
Produce an institutional-grade equity research note. Be direct, data-driven, and opinionated.

{data_steps}
  Also call: {peers_hint}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EQUITY RESEARCH NOTE: {ticker}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

### EXECUTIVE SUMMARY  (write this last, 5 lines max)
Verdict | Target Price | Upside | Key thesis in one sentence.

---

### 1. BUSINESS & COMPETITIVE POSITION
- Revenue breakdown by segment (% of total). Which segment drives margin?
- Moat assessment: pricing power, switching costs, network effects, scale advantages.
- Addressable market size and penetration in Vietnam.

---

### 2. EARNINGS QUALITY & GROWTH
Compute and present in a table:
| Metric | 2022 | 2023 | 2024 | 2025 | CAGR 3Y |
Revenue, Gross Profit, EBIT, Net Profit (parent), EPS

- YoY growth rates for each year. Is growth re-accelerating or decelerating?
- Derive Q1 2025 = (Full Year 2025) − Q2 − Q3 − Q4, then compare Q1 2026 vs Q1 2025.
- EPS quality check: does EPS CAGR match profit CAGR? If not, explain dilution or buybacks.

---

### 3. PROFITABILITY & DUPONT DECOMPOSITION
Compute margins table (Gross / EBIT / Net) for all years.

DuPont ROE breakdown (most recent year):
  ROE = Net Margin × Asset Turnover × Financial Leverage
  = (Net Profit/Sales) × (Sales/Assets) × (Assets/Equity)

- Is ROE driven by genuine profitability or leverage? Compare to 3-year trend.
- ROIC = EBIT(1−tax) / (Equity + Net Debt) — capital efficiency beyond leverage.

---

### 4. BALANCE SHEET & WORKING CAPITAL EFFICIENCY
Standard ratios: Current Ratio, Quick Ratio, D/E, Net Debt/EBITDA.

Working capital cycle:
- DSO (Days Sales Outstanding) = Receivables / (Revenue/365)
- DIO (Days Inventory Outstanding) = Inventory / (COGS/365)
- DPO (Days Payable Outstanding) = Payables / (COGS/365)
- Cash Conversion Cycle = DSO + DIO − DPO

Trend: is the company collecting cash faster or slower? Flag any deterioration.

---

### 5. CASH FLOW & CAPITAL ALLOCATION
| Metric | 2022 | 2023 | 2024 | 2025 |
OCF, Capex, FCF, Dividends Paid, OCF/Net Profit %

- FCF yield = FCF / Market Cap
- Capex intensity = Capex / Revenue — is it rising (investment phase) or falling (harvest)?
- Capital allocation scorecard: M&A track record, dividend policy, share issuance/buybacks.
- Altman Z-score proxy for financial distress risk (if manufacturing/industrial company).

---

### 6. VALUATION — ABSOLUTE & RELATIVE
Absolute:
| Metric | Current | 1Y Avg | 3Y Avg | Sector Avg |
P/E, P/B, EV/EBITDA, PEG (P/E ÷ EPS growth %), FCF Yield

- Implied fair value: if the stock re-rated to its 3-year average P/E, what would the price be?
- DCF sanity check: at current price, what perpetual growth rate is implied?
  (Simplified: P = EPS × (1+g) / (r−g), solve for g using r=12% cost of equity)

Relative (from compare_stocks output):
- Rank {ticker} vs peers on: P/E, EV/EBITDA, ROE, Net Margin, Revenue Growth.
- Is the premium/discount justified? Explain in 2 sentences.

---

### 7. TECHNICAL ANALYSIS  (from get_technical_analysis output)
Present the computed signal cleanly:
- **Trend:** Is price above/below MA20, MA50, MA200? Is it in a downtrend, base, or uptrend?
- **Momentum:** RSI reading + interpretation. MACD crossover status.
- **Bollinger Bands:** Where is price relative to bands? Squeeze or expansion?
- **Key levels:** Resistance, support, 52W high/low. How far is price from each?
- **Volume:** Is the recent move confirmed by volume?
- **Entry/exit guidance:** Based on technicals alone, is this a good entry point, or should one wait?
  (e.g. "RSI 28 + price at 52W low support = high-conviction entry zone")

---

### 9. RISK MATRIX
| Risk | Probability | Impact | Mitigant |
Present 4–6 specific, non-generic risks with probability (H/M/L) and impact (H/M/L).
Include at least one macro risk (VN interest rates, FX, regulatory) and one company-specific risk.

---

### 10. INVESTMENT RECOMMENDATION
**Verdict: STRONG BUY / BUY / HOLD / SELL / STRONG SELL**
**12-month Target Price: ___ VND**  (state methodology: P/E-based, DCF, or blended)
**Expected Total Return: ___% (price appreciation + dividend yield)**

| Scenario | Probability | Price Target | Rationale |
| Bull | 30% | | |
| Base | 50% | | |
| Bear | 20% | | |

Probability-weighted target = (bull × 0.3) + (base × 0.5) + (bear × 0.2)

**Catalysts:** List 2–3 specific events with approximate timing.
**Key risk to the thesis:** One sentence.

---

FINAL STEP — SAVE
After completing all sections, call:
  save_analysis(ticker="{ticker}", content="<full markdown>", period="<e.g. Q1-2026>")
"""
    return [types.TextContent(type="text", text=prompt)]


_MARKET_WATCH = [
    "VCB", "BID", "TCB", "MBB", "VPB",   # Banking
    "VIC", "VHM",                           # Real Estate
    "FPT", "HPG", "VNM",                   # Tech / Steel / Consumer staples
    "MWG", "GAS", "PLX", "MSN", "SAB",    # Retail / Energy / Consumer
]


async def _fetch_headlines_from_rss(
    client: httpx.AsyncClient, source: str, feed_url: str, limit: int = 5
) -> list[dict]:
    import xml.etree.ElementTree as ET
    import re
    from urllib.parse import urlparse

    parsed = urlparse(feed_url)
    source_url = f"{parsed.scheme}://{parsed.netloc}"

    try:
        resp = await client.get(feed_url, timeout=10, headers=_RSS_HEADERS)
        resp.raise_for_status()
        raw = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', resp.text).encode("utf-8", errors="replace")
        root = ET.fromstring(raw)
    except Exception:
        return []
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link_el = item.find("link")
        link = (link_el.text or "").strip() if link_el is not None else ""
        if not link:
            link = item.findtext("guid", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        if title:
            items.append({
                "source": source,
                "source_url": source_url,
                "title": title,
                "link": link,
                "date": pub_date[:22] if pub_date else "",
            })
        if len(items) >= limit:
            break
    return items


async def _get_market_overview(_args: dict) -> list[types.TextContent]:
    from datetime import date, datetime, timezone, timedelta

    start = (date.today() - timedelta(days=7)).isoformat()
    end = date.today().isoformat()
    kw = {"start": start, "end": end}

    index_symbols = ["VNINDEX", "HNXINDEX", "UPCOMINDEX"]
    all_symbols = index_symbols + _MARKET_WATCH

    async with httpx.AsyncClient(follow_redirects=True) as client:
        all_results = await asyncio.gather(
            *[_vnstock_subprocess("quote_history", {"ticker": sym, **kw}) for sym in all_symbols],
            *[_fetch_headlines_from_rss(client, name, url) for name, url in _RSS_FEEDS],
            return_exceptions=True,
        )

    n_prices = len(all_symbols)
    raw_results    = all_results[:n_prices]
    raw_headlines  = all_results[n_prices:]

    def _parse_rows(raw) -> list:
        if isinstance(raw, Exception):
            return []
        try:
            rows = json.loads(raw)
            return rows if isinstance(rows, list) else []
        except Exception:
            return []

    data = {sym: _parse_rows(raw_results[i]) for i, sym in enumerate(all_symbols)}

    def _last_change(rows, scale=1000):
        if len(rows) < 2:
            return None, None, None
        price = float(rows[-1].get("close", 0)) * scale
        prev  = float(rows[-2].get("close", 0)) * scale
        vol   = float(rows[-1].get("volume", 0))
        chg   = (price - prev) / prev * 100 if prev else 0
        return price, chg, vol

    vn_tz  = timezone(timedelta(hours=7))
    now_str = datetime.now(vn_tz).strftime("%Y-%m-%d %H:%M VNT")

    lines = [f"## VN Stock Market Overview\n*{now_str}*\n"]

    # ── Indices ───────────────────────────────────────────────────────────────
    # Indices are stored in natural units (points), not ÷1000 like stock prices
    lines += [
        "### Market Indices",
        "| Index | Points | Change | Change % | Volume |",
        "|---|---:|---:|---:|---:|",
    ]
    idx_labels = {"VNINDEX": "VN-Index", "HNXINDEX": "HNX-Index", "UPCOMINDEX": "UPCOM"}
    for sym in index_symbols:
        rows = data[sym]
        price, chg, vol = _last_change(rows, scale=1)
        if price is None:
            lines.append(f"| **{idx_labels[sym]}** | N/A | — | — | — |")
            continue
        chg_pts = price - float(rows[-2].get("close", 0))
        arrow = "▲" if chg_pts >= 0 else "▼"
        sign  = "+" if chg >= 0 else ""
        lines.append(
            f"| **{idx_labels[sym]}** | {price:,.2f} | {arrow} {abs(chg_pts):,.2f} | {sign}{chg:.2f}% | {vol:,.0f} |"
        )

    # ── Top movers ────────────────────────────────────────────────────────────
    movers = []
    for sym in _MARKET_WATCH:
        price, chg, vol = _last_change(data[sym], scale=1000)
        if price is not None:
            movers.append({"ticker": sym, "price": price, "chg": chg, "vol": vol})

    if movers:
        gainers = [m for m in sorted(movers, key=lambda x: x["chg"], reverse=True) if m["chg"] > 0][:5]
        losers  = [m for m in sorted(movers, key=lambda x: x["chg"]) if m["chg"] < 0][:5]

        if gainers:
            lines += [
                "\n### Top Gainers",
                "| Ticker | Price (VND) | Change % | Volume |",
                "|---|---:|---:|---:|",
            ]
            for m in gainers:
                lines.append(f"| **{m['ticker']}** | {m['price']:,.0f} | +{m['chg']:.2f}% | {m['vol']:,.0f} |")

        if losers:
            lines += [
                "\n### Top Losers",
                "| Ticker | Price (VND) | Change % | Volume |",
                "|---|---:|---:|---:|",
            ]
            for m in losers:
                lines.append(f"| **{m['ticker']}** | {m['price']:,.0f} | {m['chg']:.2f}% | {m['vol']:,.0f} |")

    lines.append("\n*Live prices available 09:00–15:00 VNT. After hours reflects last close.*")

    # ── Market Headlines ──────────────────────────────────────────────────────
    seen_titles: set = set()
    per_source: dict = {}
    articles: list = []
    for batch in raw_headlines:
        if isinstance(batch, list):
            for item in batch:
                key = item["title"].lower()
                src = item["source"]
                if key not in seen_titles and per_source.get(src, 0) < 2:
                    seen_titles.add(key)
                    per_source[src] = per_source.get(src, 0) + 1
                    articles.append(item)

    if articles:
        lines += [
            "\n---\n### Market Headlines Today",
            "| Source | Date | Headline |",
            "|---|---|---|",
        ]
        for a in articles:
            source_md = f"[{a['source']}]({a['source_url']})"
            title_md  = f"[{a['title']}]({a['link']})" if a["link"] else a["title"]
            lines.append(f"| {source_md} | {a['date']} | {title_md} |")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_economy_news(args: dict) -> list[types.TextContent]:
    from datetime import datetime, timezone, timedelta

    limit = int(args.get("limit", 20))
    per_source_cap = max(2, limit // len(_ECONOMY_FEEDS) + 1)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        batches = await asyncio.gather(
            *[_fetch_headlines_from_rss(client, name, url, limit=per_source_cap)
              for name, url in _ECONOMY_FEEDS],
            return_exceptions=True,
        )

    seen_titles: set = set()
    per_source: dict = {}
    articles: list = []
    for batch in batches:
        if not isinstance(batch, list):
            continue
        for item in batch:
            key = item["title"].lower()
            src = item["source"]
            if key not in seen_titles and per_source.get(src, 0) < per_source_cap:
                seen_titles.add(key)
                per_source[src] = per_source.get(src, 0) + 1
                articles.append(item)

    articles = articles[:limit]

    vn_tz   = timezone(timedelta(hours=7))
    now_str = datetime.now(vn_tz).strftime("%Y-%m-%d %H:%M VNT")

    if not articles:
        return [types.TextContent(type="text", text="No headlines fetched — RSS feeds may be temporarily unavailable.")]

    lines = [
        f"## Vietnam Economy & Market News",
        f"*{now_str} — {len(articles)} headlines from {len(_ECONOMY_FEEDS)} sources*\n",
        "| Source | Date | Headline |",
        "|---|---|---|",
    ]
    for a in articles:
        source_md = f"[{a['source']}]({a['source_url']})"
        title_md  = f"[{a['title']}]({a['link']})" if a["link"] else a["title"]
        lines.append(f"| {source_md} | {a['date']} | {title_md} |")

    active_sources = sorted(per_source.keys())
    lines.append(f"\n*Sources: {', '.join(active_sources)}*")

    return [types.TextContent(type="text", text="\n".join(lines))]


# Sector lookup uses lowercase substring match against vnstock's sector strings
# (which vary: "Banks" vs "Banking", "Real Estate" vs "Real Estate Development", etc.)
# Key = lowercase substring to look for in vnstock sector string.

_SECTOR_PEER_SET: list[tuple[str, list[str]]] = [
    ("technolog",          ["FPT", "CMG", "VGI", "ITD", "ELC"]),
    ("bank",               ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "STB", "VPB", "HDB"]),
    ("real estate",        ["VIC", "VHM", "NLG", "KDH", "DXG", "DIG"]),
    ("steel",              ["HPG", "HSG", "NKG"]),
    ("material",           ["HPG", "HSG", "DGC"]),
    ("staple",             ["VNM", "SAB", "MSN"]),
    ("discretionary",      ["MWG", "FRT", "PNJ"]),
    ("retail",             ["MWG", "FRT", "PNJ"]),
    ("aviation",           ["VJC", "HVN"]),
    ("airline",            ["VJC", "HVN"]),
    ("industrial",         ["GVR", "PHR"]),
    ("energy",             ["GAS", "PLX", "BSR"]),
    ("oil",                ["GAS", "PLX", "BSR", "PVD", "PVS"]),
    ("telecom",            ["VGI", "CTR"]),
]

_VALUATION_WEIGHTS: list[tuple[str, dict[str, float]]] = [
    ("bank",               {"dcf": 0.1, "relative": 0.9}),  # DCF nonsensical for banks (deposit flows in OCF)
    ("insurance",          {"dcf": 0.1, "relative": 0.9}),
    ("real estate",        {"dcf": 0.2, "relative": 0.8}),  # NAV-based ideally
    ("aviation",           {"dcf": 0.2, "relative": 0.8}),
    ("airline",            {"dcf": 0.2, "relative": 0.8}),
    ("steel",              {"dcf": 0.3, "relative": 0.7}),  # cyclical
    ("material",           {"dcf": 0.3, "relative": 0.7}),
    ("energy",             {"dcf": 0.3, "relative": 0.7}),
    ("oil",                {"dcf": 0.3, "relative": 0.7}),
    ("discretionary",      {"dcf": 0.4, "relative": 0.6}),
    ("retail",             {"dcf": 0.4, "relative": 0.6}),
    ("staple",             {"dcf": 0.6, "relative": 0.4}),  # durable franchise → DCF more reliable
    ("technolog",          {"dcf": 0.5, "relative": 0.5}),
    ("telecom",            {"dcf": 0.5, "relative": 0.5}),
    ("industrial",         {"dcf": 0.5, "relative": 0.5}),
]
_DEFAULT_WEIGHTS = {"dcf": 0.5, "relative": 0.5}

# Sectors where DCF is structurally unreliable — surface a strong warning
_DCF_UNRELIABLE_KEYS = ["bank", "insurance", "real estate"]


def _lookup_sector_peers(sector: str) -> list[str]:
    s = (sector or "").lower()
    for key, peers in _SECTOR_PEER_SET:
        if key in s:
            return list(peers)
    return []


def _lookup_sector_weights(sector: str) -> dict[str, float]:
    s = (sector or "").lower()
    for key, weights in _VALUATION_WEIGHTS:
        if key in s:
            return dict(weights)
    return dict(_DEFAULT_WEIGHTS)


def _is_dcf_unreliable(sector: str) -> bool:
    s = (sector or "").lower()
    return any(k in s for k in _DCF_UNRELIABLE_KEYS)


async def _fetch_peer_multiples(target: str, peers: list[str]) -> dict:
    """Fetch P/E, P/B, EV/EBITDA for each peer in parallel. Return medians."""
    other_peers = [p for p in peers if p.upper() != target.upper()]
    if not other_peers:
        return {"peers_used": [], "pe": None, "pb": None, "ev_ebitda": None}

    metrics_list = await asyncio.gather(*[
        _fetch_metrics_for_ticker(t, "year") for t in other_peers
    ])

    pe_values:        list[float] = []
    pb_values:        list[float] = []
    ev_ebitda_values: list[float] = []
    used:             list[str]   = []

    for m in metrics_list:
        has_any = False
        if m.get("pe") and 0 < m["pe"] < 200:  # filter outliers
            pe_values.append(m["pe"])
            has_any = True
        if m.get("pb") and 0 < m["pb"] < 20:
            pb_values.append(m["pb"])
            has_any = True
        if m.get("ev_ebitda") and 0 < m["ev_ebitda"] < 100:
            ev_ebitda_values.append(m["ev_ebitda"])
            has_any = True
        if has_any:
            used.append(m["ticker"])

    def _median(xs: list[float]) -> float | None:
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    return {
        "peers_used":  used,
        "pe":          _median(pe_values),
        "pb":          _median(pb_values),
        "ev_ebitda":   _median(ev_ebitda_values),
        "n_pe":        len(pe_values),
        "n_pb":        len(pb_values),
        "n_ev_ebitda": len(ev_ebitda_values),
    }


def _compute_sensitivity_grid(
    base_fcf: float, shares: float, projection_years: int, growth_rate: float,
    wacc_center: float, g_center: float,
) -> dict:
    """5×5 sensitivity grid of WACC × terminal-growth → IV per share."""
    wacc_steps = [wacc_center - 0.02, wacc_center - 0.01, wacc_center, wacc_center + 0.01, wacc_center + 0.02]
    g_steps    = [max(g_center - 0.02, 0.01), max(g_center - 0.01, 0.01), g_center, g_center + 0.01, g_center + 0.02]

    grid: list[list[float | None]] = []
    for wacc in wacc_steps:
        row: list[float | None] = []
        for g in g_steps:
            if wacc <= g:
                row.append(None)  # infinite terminal value
                continue
            fcf = base_fcf
            npv = 0.0
            for yr in range(1, projection_years + 1):
                fcf *= (1 + growth_rate)
                npv += fcf / (1 + wacc) ** yr
            terminal_fcf   = fcf * (1 + g)
            terminal_value = terminal_fcf / (wacc - g)
            npv           += terminal_value / (1 + wacc) ** projection_years
            iv_per_share   = npv / shares
            row.append(iv_per_share)
        grid.append(row)

    return {
        "wacc_steps": wacc_steps,
        "g_steps":    g_steps,
        "grid":       grid,
    }


async def _get_dcf_valuation(args: dict) -> list[types.TextContent]:
    ticker: str = args["ticker"].upper()
    discount_rate: float = float(args.get("discount_rate", 12.0)) / 100
    terminal_growth: float = float(args.get("terminal_growth", 5.0)) / 100
    bull_growth: float = float(args.get("bull_growth", 20.0)) / 100
    base_growth: float = float(args.get("base_growth", 12.0)) / 100
    bear_growth: float = float(args.get("bear_growth", 5.0)) / 100
    projection_years: int = int(args.get("projection_years", 5))
    peers_override: list[str] | None = args.get("peers")

    if discount_rate <= terminal_growth:
        return [types.TextContent(type="text", text="Error: discount_rate must exceed terminal_growth to avoid an infinite terminal value.")]

    try:
        import pandas as pd
        from datetime import date

        inc_json, bal_json, cf_json, ov_json, hist_json = await asyncio.gather(
            _vnstock_subprocess("income_statement", {"ticker": ticker, "period": "year"}),
            _vnstock_subprocess("balance_sheet",    {"ticker": ticker, "period": "year"}),
            _vnstock_subprocess("cash_flow",        {"ticker": ticker, "period": "year"}),
            _vnstock_subprocess("company_overview", {"ticker": ticker}),
            _vnstock_subprocess("quote_history",    {"ticker": ticker, "start": "2026-01-01", "end": date.today().isoformat()}),
        )

        income   = pd.DataFrame(json.loads(inc_json))   if inc_json.strip().startswith("[")  else pd.DataFrame()
        balance  = pd.DataFrame(json.loads(bal_json))   if bal_json.strip().startswith("[")  else pd.DataFrame()
        cashflow = pd.DataFrame(json.loads(cf_json))    if cf_json.strip().startswith("[")   else pd.DataFrame()
        ov_rows  = json.loads(ov_json)
        hist_rows = json.loads(hist_json)

        if income.empty or cashflow.empty:
            return [types.TextContent(type="text", text=f"Insufficient financial data for DCF on {ticker}.")]

        def _f(v: object, d: float = 0.0) -> float:
            try:
                return float(v) if v is not None else d
            except (TypeError, ValueError):
                return d

        if hist_rows and not isinstance(hist_rows, dict):
            current_price = _f(hist_rows[-1].get("close")) * 1000
        else:
            ov = ov_rows[0] if ov_rows and isinstance(ov_rows, list) else {}
            current_price = _f(ov.get("current_price"))

        if current_price <= 0:
            return [types.TextContent(type="text", text=f"Could not determine current price for {ticker}.")]

        def get_item_values(df: pd.DataFrame, item_id: str) -> dict[str, float]:
            if df.empty or "item_id" not in df.columns:
                return {}
            row = df[df["item_id"] == item_id]
            if row.empty:
                return {}
            year_cols = [c for c in df.columns if str(c).isdigit() or isinstance(c, int)]
            result: dict[str, float] = {}
            for col in year_cols:
                try:
                    val = float(row.iloc[0][col])
                    if val == val:  # exclude NaN
                        result[str(col)] = val
                except (TypeError, ValueError):
                    pass
            return result

        ocf_vals   = get_item_values(cashflow, "net_cash_from_operating_activities")
        capex_vals = get_item_values(cashflow, "purchases_of_fixed_assets_and_other")

        fcf_by_year: dict[str, float] = {}
        for year in sorted(ocf_vals.keys(), reverse=True):
            o = ocf_vals.get(year, 0.0)
            c = capex_vals.get(year, 0.0)  # negative in VN accounting
            if o:
                fcf_by_year[year] = o + c

        use_metric = "Free Cash Flow (OCF − Capex)"
        if not any(v > 0 for v in fcf_by_year.values()):
            net_profit_vals = get_item_values(income, "attributable_to_parent_company")
            fcf_by_year = {yr: v for yr, v in net_profit_vals.items() if v > 0}
            use_metric = "Net Profit to Parent (FCF unavailable — negative or zero)"

        if not fcf_by_year:
            return [types.TextContent(type="text", text=f"No positive cash flow or profit data available for {ticker} DCF.")]

        recent_years = sorted(fcf_by_year.keys(), reverse=True)
        valid_fcfs   = [fcf_by_year[y] for y in recent_years if fcf_by_year[y] > 0]
        base_value   = valid_fcfs[0]

        hist_growth_rate = None
        if len(valid_fcfs) >= 2:
            hist_growth_rate = (valid_fcfs[0] / valid_fcfs[-1]) ** (1 / (len(valid_fcfs) - 1)) - 1

        # Shares = Net Profit / EPS (both in VND)
        shares: float | None = None
        eps_vals        = get_item_values(income, "eps_basic_vnd")
        net_profit_vals = get_item_values(income, "attributable_to_parent_company")
        for year in sorted(eps_vals.keys(), reverse=True):
            eps    = eps_vals.get(year)
            profit = net_profit_vals.get(year)
            if eps and eps > 0 and profit and profit > 0:
                shares = profit / eps
                break

        if not shares:
            ov = ov_rows[0] if ov_rows and isinstance(ov_rows, list) else {}
            market_cap = _f(ov.get("market_cap"))
            if market_cap > 0 and current_price > 0:
                shares = market_cap / current_price

        if not shares or shares <= 0:
            return [types.TextContent(type="text", text=f"Could not determine shares outstanding for {ticker}.")]

        def calc_scenario(growth: float, label: str) -> dict:
            fcf_years = []
            fcf = base_value
            for yr in range(1, projection_years + 1):
                fcf = fcf * (1 + growth)
                pv  = fcf / (1 + discount_rate) ** yr
                fcf_years.append((yr, fcf, pv))

            terminal_fcf   = fcf_years[-1][1] * (1 + terminal_growth)
            terminal_value = terminal_fcf / (discount_rate - terminal_growth)
            pv_terminal    = terminal_value / (1 + discount_rate) ** projection_years
            total_npv      = sum(pv for _, _, pv in fcf_years) + pv_terminal
            iv_per_share   = total_npv / shares
            mos            = (iv_per_share - current_price) / current_price * 100

            return {
                "label":        label,
                "growth":       growth,
                "fcf_years":    fcf_years,
                "pv_terminal":  pv_terminal,
                "total_npv":    total_npv,
                "iv_per_share": iv_per_share,
                "mos":          mos,
            }

        scenarios = [
            calc_scenario(bull_growth, f"Bull  (+{bull_growth*100:.0f}%/yr)"),
            calc_scenario(base_growth, f"Base  (+{base_growth*100:.0f}%/yr)"),
            calc_scenario(bear_growth, f"Bear  (+{bear_growth*100:.0f}%/yr)"),
        ]

        weighted_dcf_iv  = scenarios[0]["iv_per_share"] * 0.3 + scenarios[1]["iv_per_share"] * 0.5 + scenarios[2]["iv_per_share"] * 0.2

        # ── Relative valuation via peer multiples ────────────────────────────
        ov = ov_rows[0] if ov_rows and isinstance(ov_rows, list) else {}
        sector = str(ov.get("sector", "")).strip()
        company_name = str(ov.get("organ_short_name") or ov.get("organ_name", ticker))

        # Target's own metrics for applying peer multiples.
        # Latest year — try several statement items because bank income statements
        # don't have "net_sales" (they use interest_income, net_interest_income, etc.)
        latest_year = None
        for probe_item in ("net_sales", "eps_basic_vnd", "attributable_to_parent_company",
                           "net_profit_loss_after_tax", "operating_profit_loss",
                           "interest_income_net", "interest_income"):
            years = sorted(get_item_values(income, probe_item).keys(), reverse=True)
            if years:
                latest_year = years[0]
                break
        target_eps    = eps_vals.get(latest_year) if latest_year else None
        target_profit = net_profit_vals.get(latest_year) if latest_year else None
        op_profit_vals = get_item_values(income, "operating_profit_loss")
        dep_vals       = get_item_values(cashflow, "depreciation_and_amortization")
        equity_vals    = get_item_values(balance, "owners_equity")
        st_borrow_vals = get_item_values(balance, "short_term_borrowings")
        lt_borrow_vals = get_item_values(balance, "long_term_borrowings")
        cash_vals      = get_item_values(balance, "cash_and_cash_equivalents")

        target_op_profit = op_profit_vals.get(latest_year) if latest_year else None
        target_dep       = dep_vals.get(latest_year) if latest_year else None
        target_ebitda    = (target_op_profit + target_dep) if (target_op_profit and target_dep) else target_op_profit
        target_equity    = equity_vals.get(latest_year) if latest_year else None
        target_net_debt  = ((st_borrow_vals.get(latest_year) or 0) +
                            (lt_borrow_vals.get(latest_year) or 0) -
                            (cash_vals.get(latest_year) or 0)) if latest_year else 0

        # Peer set selection (allow user override)
        peers = peers_override if peers_override else _lookup_sector_peers(sector)
        peers = [p for p in peers if p.upper() != ticker.upper()]
        peer_multiples = await _fetch_peer_multiples(ticker, peers) if peers else {"peers_used": [], "pe": None, "pb": None, "ev_ebitda": None}

        # Implied prices from each multiple
        implied_pe = target_eps * peer_multiples["pe"] if (target_eps and peer_multiples.get("pe")) else None
        implied_pb = (peer_multiples["pb"] * target_equity / shares) if (target_equity and peer_multiples.get("pb") and shares) else None
        implied_ev_ebitda = None
        if target_ebitda and target_ebitda > 0 and peer_multiples.get("ev_ebitda") and shares:
            implied_ev    = peer_multiples["ev_ebitda"] * target_ebitda
            implied_equity = implied_ev - target_net_debt
            implied_ev_ebitda = implied_equity / shares

        implied_relatives = [v for v in (implied_pe, implied_pb, implied_ev_ebitda) if v and v > 0]
        relative_iv = sum(implied_relatives) / len(implied_relatives) if implied_relatives else None

        # ── Blended (triangulated) implied price ─────────────────────────────
        weight_profile = _lookup_sector_weights(sector)
        w_dcf = weight_profile["dcf"]
        w_rel = weight_profile["relative"]

        # When DCF is structurally unreliable AND relative valuation also missing,
        # we have no defensible blended estimate — flag it instead of inventing one.
        dcf_unreliable = _is_dcf_unreliable(sector)
        insufficient_data = relative_iv is None and dcf_unreliable

        if insufficient_data:
            blended_iv = None
            blend_explained = (
                f"INSUFFICIENT DATA — DCF is unreliable for {sector or 'this sector'} and "
                f"no peer relative valuation could be computed (target metrics or peer multiples missing)"
            )
            w_dcf, w_rel = 0.0, 0.0
        elif relative_iv is None:
            blended_iv = weighted_dcf_iv
            blend_explained = "DCF only (relative valuation unavailable — no peer data)"
            w_dcf, w_rel = 1.0, 0.0
        else:
            total_w = w_dcf + w_rel
            w_dcf, w_rel = w_dcf / total_w, w_rel / total_w
            blended_iv = weighted_dcf_iv * w_dcf + relative_iv * w_rel
            blend_explained = f"DCF × {w_dcf:.2f} + Relative × {w_rel:.2f}"

        blended_mos = ((blended_iv - current_price) / current_price * 100) if blended_iv else None

        # ── Sensitivity grid (WACC × terminal growth) ────────────────────────
        sensitivity = _compute_sensitivity_grid(
            base_fcf=base_value, shares=shares, projection_years=projection_years,
            growth_rate=base_growth,
            wacc_center=discount_rate, g_center=terminal_growth,
        )

        # ── Build markdown output ────────────────────────────────────────────
        weighted_dcf_mos = (weighted_dcf_iv - current_price) / current_price * 100
        relative_mos = (relative_iv - current_price) / current_price * 100 if relative_iv else None

        lines = [
            f"## Triangulated Valuation — {ticker}",
            f"*Company: {company_name} | Sector: {sector or 'unknown'}*",
            f"*Metric: {use_metric} | WACC: {discount_rate*100:.0f}% | Terminal growth: {terminal_growth*100:.0f}%*",
            "",
        ]

        if _is_dcf_unreliable(sector):
            lines += [
                f"> ⚠️  **DCF is structurally unreliable for {sector}.** Earnings are cyclically distorted by",
                f"> provisioning timing (banks) or land bank revaluation (real estate). Output below leans",
                f"> heavily on relative valuation by design (see weight profile).",
                "",
            ]

        lines += [
            "### Historical Base Metric (VND)",
            "| Year | Value (B VND) |",
            "|---|---:|",
        ]
        for yr in sorted(fcf_by_year.keys(), reverse=True)[:5]:
            lines.append(f"| {yr} | {fcf_by_year[yr]/1e9:,.1f}B |")
        if hist_growth_rate is not None:
            lines.append(f"\n*Historical CAGR: {hist_growth_rate*100:.1f}% — guide for growth scenario inputs*")

        lines += [
            "",
            f"### Method 1: DCF",
            f"**Current Price: {current_price:,.0f} VND**",
            "",
            "| Scenario | FCF Growth | Intrinsic Value | Margin of Safety | Verdict |",
            "|---|---:|---:|---:|---|",
        ]
        for s in scenarios:
            mos     = s["mos"]
            verdict = "✅ Undervalued" if mos > 15 else "⚠️ Fair value" if mos > -10 else "❌ Overvalued"
            lines.append(f"| {s['label']} | {s['growth']*100:.0f}% | {s['iv_per_share']:,.0f} VND | {mos:+.1f}% | {verdict} |")

        lines.append(f"\n**DCF probability-weighted IV: {weighted_dcf_iv:,.0f} VND** ({weighted_dcf_mos:+.1f}%) — 30% bull / 50% base / 20% bear")

        # Relative section
        lines += [
            "",
            "### Method 2: Relative (peer multiples)",
        ]
        if not peer_multiples["peers_used"]:
            lines.append(f"*No peer data available for sector `{sector or '(unknown)'}`. Skipping relative method.*")
        else:
            lines += [
                f"*Peers used ({len(peer_multiples['peers_used'])}): {', '.join(peer_multiples['peers_used'])}*",
                "",
                "| Multiple | Target | Peer Median | Implied Price | vs Current |",
                "|---|---:|---:|---:|---:|",
            ]
            # Target's own multiples
            target_market_cap = shares * current_price
            own_pe        = (current_price / target_eps) if target_eps and target_eps > 0 else None
            own_pb        = (target_market_cap / target_equity) if target_equity and target_equity > 0 else None
            own_ev_ebitda = ((target_market_cap + target_net_debt) / target_ebitda) if target_ebitda and target_ebitda > 0 else None

            def _row(label: str, own: float | None, peer: float | None, implied: float | None):
                if implied is None:
                    return f"| {label} | {f'{own:.1f}x' if own else '—'} | {f'{peer:.1f}x' if peer else '—'} | — | — |"
                mos = (implied - current_price) / current_price * 100
                return f"| {label} | {f'{own:.1f}x' if own else '—'} | {f'{peer:.1f}x' if peer else '—'} | {implied:,.0f} VND | {mos:+.1f}% |"

            lines.append(_row("P/E",        own_pe,        peer_multiples["pe"],        implied_pe))
            lines.append(_row("P/B",        own_pb,        peer_multiples["pb"],        implied_pb))
            lines.append(_row("EV/EBITDA",  own_ev_ebitda, peer_multiples["ev_ebitda"], implied_ev_ebitda))

            if relative_iv:
                lines.append(f"\n**Relative average IV: {relative_iv:,.0f} VND** ({relative_mos:+.1f}%) — mean of valid implied prices")

        # SOTP placeholder
        lines += [
            "",
            "### Method 3: SOTP (sum-of-the-parts)",
            "*Not implemented yet — single-segment treatment assumed. Adds value mainly for conglomerates with 2+ distinct businesses.*",
        ]

        # Blended
        lines += [
            "",
            "### Blended Implied Price",
            f"*Formula: {blend_explained}*",
            "",
        ]
        if blended_iv is None:
            lines += [
                f"**Verdict: ⚪ INSUFFICIENT DATA**",
                f"*Add explicit peers via the `peers` parameter, or use a P/B-only screen for this sector.*",
            ]
        else:
            verdict_label = (
                "✅ MATERIALLY UNDERVALUED" if blended_mos > 25 else
                "🟢 UNDERVALUED"             if blended_mos > 10 else
                "🟡 FAIR VALUE"              if blended_mos > -10 else
                "🟠 OVERVALUED"              if blended_mos > -25 else
                "🔴 MATERIALLY OVERVALUED"
            )
            lines += [
                f"**Blended IV: {blended_iv:,.0f} VND**",
                f"**Margin of safety vs current ({current_price:,.0f} VND): {blended_mos:+.1f}%**",
                f"**Verdict: {verdict_label}**",
            ]

        # Sensitivity grid
        lines += [
            "",
            f"### Sensitivity Grid — WACC × Terminal Growth",
            f"*Cells show IV per share (VND) at base FCF growth = {base_growth*100:.0f}%. Bold marks your chosen assumption.*",
            "",
            "| WACC ↓ \\ g → | " + " | ".join(f"**{g*100:.0f}%**" for g in sensitivity["g_steps"]) + " |",
            "|---|" + "---:|" * len(sensitivity["g_steps"]),
        ]
        for i, wacc in enumerate(sensitivity["wacc_steps"]):
            row_cells = []
            for j, g in enumerate(sensitivity["g_steps"]):
                val = sensitivity["grid"][i][j]
                if val is None:
                    cell = "—"
                else:
                    cell = f"{val:,.0f}"
                # Mark the user-chosen cell
                if abs(wacc - discount_rate) < 1e-6 and abs(g - terminal_growth) < 1e-6:
                    cell = f"**[{cell}]**"
                row_cells.append(cell)
            wacc_label = f"**{wacc*100:.0f}%**" if abs(wacc - discount_rate) < 1e-6 else f"{wacc*100:.0f}%"
            lines.append(f"| {wacc_label} | " + " | ".join(row_cells) + " |")

        # Base case projection (kept from original)
        lines += [
            "",
            f"### Base Case Cash Flow Projection ({projection_years}Y)",
            "| Year | Projected FCF (B) | PV (B) |",
            "|---|---:|---:|",
        ]
        base = scenarios[1]
        for yr_num, fcf, pv in base["fcf_years"]:
            lines.append(f"| Year {yr_num} | {fcf/1e9:,.1f}B | {pv/1e9:,.1f}B |")
        lines += [
            f"| Terminal Value | — | {base['pv_terminal']/1e9:,.1f}B |",
            f"| **Total NPV** | | **{base['total_npv']/1e9:,.1f}B** |",
            "",
            f"*Shares outstanding: ~{shares/1e6:.0f}M | Sector weight profile: DCF {w_dcf:.0%} / Relative {w_rel:.0%}*",
            "",
            "*Triangulation reduces single-method brittleness — but valuation remains sensitive to inputs.",
            "Sanity-check assumptions against historical CAGR + the sensitivity grid above.*",
        ]
        text = "\n".join(lines)

    except Exception as e:
        text = f"Triangulated valuation failed for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


async def _get_position_sizing(args: dict) -> list[types.TextContent]:
    ticker: str          = args["ticker"].upper()
    portfolio_value: float = float(args["portfolio_value"])
    risk_per_trade_pct: float = float(args.get("risk_per_trade_pct", 2.0))
    conviction: str      = args.get("conviction", "medium").lower()
    atr_multiplier: float = float(args.get("atr_multiplier", 2.0))

    if portfolio_value <= 0:
        return [types.TextContent(type="text", text="Error: portfolio_value must be a positive VND amount.")]

    conviction_multipliers = {"low": 0.5, "medium": 1.0, "high": 1.5}
    conviction_mult    = conviction_multipliers.get(conviction, 1.0)
    effective_risk_pct = (risk_per_trade_pct / 100) * conviction_mult
    max_loss_vnd       = portfolio_value * effective_risk_pct

    try:
        import pandas as pd
        import pandas_ta as ta

        raw  = await _vnstock_subprocess("quote_history_full", {"ticker": ticker, "days": 30})
        rows = json.loads(raw)

        if not rows or isinstance(rows, dict):
            return [types.TextContent(type="text", text=f"No price data available for {ticker}.")]

        df = pd.DataFrame(rows)
        df["close"] = df["close"].astype(float) * 1000
        df["high"]  = df["high"].astype(float)  * 1000
        df["low"]   = df["low"].astype(float)   * 1000
        df = df.sort_values("time").reset_index(drop=True)

        current_price = float(df["close"].iloc[-1])
        atr_series    = ta.atr(df["high"], df["low"], df["close"], length=14)
        atr           = float(atr_series.iloc[-1]) if atr_series is not None and not atr_series.empty else None

        if not atr or atr <= 0:
            return [types.TextContent(type="text", text=f"Insufficient data to compute ATR for {ticker} (need ≥14 trading days).")]

        stop_distance  = atr * atr_multiplier
        stop_price     = current_price - stop_distance
        max_shares     = int(max_loss_vnd / stop_distance)
        position_value = max_shares * current_price
        position_pct   = (position_value / portfolio_value) * 100

        # Cap at 20% single-position limit
        max_single_pct = 20.0
        capped_shares  = max_shares
        cap_note       = ""
        if position_pct > max_single_pct:
            capped_shares  = int((portfolio_value * max_single_pct / 100) / current_price)
            position_value = capped_shares * current_price
            position_pct   = max_single_pct
            cap_note       = f"\n⚠️  **Size capped at 20% single-position limit**: {capped_shares:,} shares"

        lines = [
            f"## Position Sizing — {ticker}",
            f"*{conviction.title()} conviction: {risk_per_trade_pct:.1f}% × {conviction_mult:.1f}x = {effective_risk_pct*100:.2f}% risk*\n",
            "### Market Context",
            "| Metric | Value |",
            "|---|---:|",
            f"| Current Price | {current_price:,.0f} VND |",
            f"| ATR (14-day) | {atr:,.0f} VND ({atr/current_price*100:.1f}% of price) |",
            "",
            "### Calculated Position",
            "| Parameter | Value |",
            "|---|---:|",
            f"| Max loss allowed | {max_loss_vnd/1e6:.2f}M VND |",
            f"| Stop distance ({atr_multiplier:.1f}× ATR) | {stop_distance:,.0f} VND |",
            f"| Suggested stop-loss | {stop_price:,.0f} VND |",
            f"| **Shares to buy** | **{capped_shares:,}** |",
            f"| **Position value** | **{position_value/1e6:.1f}M VND** |",
            f"| Portfolio weight | {position_pct:.1f}% |",
            cap_note,
            "",
            "### Risk / Reward at Key Levels",
            "| Level | Price (VND) | P&L / Share | Total P&L |",
            "|---|---:|---:|---:|",
            f"| Entry | {current_price:,.0f} | — | — |",
            f"| Stop-loss | {stop_price:,.0f} | -{stop_distance:,.0f} | -{max_loss_vnd/1e6:.2f}M |",
            f"| Target 1:1 | {current_price+stop_distance:,.0f} | +{stop_distance:,.0f} | +{max_loss_vnd/1e6:.2f}M |",
            f"| Target 2:1 | {current_price+stop_distance*2:,.0f} | +{stop_distance*2:,.0f} | +{max_loss_vnd*2/1e6:.2f}M |",
            f"| Target 3:1 | {current_price+stop_distance*3:,.0f} | +{stop_distance*3:,.0f} | +{max_loss_vnd*3/1e6:.2f}M |",
            "",
            "### Diversification Rules",
            "- Max single position: 20% of portfolio",
            "- Max sector concentration: 35% of portfolio",
            "- Minimum cash reserve: 5% for opportunities",
            "- Review and rebalance if any position drifts >5% above target weight",
        ]
        text = "\n".join(lines)

    except Exception as e:
        text = f"Position sizing failed for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


async def _save_investment_thesis(args: dict) -> list[types.TextContent]:
    ticker: str                = args["ticker"].upper()
    thesis: str                = args["thesis"]
    buy_price: float           = float(args["buy_price"])
    target_price: float        = float(args["target_price"])
    stop_price: float          = float(args["stop_price"])
    conviction: str            = args.get("conviction", "Medium")
    falsification: str         = args["falsification_criteria"]
    catalysts: str             = args.get("catalysts", "").strip()
    strongest_bias: str        = args.get("strongest_bias", "").strip()
    premortem_reason: str      = args.get("premortem_reason", "").strip()

    upside   = (target_price - buy_price) / buy_price * 100
    downside = (stop_price   - buy_price) / buy_price * 100
    rr       = abs(upside / downside) if downside != 0 else 0.0

    from datetime import date
    date_str = date.today().isoformat()

    THESES_DIR.mkdir(exist_ok=True)

    filename = f"{ticker}_thesis_{date_str}.md"
    filepath = THESES_DIR / filename

    content = f"""# Investment Thesis — {ticker}
**Date:** {date_str}  |  **Conviction:** {conviction}  |  **R/R:** {rr:.1f}:1

---

## Core Thesis
{thesis}

---

## Price Targets
| | Price (VND) | vs Entry |
|---|---:|---:|
| Entry (buy zone) | {buy_price:,.0f} | — |
| 12-month target | {target_price:,.0f} | +{upside:.1f}% |
| Stop-loss | {stop_price:,.0f} | {downside:.1f}% |

---

## Falsification Criteria
*Exit immediately if any of the following occur:*

{falsification}

---

## Catalysts
{catalysts if catalysts else 'None specified.'}

---

## Pre-Mortem (Bias Check)
**Strongest bias likely affecting this thesis:** {strongest_bias if strongest_bias else '*Not specified — go back and identify before trading.*'}

**If wrong 12 months from now, the most likely reason:** {premortem_reason if premortem_reason else '*Not specified — pre-mortem is the single highest-leverage discipline. Add this before saving.*'}

---

## Review Log
| Date | Price (VND) | Action | Notes |
|---|---:|---|---|
| {date_str} | {buy_price:,.0f} | Thesis written | Initial entry |
"""

    filepath.write_text(content, encoding="utf-8")

    index_path  = THESES_DIR / "INDEX.md"
    index_line  = f"- [{ticker} — {date_str}]({filename}) — {conviction} conviction | R/R {rr:.1f}:1 | target {target_price:,.0f} VND | stop {stop_price:,.0f} VND\n"
    if index_path.exists():
        existing = index_path.read_text(encoding="utf-8")
        if filename not in existing:
            index_path.write_text(existing + index_line, encoding="utf-8")
    else:
        index_path.write_text(f"# Investment Theses\n\n{index_line}", encoding="utf-8")

    return [types.TextContent(
        type="text",
        text=(
            f"Thesis saved: {filepath}\n"
            f"R/R: {rr:.1f}:1  |  Upside: +{upside:.1f}%  |  Downside: {downside:.1f}%\n"
            f"Index: {index_path}"
        ),
    )]


async def _save_decision_log(args: dict) -> list[types.TextContent]:
    ticker: str    = args["ticker"].upper()
    action: str    = args["action"].upper()
    price: float   = float(args["price"])
    rationale: str = args["rationale"]
    quantity: int  = int(args.get("quantity", 0))
    outcome: str   = args.get("outcome", "").strip()

    from datetime import date
    date_str = date.today().isoformat()

    DECISIONS_DIR.mkdir(exist_ok=True)

    log_path   = DECISIONS_DIR / "LOG.md"
    qty_str    = f"{quantity:,}" if quantity else "—"
    outcome_str = outcome or "Pending"
    entry = f"| {date_str} | {ticker} | **{action}** | {price:,.0f} | {qty_str} | {rationale} | {outcome_str} |\n"

    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
        log_path.write_text(existing + entry, encoding="utf-8")
    else:
        header = (
            "# Decision Journal\n\n"
            "*Log every buy/sell/add/trim with rationale at execution. Review quarterly.*\n\n"
            "| Date | Ticker | Action | Price (VND) | Shares | Rationale | Outcome |\n"
            "|---|---|---|---:|---:|---|---|\n"
        )
        log_path.write_text(header + entry, encoding="utf-8")

    value_note = f" ({price * quantity / 1e6:.1f}M VND)" if quantity else ""
    return [types.TextContent(
        type="text",
        text=f"Decision logged: {action} {ticker} @ {price:,.0f} VND{value_note}\nJournal: {log_path}",
    )]


def _parse_decision_log(raw: str) -> list[dict]:
    """Extract structured decisions from the LOG.md markdown table."""
    from datetime import date as _date
    decisions: list[dict] = []

    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "Date" in line and "Ticker" in line and "Action" in line:
            continue
        stripped_chars = set(line.replace("|", "").replace(" ", "").replace("-", "").replace(":", ""))
        if not stripped_chars:
            continue

        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 6:
            continue

        try:
            d = _date.fromisoformat(cells[0])
            ticker = cells[1].upper()
            action = cells[2].strip("*").upper()
            price = float(cells[3].replace(",", ""))
            qty_str = cells[4].replace(",", "").strip()
            qty = int(qty_str) if qty_str and qty_str not in ("—", "-") else 0
            rationale = cells[5]
            outcome = cells[6] if len(cells) > 6 else ""

            decisions.append({
                "date": d,
                "ticker": ticker,
                "action": action,
                "price": price,
                "quantity": qty,
                "rationale": rationale,
                "outcome": outcome.strip(),
            })
        except (ValueError, IndexError):
            continue

    return sorted(decisions, key=lambda x: x["date"])


def _pair_trades(decisions: list[dict]) -> tuple[list[dict], dict]:
    """Walk decisions chronologically and realize P&L using FIFO lot matching."""
    positions: dict[str, list[dict]] = {}
    closed: list[dict] = []

    for d in decisions:
        ticker = d["ticker"]
        action = d["action"]
        price = d["price"]
        qty = d["quantity"]

        if action == "HOLD":
            continue
        if qty <= 0:
            continue

        positions.setdefault(ticker, [])

        if action in ("BUY", "ADD"):
            positions[ticker].append({"date": d["date"], "price": price, "qty": qty})
            continue

        if action in ("SELL", "TRIM"):
            qty_to_sell = qty
            while qty_to_sell > 0 and positions[ticker]:
                lot = positions[ticker][0]
                taken = min(qty_to_sell, lot["qty"])
                pnl_pct = (price - lot["price"]) / lot["price"] * 100 if lot["price"] else 0
                closed.append({
                    "ticker": ticker,
                    "buy_date": lot["date"],
                    "sell_date": d["date"],
                    "buy_price": lot["price"],
                    "sell_price": price,
                    "qty": taken,
                    "pnl": (price - lot["price"]) * taken,
                    "pnl_pct": pnl_pct,
                    "hold_days": (d["date"] - lot["date"]).days,
                })
                lot["qty"] -= taken
                qty_to_sell -= taken
                if lot["qty"] == 0:
                    positions[ticker].pop(0)

    open_positions: dict[str, dict] = {}
    for ticker, lots in positions.items():
        if not lots:
            continue
        total_qty = sum(lot["qty"] for lot in lots)
        if total_qty <= 0:
            continue
        avg_cost = sum(lot["price"] * lot["qty"] for lot in lots) / total_qty
        open_positions[ticker] = {
            "qty": total_qty,
            "avg_cost": avg_cost,
            "first_buy": min(lot["date"] for lot in lots),
        }

    return closed, open_positions


def _compute_performance_metrics(closed_trades: list[dict]) -> dict:
    if not closed_trades:
        return {"total_trades": 0}

    winners = [t for t in closed_trades if t["pnl"] > 0]
    losers  = [t for t in closed_trades if t["pnl"] < 0]
    n = len(closed_trades)
    win_rate = len(winners) / n * 100

    avg_winner_pct = sum(t["pnl_pct"] for t in winners) / len(winners) if winners else 0
    avg_loser_pct  = sum(t["pnl_pct"] for t in losers)  / len(losers)  if losers  else 0
    expectancy_pct = (win_rate / 100) * avg_winner_pct + (1 - win_rate / 100) * avg_loser_pct

    max_streak = current_streak = 0
    for t in sorted(closed_trades, key=lambda x: x["sell_date"]):
        if t["pnl"] < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    return {
        "total_trades":   n,
        "winners":        len(winners),
        "losers":         len(losers),
        "win_rate":       win_rate,
        "avg_winner_pct": avg_winner_pct,
        "avg_loser_pct":  avg_loser_pct,
        "expectancy_pct": expectancy_pct,
        "total_pnl":      sum(t["pnl"] for t in closed_trades),
        "max_consecutive_losses": max_streak,
        "avg_hold_days":  sum(t["hold_days"] for t in closed_trades) / n,
    }


def _find_stale_pending(decisions: list[dict], max_age_days: int = 90) -> list[dict]:
    from datetime import date as _date, timedelta
    cutoff = _date.today() - timedelta(days=max_age_days)
    stale = []
    for d in decisions:
        outcome = (d.get("outcome") or "").lower().strip()
        if outcome in ("", "pending", "—", "-") and d["date"] <= cutoff:
            stale.append({**d, "days_old": (_date.today() - d["date"]).days})
    return sorted(stale, key=lambda x: x["days_old"], reverse=True)


def _cluster_losses(closed_trades: list[dict]) -> dict:
    losers = [t for t in closed_trades if t["pnl"] < 0]
    if not losers:
        return {"by_ticker": {}, "by_hold_period": {}, "patterns": []}

    by_ticker: dict[str, int] = {}
    for t in losers:
        by_ticker[t["ticker"]] = by_ticker.get(t["ticker"], 0) + 1

    buckets = {"<7d": 0, "7-30d": 0, "30-90d": 0, ">90d": 0}
    for t in losers:
        days = t["hold_days"]
        if   days < 7:  buckets["<7d"]    += 1
        elif days < 30: buckets["7-30d"]  += 1
        elif days < 90: buckets["30-90d"] += 1
        else:           buckets[">90d"]   += 1

    n_losers = len(losers)
    patterns: list[str] = []

    if buckets["<7d"] / n_losers > 0.4:
        patterns.append("Many quick losses (<7d) — possible momentum chasing or premature entries")
    if buckets[">90d"] / n_losers > 0.4:
        patterns.append("Many slow-grinder losses (>90d) — possible loss aversion (not cutting losers)")
    for ticker, count in by_ticker.items():
        if count / n_losers > 0.3 and count >= 2:
            patterns.append(f"{ticker} alone accounts for {count}/{n_losers} losses — concentration risk")

    return {"by_ticker": by_ticker, "by_hold_period": buckets, "patterns": patterns}


def _make_verdict(metrics: dict) -> tuple[str, str]:
    n = metrics.get("total_trades", 0)
    if n < 5:
        return (
            "⚠️  INSUFFICIENT SAMPLE",
            "Fewer than 5 closed trades — results so far are noise, not signal. Keep logging; review again at 10+ closed trades.",
        )

    exp   = metrics["expectancy_pct"]
    win   = metrics["win_rate"]
    avg_w = metrics["avg_winner_pct"]
    avg_l = abs(metrics["avg_loser_pct"])

    if exp > 3 and win >= 50:
        return (
            "🟢 PROFITABLE PROCESS",
            "Process is working. Document what's driving the wins and continue. Do not increase position size yet — let the sample grow to 30+ trades before scaling.",
        )
    if exp > 0 and avg_w > avg_l:
        return (
            "🟡 MARGINALLY PROFITABLE",
            "Positive expectancy but slim margin. Focus on cutting losers earlier. Do not add new position categories until expectancy stabilizes above 3%.",
        )
    if exp <= 0 and avg_l > avg_w * 1.5:
        return (
            "🔴 PROCESS LEAK: HOLDING LOSERS TOO LONG",
            "Average loser is materially larger than average winner — classic loss aversion. "
            "Action: enforce stop-losses on every position, no exceptions. Downgrade all high-conviction sizes to medium until expectancy turns positive.",
        )
    if exp <= 0 and win < 40:
        return (
            "🔴 PROCESS LEAK: LOW HIT RATE",
            "Win rate below 40% — entry timing or stock selection is off. "
            "Stop new entries for 30 days. Review losing theses: was the falsification criterion triggered? If yes, you were slow to exit. If no, the thesis structure itself was wrong.",
        )

    return (
        "🟠 UNDERPERFORMING — TRIAGE NEEDED",
        "Expectancy is negative or flat. Follow the triage framework: cut position sizes by 50%, halt new entries for 30 days, "
        "classify each losing trade as good-process or bad-process, and fix the single biggest systematic error before resuming.",
    )


async def _review_performance(args: dict) -> list[types.TextContent]:
    lookback_days: int = int(args.get("lookback_days", 365))

    log_path = DECISIONS_DIR / "LOG.md"
    if not log_path.exists():
        return [types.TextContent(type="text", text=(
            "## Performance Review\n\n"
            f"No decision log found at `{log_path}`.\n\n"
            "Start logging decisions with `save_decision_log` to build a track record. "
            "Performance review becomes meaningful at 10+ closed trades."
        ))]

    decisions = _parse_decision_log(log_path.read_text(encoding="utf-8"))
    if not decisions:
        return [types.TextContent(type="text", text=(
            "## Performance Review\n\n"
            f"Decision log exists at `{log_path}` but no valid entries were parsed. "
            "Each row must match: `| Date | Ticker | Action | Price | Shares | Rationale | Outcome |`"
        ))]

    from datetime import date as _date, timedelta
    cutoff = _date.today() - timedelta(days=lookback_days)
    in_window = [d for d in decisions if d["date"] >= cutoff]

    closed_trades, open_positions = _pair_trades(in_window)
    metrics  = _compute_performance_metrics(closed_trades)
    pending  = _find_stale_pending(in_window)
    clusters = _cluster_losses(closed_trades)

    lines = [
        f"## Performance Review — {_date.today().isoformat()}",
        f"*Lookback: {lookback_days} days | {len(in_window)} decisions parsed*\n",
    ]

    if metrics["total_trades"] == 0:
        lines += [
            "**No closed round-trips to evaluate yet.**\n",
            f"- Open positions: {len(open_positions)}",
            f"- Total decisions in window: {len(in_window)}",
            f"- Stale pending (>90d, no outcome): {len(pending)}",
            "",
            "Log SELL/TRIM decisions with quantities to close trades and build a performance record.",
        ]
        return [types.TextContent(type="text", text="\n".join(lines))]

    pnl_display = f"{metrics['total_pnl']/1e6:+,.2f}M" if abs(metrics['total_pnl']) >= 1e6 else f"{metrics['total_pnl']:+,.0f}"

    lines += [
        "### Summary",
        "| Metric | Value |",
        "|---|---:|",
        f"| Closed trades | {metrics['total_trades']} |",
        f"| Open positions | {len(open_positions)} |",
        f"| Win rate | {metrics['win_rate']:.1f}% ({metrics['winners']}W / {metrics['losers']}L) |",
        f"| Avg winner | +{metrics['avg_winner_pct']:.2f}% |",
        f"| Avg loser | {metrics['avg_loser_pct']:.2f}% |",
        f"| Expectancy per trade | {metrics['expectancy_pct']:+.2f}% |",
        f"| Total realized P&L | {pnl_display} VND |",
        f"| Max consecutive losses | {metrics['max_consecutive_losses']} |",
        f"| Avg hold period | {metrics['avg_hold_days']:.0f} days |",
        "",
    ]

    verdict, advice = _make_verdict(metrics)
    lines += [
        "### Verdict",
        f"**{verdict}**",
        "",
        advice,
        "",
    ]

    if pending:
        lines += [
            f"### ⚠️  Stale Pending Decisions ({len(pending)})",
            "*Older than 90 days with no outcome — update these. Undocumented outcomes hide skill from luck.*\n",
            "| Date | Ticker | Action | Price | Days Open | Rationale |",
            "|---|---|---|---:|---:|---|",
        ]
        for p in pending[:10]:
            rationale_short = (p["rationale"][:60] + "…") if len(p["rationale"]) > 60 else p["rationale"]
            lines.append(
                f"| {p['date']} | {p['ticker']} | {p['action']} | {p['price']:,.0f} | {p['days_old']} | {rationale_short} |"
            )
        lines.append("")

    if clusters["patterns"] or clusters["by_ticker"]:
        lines.append("### Loss Clustering")
        if clusters["patterns"]:
            lines.append("**Pattern flags:**")
            lines += [f"- ⚠️  {p}" for p in clusters["patterns"]]
            lines.append("")

        if clusters["by_ticker"]:
            lines += [
                "**Losses by ticker:**",
                "| Ticker | Losing trades |",
                "|---|---:|",
            ]
            for ticker, count in sorted(clusters["by_ticker"].items(), key=lambda x: -x[1]):
                lines.append(f"| {ticker} | {count} |")
            lines.append("")

        if any(clusters["by_hold_period"].values()):
            lines += [
                "**Losses by hold period:**",
                "| Bucket | Count |",
                "|---|---:|",
            ]
            for bucket, count in clusters["by_hold_period"].items():
                if count > 0:
                    lines.append(f"| {bucket} | {count} |")
            lines.append("")

    if open_positions:
        lines += [
            f"### Open Positions ({len(open_positions)})",
            "| Ticker | Shares | Avg Cost (VND) | First Buy |",
            "|---|---:|---:|---|",
        ]
        for ticker in sorted(open_positions.keys()):
            p = open_positions[ticker]
            lines.append(f"| {ticker} | {p['qty']:,} | {p['avg_cost']:,.0f} | {p['first_buy']} |")
        lines.append("")

    if closed_trades:
        lines += [
            "### Recent Closed Trades (last 10)",
            "| Sell Date | Ticker | Hold (d) | P&L % | P&L (VND) |",
            "|---|---|---:|---:|---:|",
        ]
        for t in sorted(closed_trades, key=lambda x: x["sell_date"], reverse=True)[:10]:
            sign = "🟢" if t["pnl"] > 0 else "🔴"
            lines.append(
                f"| {t['sell_date']} | {sign} {t['ticker']} | {t['hold_days']} | {t['pnl_pct']:+.2f}% | {t['pnl']:+,.0f} |"
            )
        lines.append("")

    lines += [
        "---",
        "*Next step: if verdict is red or orange, follow the triage framework in `vn-risk-manager` skill — cut sizes, halt new entries 30 days, classify each loser as good-process or bad-process.*",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


WATCHLIST_PATH = Path(__file__).parent / ".watchlist.json"


def _get_item_series(df, item_id: str) -> dict[str, float]:
    """Return a {year_str: value} mapping for one item_id across all year columns."""
    if df is None or df.empty or "item_id" not in df.columns:
        return {}
    row = df[df["item_id"] == item_id]
    if row.empty:
        return {}
    year_cols = [c for c in df.columns if str(c).isdigit() or isinstance(c, int)]
    out: dict[str, float] = {}
    for col in year_cols:
        try:
            v = float(row.iloc[0][col])
            if v == v:
                out[str(col)] = v
        except (TypeError, ValueError):
            pass
    return out


async def _get_earnings_quality(args: dict) -> list[types.TextContent]:
    ticker: str = args["ticker"].upper()

    try:
        import pandas as pd
        inc_json, bal_json, cf_json = await asyncio.gather(
            _vnstock_subprocess("income_statement", {"ticker": ticker, "period": "year"}),
            _vnstock_subprocess("balance_sheet",    {"ticker": ticker, "period": "year"}),
            _vnstock_subprocess("cash_flow",        {"ticker": ticker, "period": "year"}),
        )
        inc = pd.DataFrame(json.loads(inc_json)) if inc_json.strip().startswith("[") else pd.DataFrame()
        bal = pd.DataFrame(json.loads(bal_json)) if bal_json.strip().startswith("[") else pd.DataFrame()
        cf  = pd.DataFrame(json.loads(cf_json))  if cf_json.strip().startswith("[")  else pd.DataFrame()

        if inc.empty or bal.empty or cf.empty:
            return [types.TextContent(type="text", text=f"Insufficient financial data for earnings quality on {ticker}.")]

        net_sales      = _get_item_series(inc, "net_sales")
        net_profit     = _get_item_series(inc, "attributable_to_parent_company")
        total_assets   = _get_item_series(bal, "total_assets")
        current_assets = _get_item_series(bal, "current_assets")
        current_liab   = _get_item_series(bal, "current_liabilities")
        ocf            = _get_item_series(cf,  "net_cash_from_operating_activities") or _get_item_series(cf, "net_cash_inflows_outflows_from_op")
        capex          = _get_item_series(cf,  "purchases_of_fixed_assets_and_other")

        years = sorted(set(net_profit.keys()) & set(ocf.keys()) & set(total_assets.keys()), reverse=True)[:4]
        if len(years) < 2:
            return [types.TextContent(type="text", text=f"Need at least 2 years of data for earnings quality on {ticker}.")]

        rows = []
        for i, yr in enumerate(years):
            ni  = net_profit.get(yr, 0)
            op  = ocf.get(yr, 0)
            cx  = capex.get(yr, 0)
            sa  = net_sales.get(yr, 0)
            ta  = total_assets.get(yr, 0)
            fcf = (op + cx) if cx else op

            # Accruals = (NI − OCF) / Avg Total Assets
            avg_ta = (ta + total_assets.get(years[i+1], ta)) / 2 if i + 1 < len(years) else ta
            accruals = (ni - op) / avg_ta if avg_ta else None

            # Working capital change as % of revenue
            wc_curr = (current_assets.get(yr, 0)              - current_liab.get(yr, 0))
            wc_prev = (current_assets.get(years[i+1], wc_curr) - current_liab.get(years[i+1], 0)) if i + 1 < len(years) else wc_curr
            wc_change_pct = (wc_curr - wc_prev) / sa * 100 if sa else None

            rows.append({
                "year":          yr,
                "ni_b":          ni / 1e9,
                "ocf_b":         op / 1e9,
                "fcf_b":         fcf / 1e9,
                "fcf_ni_ratio":  (fcf / ni) if ni and ni > 0 else None,
                "ocf_margin":    (op / sa * 100) if sa else None,
                "accruals":      accruals,
                "wc_change_pct": wc_change_pct,
            })

        # Score most recent year on five dimensions (0-20 each)
        latest = rows[0]
        scores: dict[str, int] = {}

        # FCF/NI ratio: >1 = great, 0.5-1 = ok, <0.5 = weak
        r = latest["fcf_ni_ratio"]
        scores["FCF/NI quality"] = 20 if r and r > 1.0 else 15 if r and r > 0.8 else 10 if r and r > 0.5 else 5 if r and r > 0.2 else 0

        # OCF margin: vs revenue
        m = latest["ocf_margin"]
        scores["OCF margin"] = 20 if m and m > 15 else 15 if m and m > 10 else 10 if m and m > 5 else 5 if m and m > 0 else 0

        # Accruals: lower abs = better (Sloan ratio)
        a = abs(latest["accruals"]) if latest["accruals"] is not None else None
        scores["Accruals (low = good)"] = 20 if a is not None and a < 0.03 else 15 if a is not None and a < 0.06 else 10 if a is not None and a < 0.10 else 5 if a is not None and a < 0.15 else 0

        # Working capital trend (smaller increase = better)
        w = latest["wc_change_pct"]
        scores["WC discipline"] = 20 if w is not None and abs(w) < 2 else 15 if w is not None and abs(w) < 5 else 10 if w is not None and abs(w) < 10 else 5 if w is not None and abs(w) < 20 else 0

        # OCF stability across years
        ocf_vals = [r["ocf_b"] for r in rows if r["ocf_b"]]
        positive_years = sum(1 for v in ocf_vals if v > 0)
        scores["OCF consistency"] = int(20 * positive_years / len(ocf_vals)) if ocf_vals else 0

        total = sum(scores.values())
        verdict = "🟢 HIGH QUALITY" if total >= 75 else "🟡 MODERATE" if total >= 50 else "🟠 LOW QUALITY" if total >= 30 else "🔴 SUSPECT"

        lines = [
            f"## Earnings Quality — {ticker}",
            f"**Score: {total}/100 — {verdict}**\n",
            "### Multi-Year Trend",
            "| Year | NI (B) | OCF (B) | FCF (B) | FCF/NI | OCF Margin | Accruals | WC Δ % |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in rows:
            fcf_ni = f"{r['fcf_ni_ratio']:.2f}x"  if r['fcf_ni_ratio']  is not None else "—"
            ocf_m  = f"{r['ocf_margin']:.1f}%"    if r['ocf_margin']    is not None else "—"
            accr   = f"{r['accruals']:+.3f}"      if r['accruals']      is not None else "—"
            wc     = f"{r['wc_change_pct']:+.1f}%" if r['wc_change_pct'] is not None else "—"
            lines.append(
                f"| {r['year']} | {r['ni_b']:,.1f} | {r['ocf_b']:,.1f} | {r['fcf_b']:,.1f} | {fcf_ni} | {ocf_m} | {accr} | {wc} |"
            )

        lines += ["", "### Score Breakdown", "| Dimension | Score |", "|---|---:|"]
        for label, val in scores.items():
            lines.append(f"| {label} | {val}/20 |")
        lines += [
            "",
            "### Interpretation",
            "- **FCF/NI > 1.0**: cash exceeds reported profit — high quality",
            "- **Accruals > 0.10 (abs)**: NI inflated by non-cash items — suspect",
            "- **WC change > 10% of revenue**: receivables or inventory growing faster than sales — possible channel stuffing",
            "- **Negative OCF years**: cash earnings are unreliable — discount valuation",
        ]
        text = "\n".join(lines)
    except Exception as e:
        text = f"Earnings quality failed for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


async def _get_foreign_flow(args: dict) -> list[types.TextContent]:
    ticker: str = args["ticker"].upper()
    try:
        ov_json, pb_json = await asyncio.gather(
            _vnstock_subprocess("company_overview", {"ticker": ticker}),
            _vnstock_subprocess("price_board",      {"tickers": [ticker]}),
        )

        ov_rows = json.loads(ov_json)
        ov = ov_rows[0] if ov_rows and isinstance(ov_rows, list) else {}

        def _f(v, d=0.0):
            try: return float(v) if v is not None else d
            except (TypeError, ValueError): return d

        lines = [
            f"## Foreign Flow — {ticker}",
            "",
            "### Static / Ownership",
            "| Metric | Value |",
            "|---|---:|",
            f"| Foreign ownership | {_f(ov.get('foreigner_percentage')) * 100:.2f}% |",
            f"| Outstanding shares (B) | {_f(ov.get('outstanding_share')):,.0f}" + (" |" if ov else " |"),
        ]

        pb_rows = json.loads(pb_json) if pb_json else []
        if isinstance(pb_rows, list) and pb_rows:
            r = pb_rows[0]
            # Column names from vnstock Trading().price_board() are tuple-flattened — try common variants
            def pick(*keys):
                for k in keys:
                    if k in r and r[k] not in (None, ""):
                        return r[k]
                return None

            fb = pick("match.foreign_buy_volume_total", "foreign_buy_volume_total", "match.foreign_buy_volume", "foreign_buy_volume")
            fs = pick("match.foreign_sell_volume_total", "foreign_sell_volume_total", "match.foreign_sell_volume", "foreign_sell_volume")
            room = pick("match.foreign_room_remaining", "foreign_room_remaining", "listing.foreign_room", "foreign_room")
            px = pick("match.match_price", "match_price", "last_price")

            lines += [
                "",
                "### Today's Foreign Activity (latest snapshot)",
                "| Metric | Value |",
                "|---|---:|",
                f"| Last match price | {_f(px) * 1000:,.0f} VND |" if px else "| Last match price | — |",
                f"| Foreign buy volume | {_f(fb):,.0f} shares |" if fb is not None else "| Foreign buy volume | — |",
                f"| Foreign sell volume | {_f(fs):,.0f} shares |" if fs is not None else "| Foreign sell volume | — |",
            ]

            if fb is not None and fs is not None:
                net = _f(fb) - _f(fs)
                direction = "🟢 NET BUY" if net > 0 else "🔴 NET SELL" if net < 0 else "⚪ FLAT"
                lines.append(f"| **Net foreign flow** | **{net:+,.0f} shares — {direction}** |")
                if px:
                    value_vnd = net * _f(px) * 1000
                    lines.append(f"| Net flow value | {value_vnd/1e9:+,.2f}B VND |")

            if room is not None:
                lines.append(f"| Foreign room remaining | {_f(room):,.0f} shares |")
        else:
            err = pb_rows.get("error") if isinstance(pb_rows, dict) else None
            lines += [
                "",
                "### Today's Foreign Activity",
                f"*Price board snapshot unavailable — {err or 'market may be closed (live data 09:00–15:00 VNT) or vnstock endpoint changed'}.*",
            ]

        lines += [
            "",
            "### Interpretation",
            "- Foreign ownership near the 49% cap (or 30% for banking) limits future demand — check `foreign room remaining`",
            "- Sustained foreign net buying in large-caps often precedes price moves by 1-4 weeks",
            "- Foreign net selling combined with VND weakness signals FX-driven outflow (different from fundamental selling)",
            "- Snapshot view only — for historical foreign flow time-series, check cafef.vn or fireant.vn",
        ]
        text = "\n".join(lines)
    except Exception as e:
        text = f"Foreign flow failed for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


_WB_INDICATORS = {
    "GDP growth (%)":         "NY.GDP.MKTP.KD.ZG",
    "CPI inflation (%)":      "FP.CPI.TOTL.ZG",
    "Real interest rate (%)": "FR.INR.RINR",
    "Unemployment (%)":       "SL.UEM.TOTL.ZS",
    "Current account (% GDP)":"BN.CAB.XOKA.GD.ZS",
}


async def _fetch_wb_indicator(client: httpx.AsyncClient, label: str, code: str) -> tuple[str, list[tuple[int, float]]]:
    url = f"https://api.worldbank.org/v2/country/VNM/indicator/{code}?format=json&date=2015:2026&per_page=30"
    try:
        resp = await client.get(url, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return label, []

    if not isinstance(payload, list) or len(payload) < 2:
        return label, []
    series = []
    for entry in payload[1]:
        year = entry.get("date")
        val  = entry.get("value")
        if year and val is not None:
            try:
                series.append((int(year), float(val)))
            except (TypeError, ValueError):
                continue
    return label, sorted(series, key=lambda x: x[0], reverse=True)


async def _get_vn_macro_indicators(_args: dict) -> list[types.TextContent]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*[
            _fetch_wb_indicator(client, label, code) for label, code in _WB_INDICATORS.items()
        ])

    series_map: dict[str, list] = {label: series for label, series in results}

    all_years = sorted({y for s in series_map.values() for y, _ in s}, reverse=True)[:6]

    if not all_years:
        return [types.TextContent(type="text", text="Failed to fetch any World Bank indicators for Vietnam.")]

    lines = [
        "## Vietnam Macro Indicators",
        "*Source: World Bank Open Data API (annual, lagged 1–2 years)*\n",
        "| Indicator | " + " | ".join(str(y) for y in all_years) + " |",
        "|---" + "|---:" * len(all_years) + "|",
    ]
    for label in _WB_INDICATORS:
        series = dict(series_map.get(label, []))
        row = f"| {label} |"
        for yr in all_years:
            v = series.get(yr)
            row += f" {v:+.2f} |" if v is not None else " — |"
        lines.append(row)

    lines += [
        "",
        "### Context",
        "- **GDP growth**: VN long-term trend ~6-7%; <5% = recession concern, >7% = overheating risk",
        "- **CPI inflation**: SBV target 4%. >5% pressures bond yields and bank margins; <2% signals weak demand",
        "- **Real interest rate**: nominal rate − inflation. Negative = financial repression (savers lose)",
        "- **Current account**: positive = export-driven economy (current VN profile); deterioration warns of FX pressure",
        "",
        "*For real-time SBV base rate, see sbv.gov.vn → Lãi suất → Lãi suất điều hành.*",
        "*For monthly CPI, see gso.gov.vn → Statistical data → Price index.*",
    ]
    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_quality_score(args: dict) -> list[types.TextContent]:
    ticker: str = args["ticker"].upper()
    try:
        metrics = await _fetch_metrics_for_ticker(ticker, "year")

        if "finance_error" in metrics and "overview_error" in metrics:
            return [types.TextContent(type="text", text=f"Quality score failed: {metrics.get('finance_error')}")]

        roic     = metrics.get("roic_pct")
        de_ratio = metrics.get("de_ratio")
        roe      = metrics.get("roe_pct")
        gross_m  = metrics.get("gross_margin_pct")
        op_m     = metrics.get("op_margin_pct")
        fcf      = metrics.get("fcf_b")
        net_sales_b = metrics.get("net_sales_b")

        # Get multi-year for CAGR + FCF/NI
        import pandas as pd
        inc_json, cf_json = await asyncio.gather(
            _vnstock_subprocess("income_statement", {"ticker": ticker, "period": "year"}),
            _vnstock_subprocess("cash_flow",        {"ticker": ticker, "period": "year"}),
        )
        inc = pd.DataFrame(json.loads(inc_json)) if inc_json.strip().startswith("[") else pd.DataFrame()
        cf  = pd.DataFrame(json.loads(cf_json))  if cf_json.strip().startswith("[")  else pd.DataFrame()

        rev_series = _get_item_series(inc, "net_sales")
        ni_series  = _get_item_series(inc, "attributable_to_parent_company")
        ocf_series = _get_item_series(cf,  "net_cash_from_operating_activities") or _get_item_series(cf, "net_cash_inflows_outflows_from_op")
        capex_series = _get_item_series(cf, "purchases_of_fixed_assets_and_other")

        years = sorted(rev_series.keys(), reverse=True)[:4]
        rev_cagr = None
        if len(years) >= 3 and rev_series.get(years[-1], 0) > 0:
            rev_cagr = (rev_series[years[0]] / rev_series[years[-1]]) ** (1 / (len(years) - 1)) - 1

        latest_yr = years[0] if years else None
        fcf_ni = None
        if latest_yr and ni_series.get(latest_yr, 0) > 0:
            f = ocf_series.get(latest_yr, 0) + capex_series.get(latest_yr, 0)
            fcf_ni = f / ni_series[latest_yr]

        # Score 5 dimensions (0-20 each)
        s: dict[str, int] = {}

        # ROIC: >15% great, >10% ok, <10% weak
        s["ROIC (capital efficiency)"] = 20 if roic and roic > 20 else 16 if roic and roic > 15 else 12 if roic and roic > 10 else 6 if roic and roic > 5 else 0

        # FCF/NI: cash earnings backing
        s["FCF/NI (cash quality)"] = 20 if fcf_ni and fcf_ni > 1.0 else 15 if fcf_ni and fcf_ni > 0.7 else 10 if fcf_ni and fcf_ni > 0.4 else 5 if fcf_ni and fcf_ni > 0 else 0

        # Debt/Equity: <0.3 excellent, <0.6 ok, >1.0 risky
        s["Balance sheet"] = 20 if de_ratio is not None and de_ratio < 0.3 else 16 if de_ratio is not None and de_ratio < 0.6 else 10 if de_ratio is not None and de_ratio < 1.0 else 5 if de_ratio is not None and de_ratio < 2.0 else 0

        # Revenue CAGR: >15% strong, >10% solid, >5% ok
        s["Revenue growth"] = 20 if rev_cagr is not None and rev_cagr > 0.15 else 16 if rev_cagr is not None and rev_cagr > 0.10 else 12 if rev_cagr is not None and rev_cagr > 0.05 else 6 if rev_cagr is not None and rev_cagr > 0 else 0

        # Profitability: gross margin stability (proxy via operating margin level)
        s["Profitability"] = 20 if op_m and op_m > 20 else 16 if op_m and op_m > 12 else 12 if op_m and op_m > 7 else 6 if op_m and op_m > 3 else 0

        total = sum(s.values())
        verdict = "🟢 HIGH QUALITY (compounder candidate)" if total >= 80 else "🟡 GOOD QUALITY" if total >= 60 else "🟠 AVERAGE" if total >= 40 else "🔴 LOW QUALITY (avoid or speculate only)"

        lines = [
            f"## Quality Score — {ticker}",
            f"**{total}/100 — {verdict}**",
            f"*Company: {metrics.get('name', ticker)} | Sector: {metrics.get('sector', 'N/A')}*\n",
            "### Component Scores",
            "| Dimension | Value | Score |",
            "|---|---:|---:|",
            f"| ROIC | {f'{roic:.1f}%' if roic else '—'} | {s['ROIC (capital efficiency)']}/20 |",
            f"| FCF/NI | {f'{fcf_ni:.2f}x' if fcf_ni else '—'} | {s['FCF/NI (cash quality)']}/20 |",
            f"| Debt/Equity | {f'{de_ratio:.2f}x' if de_ratio is not None else '—'} | {s['Balance sheet']}/20 |",
            f"| Revenue CAGR ({len(years)-1}Y) | {f'{rev_cagr*100:.1f}%' if rev_cagr is not None else '—'} | {s['Revenue growth']}/20 |",
            f"| Operating margin | {f'{op_m:.1f}%' if op_m else '—'} | {s['Profitability']}/20 |",
            "",
            "### Quality Thresholds",
            "- **80+**: True compounder — high ROIC + cash backing + clean balance sheet + growth",
            "- **60-79**: Solid quality — minor weaknesses, generally a buy candidate at the right price",
            "- **40-59**: Average — needs deeper investigation; could be cyclical or in transition",
            "- **<40**: Low quality — only justified at deep discounts or special situations",
        ]
        text = "\n".join(lines)
    except Exception as e:
        text = f"Quality score failed for {ticker}: {e}"

    return [types.TextContent(type="text", text=text)]


# Sector beta proxies for VN market (vs VN-Index baseline = 1.0)
_SECTOR_BETAS = {
    "real estate": 1.4, "bất động sản": 1.4,
    "banking": 1.1, "ngân hàng": 1.1, "bank": 1.1,
    "technology": 1.2, "công nghệ": 1.2, "it services": 1.2,
    "steel": 1.5, "thép": 1.5, "materials": 1.4,
    "consumer staples": 0.7, "hàng tiêu dùng thiết yếu": 0.7,
    "consumer discretionary": 1.1, "retail": 1.1,
    "utilities": 0.5, "điện": 0.6, "tiện ích": 0.5,
    "aviation": 1.6, "hàng không": 1.6,
    "oil & gas": 1.3, "dầu khí": 1.3, "energy": 1.3,
    "telecommunications": 0.9, "viễn thông": 0.9,
}


def _lookup_sector_beta(sector: str) -> float:
    s = (sector or "").lower()
    for key, beta in _SECTOR_BETAS.items():
        if key in s:
            return beta
    return 1.0


async def _stress_test_portfolio(args: dict) -> list[types.TextContent]:
    holdings: list = args["holdings"]

    if not holdings:
        return [types.TextContent(type="text", text="No holdings provided.")]

    # Fetch sector + current price for each ticker
    tickers = [h["ticker"].upper() for h in holdings]
    metrics_list = await asyncio.gather(*[_fetch_metrics_for_ticker(t, "year") for t in tickers])
    metric_by_ticker = {m["ticker"]: m for m in metrics_list}

    shocks = [-0.10, -0.20, -0.30]
    rows = []
    portfolio_value_current = 0.0
    portfolio_cost = 0.0

    for h in holdings:
        ticker = h["ticker"].upper()
        shares = float(h["shares"])
        avg_cost = float(h["avg_cost"])
        m = metric_by_ticker.get(ticker, {})
        current_price = m.get("latest_price") or m.get("current_price") or avg_cost
        sector = m.get("sector", "N/A")
        beta = _lookup_sector_beta(sector)

        position_value = current_price * shares
        cost_basis = avg_cost * shares
        portfolio_value_current += position_value
        portfolio_cost += cost_basis

        shocked_values = []
        for shock in shocks:
            position_shock = shock * beta
            shocked_price = current_price * (1 + position_shock)
            shocked_value = shocked_price * shares
            shocked_values.append(shocked_value)

        rows.append({
            "ticker":            ticker,
            "sector":            sector,
            "beta":              beta,
            "shares":            shares,
            "avg_cost":          avg_cost,
            "current_price":     current_price,
            "position_value":    position_value,
            "shocked_values":    shocked_values,
        })

    lines = [
        "## Portfolio Stress Test",
        f"*{len(holdings)} positions | Current value: {portfolio_value_current/1e6:,.1f}M VND | Cost basis: {portfolio_cost/1e6:,.1f}M VND*",
        "",
        "### Position-Level Impact",
        "| Ticker | Sector | β | Shares | Current Value | -10% Scenario | -20% Scenario | -30% Scenario |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        sector_short = (r["sector"][:18] + "…") if len(r["sector"]) > 18 else r["sector"]
        lines.append(
            f"| {r['ticker']} | {sector_short} | {r['beta']:.1f} | {r['shares']:,.0f} | "
            f"{r['position_value']/1e6:,.1f}M | "
            f"{r['shocked_values'][0]/1e6:,.1f}M | "
            f"{r['shocked_values'][1]/1e6:,.1f}M | "
            f"{r['shocked_values'][2]/1e6:,.1f}M |"
        )

    # Portfolio totals
    totals = [sum(r["shocked_values"][i] for r in rows) for i in range(len(shocks))]
    losses = [(t - portfolio_value_current) for t in totals]
    pct_losses = [l / portfolio_value_current * 100 if portfolio_value_current else 0 for l in losses]

    lines += [
        "",
        "### Portfolio Total Impact",
        "| Scenario | Portfolio Value | Loss (VND) | Loss % | Drawdown Rule |",
        "|---|---:|---:|---:|---|",
    ]
    rule_for = lambda p: (
        "🟢 Normal monitoring" if p > -5 else
        "🟡 Review losing theses — no new buys" if p > -10 else
        "🟠 Cut positions to half size" if p > -15 else
        "🔴 Move to 50% cash" if p > -20 else
        "🛑 Stop trading entirely"
    )
    for label, total, loss, pct in zip(["-10% shock", "-20% shock", "-30% shock"], totals, losses, pct_losses):
        lines.append(f"| {label} | {total/1e6:,.1f}M | {loss/1e6:+,.1f}M | {pct:+.1f}% | {rule_for(pct)} |")

    # Concentration warnings
    max_pos = max((r["position_value"] / portfolio_value_current * 100) for r in rows) if portfolio_value_current else 0
    sector_exposure: dict[str, float] = {}
    for r in rows:
        sector_exposure[r["sector"]] = sector_exposure.get(r["sector"], 0) + r["position_value"] / portfolio_value_current * 100
    max_sector = max(sector_exposure.items(), key=lambda x: x[1]) if sector_exposure else ("N/A", 0)

    lines += [
        "",
        "### Concentration Risk",
        f"- Largest single position: **{max_pos:.1f}%** of portfolio (limit: 20%)",
        f"- Largest sector exposure: **{max_sector[0]} at {max_sector[1]:.1f}%** (limit: 35%)",
    ]
    if max_pos > 20:
        lines.append("- ⚠️  **Single-position cap exceeded** — consider trimming")
    if max_sector[1] > 35:
        lines.append(f"- ⚠️  **Sector cap exceeded** — {max_sector[0]} exposure should be reduced")

    lines += [
        "",
        "*Sector betas are heuristic proxies. Actual VN beta varies — banks 0.9–1.3, real estate 1.2–1.7, staples 0.5–0.8.*",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


def _load_watchlist() -> list[str]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        return json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _save_watchlist(tickers: list[str]) -> None:
    WATCHLIST_PATH.write_text(json.dumps(sorted(set(tickers)), indent=2), encoding="utf-8")


async def _manage_watchlist(args: dict) -> list[types.TextContent]:
    action: str = args["action"].lower()
    ticker: str = args.get("ticker", "").upper().strip()

    current = _load_watchlist()

    if action == "list":
        if not current:
            return [types.TextContent(type="text", text="Watchlist is empty. Add tickers with `manage_watchlist(action='add', ticker='FPT')`.")]
        return [types.TextContent(
            type="text",
            text=f"## Watchlist ({len(current)} tickers)\n\n" + ", ".join(current),
        )]

    if action == "clear":
        _save_watchlist([])
        return [types.TextContent(type="text", text="Watchlist cleared.")]

    if not ticker:
        return [types.TextContent(type="text", text="`ticker` is required for add/remove actions.")]

    if action == "add":
        if ticker in current:
            return [types.TextContent(type="text", text=f"{ticker} already in watchlist ({len(current)} total).")]
        current.append(ticker)
        _save_watchlist(current)
        return [types.TextContent(type="text", text=f"Added {ticker}. Watchlist now {len(current)} tickers.")]

    if action == "remove":
        if ticker not in current:
            return [types.TextContent(type="text", text=f"{ticker} not in watchlist.")]
        current.remove(ticker)
        _save_watchlist(current)
        return [types.TextContent(type="text", text=f"Removed {ticker}. Watchlist now {len(current)} tickers.")]

    return [types.TextContent(type="text", text=f"Unknown action: {action}")]


async def _scan_ticker_for_triggers(ticker: str) -> dict:
    """Return dict with current state and any triggered alerts for one ticker."""
    try:
        import pandas as pd
        import pandas_ta as ta
        raw = await _vnstock_subprocess("quote_history_full", {"ticker": ticker, "days": 100})
        rows = json.loads(raw)
        if not rows or isinstance(rows, dict) or len(rows) < 50:
            return {"ticker": ticker, "error": "insufficient price history"}

        df = pd.DataFrame(rows)
        df["close"] = df["close"].astype(float) * 1000
        df["high"]  = df["high"].astype(float)  * 1000
        df["low"]   = df["low"].astype(float)   * 1000
        df = df.sort_values("time").reset_index(drop=True)

        price = float(df["close"].iloc[-1])
        prev  = float(df["close"].iloc[-2])
        daily_pct = (price - prev) / prev * 100 if prev else 0

        ma50      = float(df["close"].rolling(50).mean().iloc[-1])
        ma50_prev = float(df["close"].rolling(50).mean().iloc[-2])
        rsi_s     = ta.rsi(df["close"], length=14)
        rsi       = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.empty else None

        triggers = []
        if rsi is not None and rsi < 30:
            triggers.append(f"🟢 RSI oversold ({rsi:.1f})")
        if rsi is not None and rsi > 70:
            triggers.append(f"🔴 RSI overbought ({rsi:.1f})")
        if price > ma50 and prev <= ma50_prev:
            triggers.append(f"🟢 Broke ABOVE MA50 ({ma50:,.0f})")
        if price < ma50 and prev >= ma50_prev:
            triggers.append(f"🔴 Broke BELOW MA50 ({ma50:,.0f})")
        if abs(daily_pct) >= 5:
            arrow = "🟢" if daily_pct > 0 else "🔴"
            triggers.append(f"{arrow} {daily_pct:+.1f}% daily move")

        return {
            "ticker":    ticker,
            "price":     price,
            "daily_pct": daily_pct,
            "rsi":       rsi,
            "ma50":      ma50,
            "triggers":  triggers,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


async def _check_watchlist(_args: dict) -> list[types.TextContent]:
    watchlist = _load_watchlist()
    if not watchlist:
        return [types.TextContent(type="text", text="Watchlist is empty. Add tickers with `manage_watchlist(action='add', ticker='FPT')`.")]

    scans = await asyncio.gather(*[_scan_ticker_for_triggers(t) for t in watchlist])

    triggered = [s for s in scans if s.get("triggers")]
    errors    = [s for s in scans if "error" in s]
    quiet     = [s for s in scans if not s.get("triggers") and "error" not in s]

    from datetime import datetime, timezone, timedelta
    vn_tz = timezone(timedelta(hours=7))
    now_str = datetime.now(vn_tz).strftime("%Y-%m-%d %H:%M VNT")

    lines = [
        f"## Watchlist Scan — {now_str}",
        f"*{len(watchlist)} tickers scanned | {len(triggered)} triggered | {len(quiet)} quiet | {len(errors)} errors*\n",
    ]

    if triggered:
        lines += [
            "### 🚨 Triggered Alerts",
            "| Ticker | Price | Daily % | RSI | MA50 | Triggers |",
            "|---|---:|---:|---:|---:|---|",
        ]
        for s in triggered:
            rsi_str = f"{s['rsi']:.0f}" if s.get("rsi") is not None else "—"
            lines.append(
                f"| **{s['ticker']}** | {s['price']:,.0f} | {s['daily_pct']:+.2f}% | {rsi_str} | "
                f"{s['ma50']:,.0f} | {' • '.join(s['triggers'])} |"
            )
        lines.append("")

    if quiet:
        lines += [
            "### Quiet (no triggers)",
            ", ".join(s["ticker"] for s in quiet),
            "",
        ]

    if errors:
        lines += [
            "### Errors",
        ]
        for s in errors:
            lines.append(f"- {s['ticker']}: {s['error']}")

    return [types.TextContent(type="text", text="\n".join(lines))]


# Map company business models / sectors to keywords that match historical principle passages.
# Used by `thesis_context` to find Buffett/Marks/Damodaran wisdom relevant to the ticker's sector.
_SECTOR_PRINCIPLE_KEYWORDS: dict[str, list[str]] = {
    "Technology":              ["software", "platform", "switching cost", "network effect", "scale economy", "intellectual property"],
    "Telecommunications":      ["regulated", "infrastructure", "network", "capex intensity", "recurring revenue"],
    "Banking":                 ["bank", "credit", "net interest margin", "asset quality", "leverage", "deposit franchise"],
    "Consumer Staples":        ["brand", "pricing power", "consumer staple", "moat", "predictable"],
    "Consumer Discretionary":  ["retail", "discretionary", "consumer cycle", "brand"],
    "Real Estate":             ["real estate", "property", "land bank", "cycle", "leverage"],
    "Steel":                   ["commodity", "cyclical", "steel", "low-cost producer"],
    "Materials":               ["commodity", "cyclical", "low-cost producer"],
    "Aviation":                ["airline", "capital intensive", "competitive destruction"],
    "Industrial":              ["industrial", "capital cycle", "capex"],
    "Energy":                  ["oil", "gas", "commodity", "cycle"],
    "Utilities":               ["regulated", "utility", "stable cash flow"],
    "Healthcare":              ["healthcare", "pharma", "research"],
    "Default":                 ["quality business", "moat", "return on capital", "intrinsic value"],
}


async def _thesis_context(args: dict) -> list[types.TextContent]:
    import re
    ticker: str = args["ticker"].upper()
    lookback_days: int = int(args.get("lookback_days", 30))
    max_articles: int = int(args.get("max_articles", 15))
    include_sector: bool = bool(args.get("include_sector_principles", True))

    # Lazy import to avoid circular imports at module load
    from knowledge.lib.corpus import (
        find_passages, iter_sources, list_analyses_for_ticker, list_theses_for_ticker, read_source,
    )

    # Recent news mentioning this ticker
    recent = list(iter_sources(
        category="articles",
        tickers=[ticker],
        since_days=lookback_days,
        limit=max_articles,
    ))

    # Existing research on this ticker
    theses   = list_theses_for_ticker(ticker)
    analyses = list_analyses_for_ticker(ticker)

    # Sector lookup via company overview (cached)
    sector = ""
    try:
        ov_json = await _vnstock_subprocess("company_overview", {"ticker": ticker})
        ov_rows = json.loads(ov_json)
        if ov_rows and isinstance(ov_rows, list):
            sector = str(ov_rows[0].get("sector", "")).strip()
    except Exception:
        pass

    # Sector principles
    principles: list[dict] = []
    if include_sector_principles := include_sector:
        sector_key = sector if sector in _SECTOR_PRINCIPLE_KEYWORDS else "Default"
        keywords = _SECTOR_PRINCIPLE_KEYWORDS[sector_key]

        principle_sources = (
            list(iter_sources(category="books")) +
            list(iter_sources(category="blogs")) +
            list(iter_sources(category="papers"))
        )
        principles = find_passages(
            principle_sources, keywords,
            context_paragraphs=1, max_matches_per_source=2,
        )[:5]  # cap total

    # Detect falsification crossings — if any thesis has a stop or falsification mentioning a metric
    # that recent news contradicts, flag it. (Heuristic — we look for stop_price markdown markers.)
    falsification_flags: list[str] = []
    for t in theses:
        thesis_path = Path(t["path"])
        if thesis_path.exists():
            text = thesis_path.read_text(encoding="utf-8")
            # Naive: pull "Stop-loss" line if present
            stop_match = re.search(r"Stop-loss\s*\|\s*([\d,]+)", text)
            stop_price = float(stop_match.group(1).replace(",", "")) if stop_match else None
            if stop_price:
                # Compare to current price if available
                try:
                    from datetime import date as _date
                    hist_json = await _vnstock_subprocess(
                        "quote_history",
                        {"ticker": ticker, "start": "2026-01-01", "end": _date.today().isoformat()},
                    )
                    hist_rows = json.loads(hist_json)
                    if hist_rows and isinstance(hist_rows, list):
                        latest = float(hist_rows[-1].get("close", 0)) * 1000
                        if latest > 0 and latest < stop_price:
                            falsification_flags.append(
                                f"⚠️  {ticker} latest close ({latest:,.0f} VND) is BELOW your stop-loss "
                                f"({stop_price:,.0f} VND) in thesis {thesis_path.name}"
                            )
                except Exception:
                    pass

    # Format the briefing
    lines = [
        f"# Thesis Context — {ticker}",
        f"*Sector: {sector or 'unknown'} | Lookback: {lookback_days} days*",
        "",
    ]

    if falsification_flags:
        lines.append("## ⚠️  Falsification Flags")
        lines.append("")
        for flag in falsification_flags:
            lines.append(f"- {flag}")
        lines.append("")

    lines.append(f"## Recent News ({len(recent)} articles, last {lookback_days} days)")
    if recent:
        lines += [
            "",
            "| Date | Source | Headline |",
            "|---|---|---|",
        ]
        for s in recent:
            pub = (s.pub_date or s.ingested_at or "")[:25]
            title_md = f"[{s.title[:80]}]({s.url})" if s.url else s.title[:80]
            lines.append(f"| {pub} | {s.source_name[:25]} | {title_md} |")
    else:
        lines.append("\n*(no articles in lookback window mention this ticker)*")

    lines.append("")
    lines.append(f"## Your Existing Research")
    lines.append("")
    if theses:
        lines.append("**Saved theses:**")
        for t in theses:
            lines.append(f"- [{t['filename']}](theses/{t['filename']}) — {t['summary'][:100]}")
        lines.append("")
    if analyses:
        lines.append("**Saved analyses:**")
        for a in analyses[:5]:
            lines.append(f"- [{a['filename']}](analyses/{a['filename']}) — {a['summary'][:100]}")
        lines.append("")
    if not theses and not analyses:
        lines.append("*(no saved theses or analyses for this ticker yet)*")
        lines.append("")

    if principles:
        lines.append(f"## Sector Principles (matched on {sector or 'general'})")
        lines.append("")
        for i, p in enumerate(principles, 1):
            authors = ", ".join(p["authors"]) if p["authors"] else "?"
            lines.append(f"### {i}. {p['title']} — {authors}")
            lines.append(f"*Matched keyword: **{p['keyword']}** | Source: `{p['source_id']}`*")
            lines.append("")
            lines.append("> " + p["passage"][:800].replace("\n", "\n> "))
            lines.append("")

    if not (recent or theses or analyses or principles):
        lines.append("*(empty context — no corpus material found for this ticker)*")

    lines.append("---")
    lines.append("")
    lines.append("**Next steps in your thesis workflow:**")
    lines.append("- Read the recent news for material changes")
    lines.append("- Check whether falsification criteria from past theses still hold")
    lines.append("- Apply the sector principles above before committing your new thesis")

    return [types.TextContent(type="text", text="\n".join(lines))]


# Naive synonym map for the cross-reference engine
_TOPIC_SYNONYMS: dict[str, list[str]] = {
    "cyclicality":     ["cycle", "cyclical", "boom", "bust", "downturn"],
    "intrinsic value": ["intrinsic worth", "intrinsic business value", "fair value"],
    "moat":            ["competitive advantage", "barriers to entry", "switching cost", "pricing power"],
    "capital allocation": ["allocate capital", "buyback", "share repurchase", "dividend"],
    "earnings quality": ["accruals", "cash earnings", "owner earnings", "free cash flow"],
    "leverage":        ["debt", "financial leverage", "borrowing"],
    "growth":          ["revenue growth", "compounding", "reinvestment rate"],
    "valuation":       ["price-to-earnings", "intrinsic value", "discount rate", "DCF"],
    "risk":            ["downside", "permanent loss", "volatility", "uncertainty"],
}


async def _compare_authors_on(args: dict) -> list[types.TextContent]:
    import re
    topic: str = args["topic"]
    authors: list[str] = args["authors"]
    user_keywords: list[str] = args.get("keywords", []) or []
    context_paragraphs: int = int(args.get("context_paragraphs", 2))
    max_per_author: int = int(args.get("max_per_author", 5))

    from knowledge.lib.corpus import find_passages, iter_sources

    # Build keyword list — start with topic, layer synonyms, then user-provided
    keywords: list[str] = [topic]
    for canonical, syns in _TOPIC_SYNONYMS.items():
        if canonical.lower() in topic.lower() or topic.lower() in canonical.lower():
            keywords.extend(syns)
    keywords.extend(user_keywords)
    # Dedupe preserving order
    seen: set = set()
    unique_keywords = []
    for k in keywords:
        kl = k.lower()
        if kl not in seen:
            seen.add(kl)
            unique_keywords.append(k)

    lines = [
        f"# Cross-Reference — {topic}",
        f"*Authors compared: {', '.join(authors)}*",
        f"*Keywords searched: {unique_keywords}*",
        "",
    ]

    total_matches = 0
    for author in authors:
        # Search across all content categories that might contain author writing
        sources = []
        for cat in ("books", "blogs", "papers", "transcripts"):
            sources.extend(iter_sources(category=cat, author=author))

        passages = find_passages(
            sources, unique_keywords,
            context_paragraphs=context_paragraphs,
            max_matches_per_source=max_per_author,
        )[:max_per_author]

        lines.append(f"## {author}")
        lines.append("")
        if not passages:
            lines.append(f"*(no passages found in {author}'s corpus matching `{topic}`. Sources scanned: {len(sources)})*")
            lines.append("")
            continue

        lines.append(f"*{len(passages)} passage(s) found across {len(sources)} source file(s).*")
        lines.append("")

        for i, p in enumerate(passages, 1):
            year_match = re.search(r"\b(20\d{2}|19\d{2})\b", p.get("pub_date", "") or "")
            year = year_match.group(1) if year_match else ""
            lines.append(f"### {i}. {p['title']}{f' ({year})' if year else ''}")
            lines.append(f"*Matched keyword: **{p['keyword']}** | Source ID: `{p['source_id']}`*")
            lines.append("")
            lines.append("> " + p["passage"][:1200].replace("\n", "\n> "))
            lines.append("")

        total_matches += len(passages)

    lines.append("---")
    lines.append("")
    if total_matches == 0:
        lines.append("**No passages matched.** Try broader keywords or check that the authors are in the corpus.")
    else:
        lines.append("**Next:** synthesize where these authors agree, where they disagree, and what it means for your VN equity research.")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
