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

# ── Shared foundations moved to vn_stock package (Phase A refactor) ─────────
# server.py handlers remain here; they now import from the package. This keeps
# the MCP entry-point unchanged while making internals testable.

from vn_stock.config import (
    CACHE_DIR as _CACHE_DIR,
    CACHE_TTL as _CACHE_TTL,
    CYCLICAL_SECTORS as _CYCLICAL_SECTORS,
    DCF_UNRELIABLE_KEYS as _DCF_UNRELIABLE_KEYS,
    DEFAULT_TTL as _DEFAULT_TTL,
    DEFAULT_WEIGHTS as _DEFAULT_WEIGHTS,
    DEFENSIVE_SECTORS as _DEFENSIVE_SECTORS,
    CPI_SERIES_PATH,
    FX_HISTORY_PATH,
    M2_BANKS as _M2_BANKS,
    M2_SERIES_PATH,
    MARKET_WATCH as _MARKET_WATCH,
    PORTFOLIO_PATH,
    RATE_SERIES_PATH,
    SECTOR_BETAS as _SECTOR_BETAS,
    SECTOR_PEER_SET as _SECTOR_PEER_SET,
    SNAPSHOTS_PATH,
    VALUATION_WEIGHTS as _VALUATION_WEIGHTS,
    VN_SECTORS as _VN_SECTORS,
    WATCHLIST_PATH,
    WB_CACHE_TTL_SEC as _WB_CACHE_TTL_SEC,
    WB_INDICATORS as _WB_INDICATORS,
    WB_STALE_MAX_SEC as _WB_STALE_MAX_SEC,
)
from vn_stock.data.cache import (
    cache_get as _cache_get,
    cache_key as _cache_key,
    cache_set as _cache_set,
)
from vn_stock.data.vnstock_client import vnstock_subprocess as _vnstock_subprocess
from vn_stock.data.worldbank import (
    fetch_wb_indicator as _fetch_wb_indicator,
    wb_cache_read as _wb_cache_read,
    wb_cache_write as _wb_cache_write,
)
from vn_stock.analytics.returns import (
    annualize as _annualize,
    correlation as _correlation,
    daily_returns_from_snapshots as _daily_returns_from_snapshots,
    find_snapshot_at_or_before as _find_snapshot_at_or_before,
    parse_price_series as _parse_price_series,
    period_return_from_series,
    period_return_from_snapshots,
    rolling_drawdown as _rolling_drawdown,
    slope_normalized as _slope_normalized,
    twr as _twr,
    ytd_return as _ytd_return,
)
from vn_stock.analytics.technical import (
    detect_candle_patterns as _detect_candle_patterns,
    detect_gaps as _detect_gaps,
    detect_wyckoff_events as _detect_wyckoff_events,
    pivot_structure as _pivot_structure,
)
from vn_stock.analytics.divergence import detect_divergence as _detect_divergence
from vn_stock.tools.registry import register_tool, get_all_specs, dispatch


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
    return get_all_specs()


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent | types.ImageContent]:
    return await dispatch(name, arguments)


@register_tool(
    name='load_financial_pdf',
    description='Load a financial statement PDF from a local file path or URL. Returns each page as an image so you can visually read and analyze the financial data (income statement, balance sheet, cash flow, etc.). Use this for VN company annual reports and quarterly financial statements.',
    input_schema={'type': 'object', 'properties': {'source': {'type': 'string', 'description': 'Absolute local file path (e.g. /Users/you/fpt_2024.pdf) or HTTPS URL to the PDF.'}, 'max_pages': {'type': 'integer', 'description': 'Maximum number of pages to return (default 20, max 40).', 'default': 20}}, 'required': ['source']},
)
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


@register_tool(
    name='get_stock_overview',
    description='Get a quick overview of a Vietnam-listed stock: current price, market cap, P/E, P/B, 52-week range, and exchange (HOSE/HNX/UPCOM). Ticker examples: VIC, FPT, HPG, VNM, MWG.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}}, 'required': ['ticker']},
)
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


@register_tool(
    name='get_financial_data',
    description='Fetch structured financial statements for a VN-listed company: income statement, balance sheet, and cash flow statement. Returns multiple periods so you can spot trends.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'period': {'type': 'string', 'enum': ['year', 'quarter'], 'description': 'Annual or quarterly data (default: year).', 'default': 'year'}}, 'required': ['ticker']},
)
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


@register_tool(
    name='save_analysis',
    description="Save a completed stock analysis as a Markdown file in the project's analyses/ folder. Call this AFTER finishing the analysis to persist it as memory for future sessions.",
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'content': {'type': 'string', 'description': 'Full analysis in Markdown format.'}, 'period': {'type': 'string', 'description': "Report period label, e.g. 'Q1-2026' or '2025-annual'. Used in filename.", 'default': ''}}, 'required': ['ticker', 'content']},
)
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


@register_tool(
    name='get_technical_analysis',
    description='Compute technical analysis for a VN-listed stock using up to 1 year of daily price data. Returns trend, moving averages (MA20/50/200), RSI, MACD, Bollinger Bands, ATR, volume profile, support/resistance levels, and an overall technical signal.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'days': {'type': 'integer', 'description': 'Number of trading days of history to use (default 365).', 'default': 365}}, 'required': ['ticker']},
)
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


@register_tool(
    name='fetch_broker_news',
    description='Fetch recent news, corporate events, insider trades, and analyst consensus for a VN-listed stock. Aggregates from FiinGroup (via vnstock) which covers disclosures from SSI, TCBS, Mirae Asset, VCBS and other local brokers. Optionally load a broker research report PDF by providing its URL or local path.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'limit': {'type': 'integer', 'description': 'Number of recent news items to return (default 15).', 'default': 15}, 'broker_pdf_url': {'type': 'string', 'description': 'Optional: URL or local path to a broker research report PDF to load alongside the news.'}}, 'required': ['ticker']},
)
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


@register_tool(
    name='compare_stocks',
    description='Fetch and compare key financial metrics side-by-side for multiple VN-listed stocks. Use this to rank peers by valuation, profitability, growth, and financial health. Returns a structured comparison table ready for expert analysis.',
    input_schema={'type': 'object', 'properties': {'tickers': {'type': 'array', 'items': {'type': 'string'}, 'description': "List of VN stock tickers to compare (e.g. ['FPT', 'CMG', 'VGI']).", 'minItems': 2, 'maxItems': 8}, 'period': {'type': 'string', 'enum': ['year', 'quarter'], 'default': 'year', 'description': 'Annual or most-recent-quarter comparison.'}}, 'required': ['tickers']},
)
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


@register_tool(
    name='get_macro_data',
    description='Fetch live Vietnamese macroeconomic data: USD/VND and major currency exchange rates from Vietcombank, plus the SBV base interest rate context. Use this when analyzing currency risk, import/export companies, or macro environment.',
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
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


@register_tool(
    name='get_commodity_prices',
    description='Fetch live commodity prices relevant to Vietnam: SJC gold (miếng), BTMC gold, silver, and key precious metals in VND per lượng. Use this for gold-related stocks (PNJ, SJC), inflation analysis, or macro context.',
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
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
    ("Stockbiz EN - Company", "http://en.stockbiz.vn/RSS/News/Company.ashx"),
    ("Stockbiz EN - Market",  "http://en.stockbiz.vn/RSS/News/Market.ashx"),
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
    ("Stockbiz EN - TopStories",  "http://en.stockbiz.vn/RSS/News/TopStories.ashx"),
    ("Stockbiz EN - Economy",     "http://en.stockbiz.vn/RSS/News/Economy.ashx"),
    ("Stockbiz EN - Financial",   "http://en.stockbiz.vn/RSS/News/Financial.ashx"),
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


@register_tool(
    name='get_market_news',
    description='Crawl Vietnamese financial news sites (CafeF, Tin Nhanh Chứng Khoán, VnExpress, Vietnam Investment Review, VietStock) via RSS and return recent articles that mention the stock ticker. Complements fetch_broker_news (which pulls from vnstock/FiinGroup) with broader editorial coverage from independent news outlets. Use this to gauge media sentiment, spot breaking news, or find analyst commentary not covered by broker disclosures.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'limit': {'type': 'integer', 'description': 'Maximum number of articles to return (default 20).', 'default': 20}}, 'required': ['ticker']},
)
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


@register_tool(
    name='get_analysis_prompt',
    description="Returns a structured analysis framework to guide a deep-dive of a VN stock. Call this FIRST when the user asks to 'analyze' a stock, then use the other tools to gather data and follow the framework step by step.",
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'mode': {'type': 'string', 'enum': ['full', 'quick', 'pdf'], 'description': 'full = structured data + PDF report (default); quick = structured data only, no PDF; pdf = PDF report only.', 'default': 'full'}, 'pdf_path': {'type': 'string', 'description': 'Optional path or URL to a financial statement PDF for pdf/full modes.'}}, 'required': ['ticker']},
)
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


_HTML_TAG_RE = None
_HTML_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " ", "&hellip;": "…"}


def _clean_rss_summary(raw: str, max_chars: int = 220) -> str:
    """Strip HTML tags and entities from an RSS description, truncate cleanly."""
    global _HTML_TAG_RE
    if _HTML_TAG_RE is None:
        import re
        _HTML_TAG_RE = re.compile(r"<[^>]+>")
    if not raw:
        return ""
    text = _HTML_TAG_RE.sub(" ", raw)
    for entity, char in _HTML_ENTITIES.items():
        text = text.replace(entity, char)
    import re
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    last_space = cut.rfind(" ")
    return (cut[:last_space] if last_space > max_chars * 0.6 else cut).rstrip() + "…"


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
        summary = _clean_rss_summary(item.findtext("description", "") or "")
        if title:
            items.append({
                "source": source,
                "source_url": source_url,
                "title": title,
                "link": link,
                "date": pub_date[:22] if pub_date else "",
                "summary": summary,
            })
        if len(items) >= limit:
            break
    return items


@register_tool(
    name='get_market_overview',
    description="Show how the Vietnamese stock market is performing today. Returns VN-Index, HNX-Index, and UPCOM index levels with today's change (points and %), plus top gainers and losers from major large-cap stocks. Use this for a quick market pulse check before or after a session.",
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
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


@register_tool(
    name='get_economy_news',
    description="Fetch today's top economic and financial headlines from high-signal Vietnamese sources: VnEconomy (tạp chí Kinh tế Việt Nam), Báo Đầu tư, CafeF, Tin Nhanh Chứng Khoán, VnExpress Business, and Vietnam Investment Review. Returns a balanced feed of general market-moving news — macro policy, banking, corporate events, FDI, interest rates — not filtered by ticker. Use this for a broad economic pulse or when the user asks 'what's happening in the economy today'.",
    input_schema={'type': 'object', 'properties': {'limit': {'type': 'integer', 'description': 'Max headlines to return (default 20).', 'default': 20}}, 'required': []},
)
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


async def _get_economy_news_articles(limit: int = 50) -> list[dict]:
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

    return articles[:limit]



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


@register_tool(
    name='get_dcf_valuation',
    description='Triangulated intrinsic value for a VN-listed stock combining (1) DCF with bull/base/bear scenarios, (2) peer-relative valuation via median P/E + P/B + EV/EBITDA, and (3) a 5×5 WACC × terminal-growth sensitivity grid. Returns a blended implied price weighted by sector (e.g. banks lean relative-heavy; staples lean DCF-heavy) plus an opinionated UNDER/FAIR/OVERVALUED verdict. Defaults: 12% WACC, 5% terminal growth, default peer set per sector.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'discount_rate': {'type': 'number', 'description': 'WACC / required return in % (default 12).', 'default': 12.0}, 'terminal_growth': {'type': 'number', 'description': 'Long-term terminal growth rate in % (default 5).', 'default': 5.0}, 'bull_growth': {'type': 'number', 'description': 'Annual FCF growth in % for bull scenario (default 20).', 'default': 20.0}, 'base_growth': {'type': 'number', 'description': 'Annual FCF growth in % for base scenario (default 12).', 'default': 12.0}, 'bear_growth': {'type': 'number', 'description': 'Annual FCF growth in % for bear scenario (default 5).', 'default': 5.0}, 'projection_years': {'type': 'integer', 'description': 'Years to project FCF (default 5).', 'default': 5}, 'peers': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Optional explicit peer tickers for relative valuation. If omitted, default peer set for the sector is used.'}}, 'required': ['ticker']},
)
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


@register_tool(
    name='get_position_sizing',
    description='Calculate optimal position size for a VN stock trade using ATR-based stop-loss and fixed-fractional risk management. Returns max shares, position value, portfolio weight, stop-loss level, and risk/reward at key targets. Use this (Phase 3) before entering any new position to enforce capital discipline.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'portfolio_value': {'type': 'number', 'description': 'Total portfolio value in VND (e.g. 500000000 for 500M VND).'}, 'risk_per_trade_pct': {'type': 'number', 'description': 'Max % of portfolio to risk on this trade (default 2.0).', 'default': 2.0}, 'conviction': {'type': 'string', 'enum': ['low', 'medium', 'high'], 'description': 'Conviction level — scales risk: low 0.5x, medium 1x, high 1.5x.', 'default': 'medium'}, 'atr_multiplier': {'type': 'number', 'description': 'ATR multiplier for stop-loss distance from entry (default 2.0).', 'default': 2.0}}, 'required': ['ticker', 'portfolio_value']},
)
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


@register_tool(
    name='save_investment_thesis',
    description='Save a structured investment thesis for a VN stock to the theses/ folder. Captures the investment rationale, price targets, stop-loss, conviction level, and — critically — falsification criteria: the specific conditions that would break the thesis. Phase 4 discipline: always write the thesis before entering a position, and review it before adding to or exiting a position.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'thesis': {'type': 'string', 'description': "Core investment thesis — why you're buying, the moat, and what must remain true."}, 'buy_price': {'type': 'number', 'description': 'Entry price or range in VND.'}, 'target_price': {'type': 'number', 'description': '12-month price target in VND.'}, 'stop_price': {'type': 'number', 'description': 'Stop-loss / exit price in VND — the line where the thesis is broken.'}, 'conviction': {'type': 'string', 'enum': ['Low', 'Medium', 'High'], 'description': 'Conviction level (default Medium).', 'default': 'Medium'}, 'falsification_criteria': {'type': 'string', 'description': "Specific, testable conditions that invalidate this thesis (e.g. 'ROE drops below 15%', 'revenue growth < 10% for 2 consecutive quarters')."}, 'catalysts': {'type': 'string', 'description': '2-3 upcoming events that could prove the thesis right.', 'default': ''}, 'strongest_bias': {'type': 'string', 'description': "Pre-mortem: which cognitive bias is most likely affecting this thesis? (e.g. 'recency bias from recent rally', 'confirmation bias — I want this to work', 'anchoring on prior target').", 'default': ''}, 'premortem_reason': {'type': 'string', 'description': 'Pre-mortem: if this thesis is wrong 12 months from now, what is the SINGLE most likely reason? Be specific.', 'default': ''}}, 'required': ['ticker', 'thesis', 'buy_price', 'target_price', 'stop_price', 'falsification_criteria']},
)
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


@register_tool(
    name='save_decision_log',
    description='Log a buy/sell/add/trim/hold decision to decisions/LOG.md. Recording decisions with rationale at execution time is the foundation of Phase 4 performance review — it lets you audit your thinking vs. what actually happened. Call this every time you act on a position. Update the outcome field later when resolved.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'action': {'type': 'string', 'enum': ['BUY', 'SELL', 'ADD', 'TRIM', 'HOLD'], 'description': 'Action taken.'}, 'price': {'type': 'number', 'description': 'Execution price in VND.'}, 'rationale': {'type': 'string', 'description': "Why you're taking this action right now — cite the specific evidence."}, 'quantity': {'type': 'integer', 'description': 'Number of shares (optional).', 'default': 0}, 'outcome': {'type': 'string', 'description': "Leave blank for new entries. Fill in later: 'Correct — stock rose 25%' or 'Wrong — thesis broken at Q3 earnings'.", 'default': ''}}, 'required': ['ticker', 'action', 'price', 'rationale']},
)
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


@register_tool(
    name='review_performance',
    description="Audit your decision journal: parse decisions/LOG.md, pair buys with sells to compute realized P&L, calculate win rate / expectancy / max consecutive losses, surface stale pending decisions (>90 days), cluster losses by ticker and hold period, and output an opinionated triage verdict (e.g. 'holding losers too long', 'low hit rate'). Phase 4 performance review — call this monthly or after every 10 closed trades.",
    input_schema={'type': 'object', 'properties': {'lookback_days': {'type': 'integer', 'description': 'How many days back to include in the review (default 365).', 'default': 365}}, 'required': []},
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


def _load_portfolio() -> dict:
    if not PORTFOLIO_PATH.exists():
        return {"holdings": [], "cash_vnd": 0.0, "peak_value": 0.0, "peak_date": ""}
    try:
        data = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"holdings": [], "cash_vnd": 0.0, "peak_value": 0.0, "peak_date": ""}
    data.setdefault("holdings", [])
    data.setdefault("cash_vnd", 0.0)
    data.setdefault("peak_value", 0.0)
    data.setdefault("peak_date", "")
    return data


def _save_portfolio(portfolio: dict) -> None:
    PORTFOLIO_PATH.write_text(json.dumps(portfolio, indent=2, sort_keys=True), encoding="utf-8")


def _load_snapshots() -> list[dict]:
    if not SNAPSHOTS_PATH.exists():
        return []
    try:
        data = json.loads(SNAPSHOTS_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_snapshots(snapshots: list[dict]) -> None:
    SNAPSHOTS_PATH.write_text(json.dumps(snapshots, indent=2, sort_keys=True), encoding="utf-8")


def _append_snapshot(total_value: float, equity_value: float, cash: float) -> None:
    """Append or replace today's portfolio snapshot in .portfolio_snapshots.json."""
    from datetime import date
    today = date.today().isoformat()
    snapshots = _load_snapshots()
    snapshots = [s for s in snapshots if s.get("date") != today]
    snapshots.append({
        "date": today,
        "total_value": round(total_value, 2),
        "equity_value": round(equity_value, 2),
        "cash": round(cash, 2),
    })
    snapshots.sort(key=lambda x: x.get("date", ""))
    _save_snapshots(snapshots)


async def _fetch_holding_snapshot(ticker: str) -> dict:
    """Lightweight fetch: current price + sector for one ticker via company_overview."""
    raw = await _vnstock_subprocess("company_overview", {"ticker": ticker})
    try:
        rows = json.loads(raw)
        ov = rows[0] if isinstance(rows, list) and rows else {}
    except (json.JSONDecodeError, IndexError):
        return {"ticker": ticker, "name": ticker, "sector": "N/A", "current_price": 0.0}

    def _f(v, d=0.0):
        try: return float(v) if v is not None else d
        except (TypeError, ValueError): return d

    return {
        "ticker": ticker,
        "name": str(ov.get("organ_short_name") or ov.get("organ_name") or ticker),
        "sector": str(ov.get("sector", "N/A")),
        "current_price": _f(ov.get("current_price")),
    }


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


@register_tool(
    name='get_earnings_quality',
    description='Score earnings quality for a VN-listed stock on five dimensions: FCF/NI ratio, accruals ratio (Sloan), OCF margin, working capital trend, and OCF coverage. Returns a 0-100 quality score with verdict. Phase 2 tool — separates genuine cash earnings from accounting-driven profit. Lower accruals = higher quality.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN ticker symbol (e.g. FPT).'}}, 'required': ['ticker']},
)
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


@register_tool(
    name='get_foreign_flow',
    description="Show foreign investor activity for a VN-listed stock: current ownership %, foreign room remaining, and today's foreign buy/sell snapshot from price_board. Foreign net flow is one of the strongest leading signals on HOSE — sustained foreign accumulation in large-caps often precedes price moves.",
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN ticker symbol (e.g. FPT).'}}, 'required': ['ticker']},
)
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


@register_tool(
    name='get_vn_macro_indicators',
    description='Fetch Vietnam macroeconomic indicators from the World Bank API: GDP growth rate, CPI inflation, real interest rate, and unemployment for the last ~10 years. Phase 1 macro context — use to spot regime shifts (inflation rising, growth slowing) before they show up in stock prices.',
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
async def _get_vn_macro_indicators(_args: dict) -> list[types.TextContent]:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*[
            _fetch_wb_indicator(client, label, code) for label, code in _WB_INDICATORS.items()
        ])

    series_map: dict[str, list] = {label: series for label, series, _ in results}
    freshness_map: dict[str, str] = {label: fr for label, _, fr in results}

    all_years = sorted({y for s in series_map.values() for y, _ in s}, reverse=True)[:6]

    if not all_years:
        return [types.TextContent(
            type="text",
            text=(
                "Failed to fetch World Bank indicators — WB API appears to be temporarily unavailable "
                "(502 Bad Gateway is common when WB does maintenance). "
                "Try again in a few minutes. The Vietnam bank credit signal in `get_money_supply` "
                "does not depend on WB and remains available."
            )
        )]

    stale_labels = [l for l, f in freshness_map.items() if f == "stale"]
    unavailable_labels = [l for l, f in freshness_map.items() if f == "unavailable"]

    source_note = "*Source: World Bank Open Data API (annual, lagged 1–2 years)*"
    if stale_labels:
        source_note += f"\n> ⚠️ **{len(stale_labels)} indicator(s) served from stale cache** (WB API temporarily unavailable): {', '.join(stale_labels)}"
    if unavailable_labels:
        source_note += f"\n> ❌ **{len(unavailable_labels)} indicator(s) unavailable** (no cache): {', '.join(unavailable_labels)}"

    lines = [
        "## Vietnam Macro Indicators",
        source_note,
        "",
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


_M2_BANKS = ["VCB", "BID", "CTG", "TCB", "MBB"]


async def _fetch_bank_loan_series(ticker: str, period: str = "year") -> dict[str, float]:
    """Fetch loans_and_advances_to_customers_net for a VN bank. Returns {period_label: VND}."""
    raw = await _vnstock_subprocess("balance_sheet", {"ticker": ticker, "period": period})
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if row.get("item_id") == "loans_and_advances_to_customers_net":
            return {
                k: float(v) for k, v in row.items()
                if k not in ("item", "item_en", "item_id") and v is not None
            }
    return {}


def _load_m2_series() -> list[dict]:
    """Return user-entered monthly M2 observations sorted by date ascending."""
    if not M2_SERIES_PATH.exists():
        return []
    try:
        data = json.loads(M2_SERIES_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
    except json.JSONDecodeError:
        return []
    return sorted(data, key=lambda x: x.get("date", ""))


def _save_m2_series(rows: list[dict]) -> None:
    M2_SERIES_PATH.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")


def _m2_user_yoy(rows: list[dict]) -> tuple[float | None, str | None]:
    """Compute YoY M2 growth from user-entered series. Only requires the latest month + a matching month 12 months prior."""
    if not rows:
        return None, None
    latest = rows[-1]
    latest_date = latest.get("date", "")
    latest_val = float(latest.get("value_trillion_vnd") or latest.get("value") or 0)
    if latest_val <= 0 or len(latest_date) < 7:
        return None, latest_date or None
    try:
        y = int(latest_date[:4])
        m = int(latest_date[5:7])
        target_ym = f"{y - 1:04d}-{m:02d}"
    except ValueError:
        return None, latest_date
    for r in rows:
        if r.get("date", "").startswith(target_ym):
            prior = float(r.get("value_trillion_vnd") or r.get("value") or 0)
            if prior > 0:
                return (latest_val / prior - 1) * 100, latest_date
    return None, latest_date


@register_tool(
    name='get_money_supply',
    description="Analyze Vietnam money supply (cung tiền M2) and credit conditions. Combines World Bank annual data (broad money growth %, M2/GDP ratio) with a fresher **credit growth proxy** aggregated from top 5 VN banks' quarterly loan books (VCB, BID, CTG, TCB, MBB) — quarterly YoY loan expansion is the tactical leading indicator since SBV monthly M2 has no free public API. Returns: excess liquidity gap (M2 growth − GDP growth), historical trend, bank credit growth vs 5-year average, and monetary condition verdict (LOOSE / NEUTRAL / TIGHT) with implication for VN equity.",
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
async def _get_money_supply(_args: dict) -> list[types.TextContent]:
    # ── 1. User-entered M2 series (Option A — highest priority tactical M2) ──
    m2_user_rows = _load_m2_series()
    m2_user_yoy, m2_user_latest_date = _m2_user_yoy(m2_user_rows)

    # ── 2. Bank credit growth proxy (fresh, tactical) ────────────────────
    annual_series, quarterly_series = await asyncio.gather(
        asyncio.gather(*[_fetch_bank_loan_series(t, "year") for t in _M2_BANKS]),
        asyncio.gather(*[_fetch_bank_loan_series(t, "quarter") for t in _M2_BANKS]),
    )
    bank_annual = dict(zip(_M2_BANKS, annual_series))
    bank_quarterly = dict(zip(_M2_BANKS, quarterly_series))

    def _aggregate(banks: dict[str, dict[str, float]]) -> list[tuple[str, float]]:
        periods: set[str] = set()
        for s in banks.values():
            periods.update(s.keys())
        agg: list[tuple[str, float]] = []
        for p in sorted(periods, reverse=True):
            total = sum(s.get(p, 0) for s in banks.values() if s.get(p))
            if total > 0:
                agg.append((p, total))
        return agg

    annual_agg = _aggregate(bank_annual)
    quarterly_agg = _aggregate(bank_quarterly)

    annual_yoy: list[tuple[str, float]] = []
    for i in range(len(annual_agg) - 1):
        curr_p, curr_v = annual_agg[i]
        _, prior_v = annual_agg[i + 1]
        if prior_v > 0:
            annual_yoy.append((curr_p, (curr_v / prior_v - 1) * 100))

    quarterly_yoy: tuple[str, float] | None = None
    if len(quarterly_agg) >= 5:
        curr_p, curr_v = quarterly_agg[0]
        _, prior_v = quarterly_agg[4]
        if prior_v > 0:
            quarterly_yoy = (curr_p, (curr_v / prior_v - 1) * 100)

    latest_credit_yoy: float | None = quarterly_yoy[1] if quarterly_yoy else (annual_yoy[0][1] if annual_yoy else None)
    credit_avg_5y: float | None = None
    if annual_yoy:
        recent = annual_yoy[:5]
        credit_avg_5y = sum(g for _, g in recent) / len(recent)

    # ── 3. World Bank structural context (lagged, backdrop only) ─────────
    wb_codes = {
        "M2 growth (%)":          "FM.LBL.BMNY.ZG",
        "M2 / GDP (%)":           "FM.LBL.BMNY.GD.ZS",
        "GDP growth (%)":         "NY.GDP.MKTP.KD.ZG",
    }
    async with httpx.AsyncClient(follow_redirects=True) as client:
        wb_results = await asyncio.gather(*[
            _fetch_wb_indicator(client, label, code) for label, code in wb_codes.items()
        ])
    wb_series_map: dict[str, dict[int, float]] = {
        label: dict(series) for label, series, _ in wb_results
    }
    wb_freshness_map: dict[str, str] = {label: fr for label, _, fr in wb_results}

    m2_series = wb_series_map.get("M2 growth (%)", {})
    m2_gdp = wb_series_map.get("M2 / GDP (%)", {})
    gdp_series = wb_series_map.get("GDP growth (%)", {})

    latest_m2_year = max(m2_series.keys()) if m2_series else None
    latest_m2_wb = m2_series.get(latest_m2_year) if latest_m2_year else None
    latest_gdp = gdp_series.get(latest_m2_year) if latest_m2_year else None
    latest_m2_gdp = m2_gdp.get(latest_m2_year) if latest_m2_year else None

    if m2_series:
        m2_values = sorted(m2_series.items(), reverse=True)[:5]
        avg_5y_wb = sum(v for _, v in m2_values) / len(m2_values)
    else:
        avg_5y_wb = None

    excess_liquidity = (latest_m2_wb - latest_gdp) if latest_m2_wb and latest_gdp else None
    wb_stale = any(f in ("stale", "unavailable") for f in wb_freshness_map.values())

    # ── 4. Verdict — WEIGHT TACTICAL SIGNALS HIGHER (user M2 + bank credit) ──
    score = 0
    findings: list[str] = []

    # User-entered M2 (highest weight when available)
    if m2_user_yoy is not None:
        if m2_user_yoy > 14:
            score += 3; findings.append(f"User M2 growth {m2_user_yoy:+.2f}% YoY ({m2_user_latest_date}) — LOOSE (fresh, user-entered)")
        elif m2_user_yoy < 8:
            score -= 3; findings.append(f"User M2 growth {m2_user_yoy:+.2f}% YoY ({m2_user_latest_date}) — TIGHT (fresh, user-entered)")
        else:
            findings.append(f"User M2 growth {m2_user_yoy:+.2f}% YoY — neutral zone")

    # Bank credit growth (high weight — fresh quarterly proxy)
    if latest_credit_yoy is not None:
        if latest_credit_yoy > 16:
            score += 3; findings.append(f"Top-5 bank credit growth {latest_credit_yoy:+.1f}% YoY — aggressive expansion (leads M2 1-2 quarters)")
        elif latest_credit_yoy > 12:
            score += 1; findings.append(f"Top-5 bank credit growth {latest_credit_yoy:+.1f}% YoY — healthy expansion")
        elif latest_credit_yoy < 8:
            score -= 3; findings.append(f"Top-5 bank credit growth {latest_credit_yoy:+.1f}% YoY — weak expansion (bearish)")

    if credit_avg_5y and latest_credit_yoy is not None:
        delta = latest_credit_yoy - credit_avg_5y
        if delta > 3:
            score += 1; findings.append(f"Credit accelerating (+{delta:+.1f}pp vs 5-year avg {credit_avg_5y:.1f}%)")
        elif delta < -3:
            score -= 1; findings.append(f"Credit decelerating ({delta:+.1f}pp vs 5-year avg {credit_avg_5y:.1f}%)")

    # WB M2 — LOW weight when it's stale (2+ years old)
    if latest_m2_wb is not None and latest_m2_year and (2026 - latest_m2_year) <= 1:
        # Fresh WB data — weight normally
        if latest_m2_wb > 15:
            score += 2; findings.append(f"WB M2 growth {latest_m2_wb:.1f}% ({latest_m2_year}) — supportive structural")
        elif latest_m2_wb < 8:
            score -= 2; findings.append(f"WB M2 growth {latest_m2_wb:.1f}% ({latest_m2_year}) — restrictive structural")
    elif latest_m2_wb is not None:
        # Stale WB data — advisory only, ½ weight
        if latest_m2_wb > 15:
            score += 1; findings.append(f"WB M2 growth {latest_m2_wb:.1f}% ({latest_m2_year}) — stale, advisory only")
        elif latest_m2_wb < 8:
            score -= 1; findings.append(f"WB M2 growth {latest_m2_wb:.1f}% ({latest_m2_year}) — stale, advisory only")

    # Divergence: user M2 + bank credit disagreeing = signal
    if m2_user_yoy is not None and latest_credit_yoy is not None:
        divergence = abs(m2_user_yoy - latest_credit_yoy)
        if divergence > 6:
            if latest_credit_yoy > m2_user_yoy:
                findings.append(
                    f"⚠️ Divergence: bank credit ({latest_credit_yoy:+.1f}%) > user M2 ({m2_user_yoy:+.1f}%) "
                    f"by {divergence:.1f}pp — possible shadow banking or corporate bond stress"
                )
            else:
                findings.append(
                    f"⚠️ Divergence: user M2 ({m2_user_yoy:+.1f}%) > bank credit ({latest_credit_yoy:+.1f}%) "
                    f"by {divergence:.1f}pp — deposits growing faster than lending (banks hoarding liquidity)"
                )

    if score >= 5: verdict = "🟢 LOOSE — supportive for equity, watch for late-cycle excess"
    elif score >= 2: verdict = "🟢 MILD LOOSE — accommodative"
    elif score <= -5: verdict = "🔴 TIGHT — equity headwind, defensive positioning"
    elif score <= -2: verdict = "🟠 MILD TIGHT — cautious"
    else: verdict = "⚪ NEUTRAL"

    # ── 5. Render — tactical first, structural as backdrop ────────────────
    lines = [
        "## Vietnam Money Supply & Credit Conditions",
        f"**Verdict: {verdict}** (score {score:+d})",
        "",
        "> **Signal hierarchy** (freshest → most lagged):",
        "> 1. **User-entered monthly M2** (from TradingView / SBV / GSO — enter via `manage_m2_series`)",
        "> 2. **Top-5 bank credit growth** (vnstock quarterly — freshest institutional proxy, leads M2 by 1-2Q)",
        "> 3. **World Bank M2 annual** (structural context, typically 2-3 year lag)",
    ]

    # ── User M2 section (highest priority) ────────────────────────────────
    lines += ["", "### 1️⃣ User M2 Series (fresh monthly, if available)"]
    if m2_user_rows:
        lines.append(f"*{len(m2_user_rows)} observations from `.m2_series.json` — latest: {m2_user_rows[-1].get('date', '?')}*")
        lines.append("")
        lines.append("| Month | Value (T VND) | Source | Note |")
        lines.append("|---|---:|---|---|")
        for r in m2_user_rows[-6:]:
            date = r.get("date", "")
            val = r.get("value_trillion_vnd") or r.get("value", 0)
            src = (r.get("source") or "")[:30]
            note = (r.get("note") or "")[:40]
            lines.append(f"| {date} | {val:,.0f} | {src} | {note} |")
        if m2_user_yoy is not None:
            direction = "🟢 LOOSE" if m2_user_yoy > 14 else "🔴 TIGHT" if m2_user_yoy < 8 else "⚪ neutral"
            lines.append(f"\n**Latest YoY: {m2_user_yoy:+.2f}%** ({direction})")
        else:
            lines.append(f"\n*No same-month observation from 12 months ago — add a {int(m2_user_rows[-1].get('date', '2026')[:4]) - 1}-XX entry to enable YoY (have {len(m2_user_rows)} obs).*")
    else:
        lines.append(
            "*No user M2 data yet. Add monthly values from TradingView (ECONOMICS:VNM2) or SBV/GSO "
            "via `manage_m2_series(action='add', date='2026-05', value_trillion_vnd=15200)` — fresher than WB.*"
        )

    # ── Bank credit growth (main tactical signal) ─────────────────────────
    lines += [
        "",
        "### 2️⃣ Top-5 Bank Credit Growth (fresh tactical proxy)",
        f"*Aggregate `loans_and_advances_to_customers_net` for {', '.join(_M2_BANKS)}. "
        f"Credit leads M2 by 1-2 quarters in VN — this is the actionable signal.*",
        "",
    ]
    if annual_yoy:
        lines.append("**Annual (audited):**")
        lines.append("")
        lines.append("| Year | Aggregate loans (T VND) | YoY growth |")
        lines.append("|---|---:|---:|")
        annual_yoy_map = dict(annual_yoy)
        for p, total in annual_agg[:6]:
            g = annual_yoy_map.get(p)
            g_str = f"{g:+.2f}%" if g is not None else "—"
            lines.append(f"| {p} | {total/1e12:,.0f} | {g_str} |")
        if credit_avg_5y is not None:
            lines.append(f"\n*Trailing 5-year avg: {credit_avg_5y:.2f}% YoY.*")
    else:
        lines.append("*Annual bank data unavailable.*")

    if quarterly_agg:
        lines += ["", "**Quarterly (latest, unaudited):**", ""]
        lines.append("| Quarter | Aggregate loans (T VND) | YoY vs same Q last year |")
        lines.append("|---|---:|---:|")
        for i, (p, total) in enumerate(quarterly_agg[:4]):
            g_str = "—"
            if i + 4 < len(quarterly_agg):
                prior_v = quarterly_agg[i + 4][1]
                if prior_v > 0:
                    g_str = f"{(total / prior_v - 1) * 100:+.2f}%"
            lines.append(f"| {p} | {total/1e12:,.0f} | {g_str} |")
        if quarterly_yoy:
            lines.append(f"\n*Latest quarterly YoY: **{quarterly_yoy[1]:+.2f}%** ({quarterly_yoy[0]}) — freshest reading.*")
        else:
            lines.append(f"\n*Only {len(quarterly_agg)} quarters — need 5+ for quarterly YoY.*")

    # ── WB structural context (backdrop) ──────────────────────────────────
    lines += ["", "### 3️⃣ World Bank Structural Context (annual, lagged)"]
    if wb_stale:
        stale_labels = [l for l, f in wb_freshness_map.items() if f in ("stale", "unavailable")]
        lines.append(f"> ⚠️ **WB API partially unavailable** — served from cache where possible ({', '.join(stale_labels)}).")
    if latest_m2_wb is None and not m2_series:
        lines.append("*World Bank data unavailable — tactical signals above are still reliable.*")
    else:
        lines.append("| Metric | Latest | 5-yr avg |")
        lines.append("|---|---:|---:|")
        if latest_m2_wb is not None:
            avg_str = f"{avg_5y_wb:.2f}%" if avg_5y_wb else "—"
            lines.append(f"| M2 broad money growth | {latest_m2_wb:.2f}% ({latest_m2_year}) | {avg_str} |")
        if latest_m2_gdp is not None:
            lines.append(f"| M2 / GDP ratio | {latest_m2_gdp:.1f}% ({latest_m2_year}) | — |")
        if latest_gdp is not None:
            lines.append(f"| GDP growth | {latest_gdp:.2f}% ({latest_m2_year}) | — |")
        if excess_liquidity is not None:
            lines.append(f"| Excess liquidity gap (M2 − GDP) | {excess_liquidity:+.2f}pp | — |")

        if m2_series:
            lines += ["", "**M2 growth history (WB annual):**", ""]
            lines.append("| Year | M2 growth | GDP growth | Gap |")
            lines.append("|---|---:|---:|---:|")
            for yr in sorted(m2_series.keys(), reverse=True)[:6]:
                m2v = m2_series.get(yr)
                gdpv = gdp_series.get(yr)
                gap = (m2v - gdpv) if m2v is not None and gdpv is not None else None
                lines.append(
                    f"| {yr} | {m2v:.2f}% | {gdpv:.2f}% | {gap:+.2f}pp |" if gap is not None
                    else f"| {yr} | {m2v or '—'} | {gdpv or '—'} | — |"
                )

    lines += ["", "### Findings"]
    lines += [f"- {f}" for f in findings] if findings else ["- *Insufficient data for signals.*"]

    lines += [
        "",
        "### How to read",
        "- **Bank credit growth** is the primary VN monetary signal — credit expansion → M2 growth → asset prices. Watch for YoY > 14% (aggressive) or < 8% (tight).",
        "- **User M2 series** (if entered) gives monthly resolution and confirms bank credit's direction. Enter latest reading from TradingView ECONOMICS:VNM2, SBV, or GSO.",
        "- **WB M2 annual** is structural backdrop only — 2-3 year lag makes it useless for tactical decisions.",
        "- **Divergence** between user M2 and bank credit flags shadow banking or unusual bank behavior.",
        "- Combine with `get_vn_macro_indicators` (CPI, real rates) for full monetary picture.",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


@register_tool(
    name='manage_m2_series',
    description='Manage user-entered monthly M2 broad money series in `.m2_series.json`. VN monthly M2 has no free public API — user enters values from TradingView (ECONOMICS:VNM2), SBV, or GSO publications. Used by `get_money_supply` as the freshest structural signal (higher priority than lagged WB annual data). Actions: `list`, `add` (upsert by month), `remove` (by month), `clear`.',
    input_schema={'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'add', 'remove', 'clear']}, 'date': {'type': 'string', 'description': "Month in YYYY-MM (e.g. '2026-05'). Required for add/remove."}, 'value_trillion_vnd': {'type': 'number', 'description': 'M2 value in trillions VND (e.g. 15200 for 15.2 quadrillion / 15,200 trillion).'}, 'source': {'type': 'string', 'description': "Data source label (e.g. 'TradingView ECONOMICS:VNM2', 'SBV monthly stats')."}, 'note': {'type': 'string', 'description': 'Optional context (revision flag, methodology note, etc.).'}}, 'required': ['action']},
)
async def _manage_m2_series(args: dict) -> list[types.TextContent]:
    action = str(args.get("action", "")).lower().strip()
    rows = _load_m2_series()

    if action == "list":
        if not rows:
            return [types.TextContent(
                type="text",
                text=(
                    "No M2 observations yet. Add via:\n"
                    "`manage_m2_series(action='add', date='2026-05', value_trillion_vnd=15200, source='TradingView ECONOMICS:VNM2')`\n\n"
                    "Data sources:\n"
                    "- TradingView: https://vn.tradingview.com/symbols/ECONOMICS-VNM2/\n"
                    "- SBV: https://sbv.gov.vn (monthly monetary statistics)\n"
                    "- GSO: https://www.gso.gov.vn"
                )
            )]
        lines = [f"## User M2 Series ({len(rows)} observations)", "", "| Date | Value (T VND) | Source | Note |", "|---|---:|---|---|"]
        for r in rows:
            date = r.get("date", "")
            val = r.get("value_trillion_vnd") or r.get("value", 0)
            src = (r.get("source") or "")[:40]
            note = (r.get("note") or "")[:60]
            lines.append(f"| {date} | {float(val):,.0f} | {src} | {note} |")
        yoy, latest_date = _m2_user_yoy(rows)
        if yoy is not None:
            lines += ["", f"**Latest YoY growth ({latest_date}): {yoy:+.2f}%**"]
        return [types.TextContent(type="text", text="\n".join(lines))]

    if action == "clear":
        _save_m2_series([])
        return [types.TextContent(type="text", text="M2 series cleared.")]

    date = str(args.get("date", "")).strip()
    if not date or len(date) < 7:
        return [types.TextContent(type="text", text="`date` in YYYY-MM format is required.")]

    if action == "remove":
        before = len(rows)
        rows = [r for r in rows if not r.get("date", "").startswith(date[:7])]
        if len(rows) == before:
            return [types.TextContent(type="text", text=f"No observation matches month {date[:7]}.")]
        _save_m2_series(rows)
        return [types.TextContent(type="text", text=f"Removed {date[:7]}. {len(rows)} observations remaining.")]

    if action == "add":
        val = args.get("value_trillion_vnd") or args.get("value")
        if val is None:
            return [types.TextContent(type="text", text="`value_trillion_vnd` is required for add.")]
        try:
            val_f = float(val)
        except (TypeError, ValueError):
            return [types.TextContent(type="text", text=f"Invalid value: {val}")]
        entry = {
            "date": date[:7],  # normalize to YYYY-MM
            "value_trillion_vnd": val_f,
            "source": str(args.get("source", "")).strip() or "manual entry",
            "note": str(args.get("note", "")).strip(),
        }
        # Replace existing month, else append
        rows = [r for r in rows if not r.get("date", "").startswith(date[:7])]
        rows.append(entry)
        rows.sort(key=lambda x: x.get("date", ""))
        _save_m2_series(rows)
        return [types.TextContent(
            type="text",
            text=f"Added M2 for {date[:7]}: {val_f:,.0f}T VND. {len(rows)} observations total."
        )]

    return [types.TextContent(type="text", text=f"Unknown action: {action}. Use list/add/remove/clear.")]


# ─────────────────────────────────────────────────────────────────────────────
# Macro pillars: CPI + Interest rate + FX
# Mirrors the manage_m2_series pattern for indicators without a free public API.
# ─────────────────────────────────────────────────────────────────────────────

def _load_cpi_series() -> list[dict]:
    if not CPI_SERIES_PATH.exists():
        return []
    try:
        data = json.loads(CPI_SERIES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return sorted(data, key=lambda x: x.get("date", ""))


def _save_cpi_series(rows: list[dict]) -> None:
    CPI_SERIES_PATH.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")


def _cpi_momentum(rows: list[dict]) -> dict:
    """Latest CPI YoY + 3-month momentum (avg of last 3 minus avg of prior 3)."""
    if not rows:
        return {"latest": None, "latest_date": None, "trend_3m": None}
    latest = rows[-1]
    latest_val = latest.get("cpi_yoy")
    try:
        latest_val = float(latest_val) if latest_val is not None else None
    except (TypeError, ValueError):
        latest_val = None
    trend = None
    if len(rows) >= 6:
        recent = [float(r["cpi_yoy"]) for r in rows[-3:] if r.get("cpi_yoy") is not None]
        prior = [float(r["cpi_yoy"]) for r in rows[-6:-3] if r.get("cpi_yoy") is not None]
        if len(recent) == 3 and len(prior) == 3:
            trend = sum(recent) / 3 - sum(prior) / 3
    return {"latest": latest_val, "latest_date": latest.get("date"), "trend_3m": trend}


def _load_rate_series() -> list[dict]:
    if not RATE_SERIES_PATH.exists():
        return []
    try:
        data = json.loads(RATE_SERIES_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return sorted(data, key=lambda x: x.get("date", ""))


def _save_rate_series(rows: list[dict]) -> None:
    RATE_SERIES_PATH.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")


def _rate_latest(rows: list[dict]) -> dict:
    if not rows:
        return {"date": None, "refinance": None, "interbank_on": None, "deposit_12m": None, "prior_refinance": None}
    latest = rows[-1]

    def _f(key: str, source: dict) -> float | None:
        v = source.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    prior_refi = _f("refinance", rows[-2]) if len(rows) >= 2 else None
    return {
        "date": latest.get("date"),
        "refinance": _f("refinance", latest),
        "interbank_on": _f("interbank_on", latest),
        "deposit_12m": _f("deposit_12m", latest),
        "prior_refinance": prior_refi,
    }


async def _fetch_vcb_usd_vnd() -> tuple[float | None, str | None]:
    """Fetch USD/VND sell rate from Vietcombank XML. Returns (rate, timestamp) or (None, error)."""
    import xml.etree.ElementTree as ET
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as c:
            resp = await c.get(_VCB_FX_URL, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as e:
        return None, f"fetch failed: {e}"

    for node in root.findall("Exrate"):
        if node.get("CurrencyCode", "").strip() == "USD":
            sell = node.get("Sell", "").replace(",", "")
            try:
                return float(sell), root.findtext("DateTime")
            except ValueError:
                return None, "parse failed"
    return None, "USD not in feed"


def _load_fx_history() -> list[dict]:
    if not FX_HISTORY_PATH.exists():
        return []
    try:
        data = json.loads(FX_HISTORY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return sorted(data, key=lambda x: x.get("date", ""))


def _append_fx_snapshot(usd_vnd: float) -> list[dict]:
    """Append today's USD/VND snapshot (dedupe by date). Returns full history."""
    from datetime import date as _date
    rows = _load_fx_history()
    today = _date.today().isoformat()
    rows = [r for r in rows if r.get("date") != today]
    rows.append({"date": today, "usd_vnd": usd_vnd, "source": "Vietcombank sell"})
    rows.sort(key=lambda x: x.get("date", ""))
    FX_HISTORY_PATH.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
    return rows


def _fx_change_pct(rows: list[dict], lookback_days: int) -> float | None:
    """Return % change vs snapshot ~lookback_days ago (nearest older)."""
    if len(rows) < 2:
        return None
    from datetime import date as _date, timedelta
    target = (_date.today() - timedelta(days=lookback_days)).isoformat()
    latest = rows[-1]
    latest_val = latest.get("usd_vnd")
    if not latest_val:
        return None
    baseline = None
    for r in rows[:-1]:
        if r.get("date", "") <= target:
            baseline = r
    if baseline is None:
        baseline = rows[0]
    base_val = baseline.get("usd_vnd")
    if not base_val:
        return None
    return (float(latest_val) / float(base_val) - 1) * 100


@register_tool(
    name='manage_cpi_series',
    description="Manage user-entered monthly CPI (lạm phát) series in `.cpi_series.json`. Vietnam CPI has no free realtime API — enter monthly YoY figures from GSO (Tổng cục Thống kê) or TradingView `ECONOMICS:VNCPIYY`. Used by `get_macro_pillars` as the freshest inflation signal. Actions: `list`, `add` (upsert by month), `remove` (by month), `clear`.",
    input_schema={'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'add', 'remove', 'clear']}, 'date': {'type': 'string', 'description': "Month in YYYY-MM (e.g. '2026-07'). Required for add/remove."}, 'cpi_yoy': {'type': 'number', 'description': 'CPI year-over-year change in percent (e.g. 3.4 for 3.4%).'}, 'cpi_mom': {'type': 'number', 'description': 'Optional month-over-month CPI change in percent.'}, 'source': {'type': 'string', 'description': "Data source label (e.g. 'GSO monthly release', 'TradingView ECONOMICS:VNCPIYY')."}, 'note': {'type': 'string', 'description': 'Optional context.'}}, 'required': ['action']},
)
async def _manage_cpi_series(args: dict) -> list[types.TextContent]:
    action = str(args.get("action", "")).lower().strip()
    rows = _load_cpi_series()

    if action == "list":
        if not rows:
            return [types.TextContent(
                type="text",
                text=(
                    "No CPI observations yet. Add via:\n"
                    "`manage_cpi_series(action='add', date='2026-07', cpi_yoy=3.4, source='GSO')`\n\n"
                    "Data sources:\n"
                    "- GSO monthly: https://www.gso.gov.vn/tin-tuc-thong-ke/\n"
                    "- TradingView: https://vn.tradingview.com/symbols/ECONOMICS-VNCPIYY/\n"
                    "- SBV inflation monitoring: https://sbv.gov.vn"
                )
            )]
        lines = [f"## User CPI Series ({len(rows)} observations)", "", "| Date | YoY % | MoM % | Source | Note |", "|---|---:|---:|---|---|"]
        for r in rows:
            yoy = r.get("cpi_yoy")
            mom = r.get("cpi_mom")
            yoy_s = f"{float(yoy):+.2f}" if yoy is not None else "-"
            mom_s = f"{float(mom):+.2f}" if mom is not None else "-"
            src = (r.get("source") or "")[:40]
            note = (r.get("note") or "")[:60]
            lines.append(f"| {r.get('date','')} | {yoy_s} | {mom_s} | {src} | {note} |")
        mom_info = _cpi_momentum(rows)
        if mom_info["latest"] is not None:
            trend = mom_info["trend_3m"]
            trend_s = f", 3M trend {trend:+.2f}pp" if trend is not None else ""
            lines += ["", f"**Latest ({mom_info['latest_date']}): {mom_info['latest']:+.2f}% YoY{trend_s}**"]
        return [types.TextContent(type="text", text="\n".join(lines))]

    if action == "clear":
        _save_cpi_series([])
        return [types.TextContent(type="text", text="CPI series cleared.")]

    date = str(args.get("date", "")).strip()
    if not date or len(date) < 7:
        return [types.TextContent(type="text", text="`date` in YYYY-MM format is required.")]
    ym = date[:7]

    if action == "remove":
        before = len(rows)
        rows = [r for r in rows if not r.get("date", "").startswith(ym)]
        if len(rows) == before:
            return [types.TextContent(type="text", text=f"No observation matches month {ym}.")]
        _save_cpi_series(rows)
        return [types.TextContent(type="text", text=f"Removed {ym}. {len(rows)} observations remaining.")]

    if action == "add":
        yoy = args.get("cpi_yoy")
        if yoy is None:
            return [types.TextContent(type="text", text="`cpi_yoy` is required for add.")]
        try:
            yoy_f = float(yoy)
        except (TypeError, ValueError):
            return [types.TextContent(type="text", text=f"Invalid cpi_yoy: {yoy}")]
        mom = args.get("cpi_mom")
        try:
            mom_f = float(mom) if mom is not None else None
        except (TypeError, ValueError):
            mom_f = None
        entry = {
            "date": ym,
            "cpi_yoy": yoy_f,
            "cpi_mom": mom_f,
            "source": str(args.get("source", "")).strip() or "manual entry",
            "note": str(args.get("note", "")).strip(),
        }
        rows = [r for r in rows if not r.get("date", "").startswith(ym)]
        rows.append(entry)
        rows.sort(key=lambda x: x.get("date", ""))
        _save_cpi_series(rows)
        return [types.TextContent(
            type="text",
            text=f"Added CPI for {ym}: {yoy_f:+.2f}% YoY. {len(rows)} observations total."
        )]

    return [types.TextContent(type="text", text=f"Unknown action: {action}. Use list/add/remove/clear.")]


@register_tool(
    name='manage_rate_series',
    description="Manage user-entered interest rate series in `.rate_series.json` — SBV refinance rate, interbank overnight, and 12-month deposit rate. VN interest rate data has no consolidated free API — enter values from SBV (https://sbv.gov.vn), TradingView `ECONOMICS:VNINTR`, or bank websites. Used by `get_macro_pillars` alongside CPI to compute real rates and monetary stance. Actions: `list`, `add` (upsert by month), `remove` (by month), `clear`.",
    input_schema={'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'add', 'remove', 'clear']}, 'date': {'type': 'string', 'description': "Month in YYYY-MM (e.g. '2026-07'). Required for add/remove."}, 'refinance': {'type': 'number', 'description': 'SBV refinance rate in percent (e.g. 4.5 for 4.5%). Primary policy rate.'}, 'interbank_on': {'type': 'number', 'description': 'Optional overnight interbank rate (VNIBOR ON) in percent — reveals system liquidity.'}, 'deposit_12m': {'type': 'number', 'description': 'Optional average 12-month deposit rate at large banks in percent — funding cost proxy.'}, 'source': {'type': 'string', 'description': "Data source label (e.g. 'SBV', 'VCB 12M deposit board rate')."}, 'note': {'type': 'string', 'description': 'Optional context (e.g. rate cut/hike decision reference).'}}, 'required': ['action']},
)
async def _manage_rate_series(args: dict) -> list[types.TextContent]:
    action = str(args.get("action", "")).lower().strip()
    rows = _load_rate_series()

    if action == "list":
        if not rows:
            return [types.TextContent(
                type="text",
                text=(
                    "No rate observations yet. Add via:\n"
                    "`manage_rate_series(action='add', date='2026-07', refinance=4.5, interbank_on=3.2, source='SBV')`\n\n"
                    "Data sources:\n"
                    "- SBV policy rates: https://sbv.gov.vn/webcenter/portal/en/home/rm/ir\n"
                    "- TradingView refinance: https://vn.tradingview.com/symbols/ECONOMICS-VNINTR/\n"
                    "- Interbank rate (VNIBOR): SBV daily bulletin\n"
                    "- Deposit rate: VCB/BID/TCB rate boards"
                )
            )]
        lines = [f"## User Rate Series ({len(rows)} observations)", "", "| Date | Refinance % | Interbank ON % | Deposit 12M % | Source | Note |", "|---|---:|---:|---:|---|---|"]
        for r in rows:
            def _s(k: str) -> str:
                v = r.get(k)
                try:
                    return f"{float(v):.2f}" if v is not None else "-"
                except (TypeError, ValueError):
                    return "-"
            src = (r.get("source") or "")[:30]
            note = (r.get("note") or "")[:40]
            lines.append(f"| {r.get('date','')} | {_s('refinance')} | {_s('interbank_on')} | {_s('deposit_12m')} | {src} | {note} |")
        latest = _rate_latest(rows)
        if latest["refinance"] is not None:
            delta = ""
            if latest["prior_refinance"] is not None:
                d = latest["refinance"] - latest["prior_refinance"]
                delta = f" (Δ {d:+.2f}pp vs prior)"
            lines += ["", f"**Latest ({latest['date']}) refinance: {latest['refinance']:.2f}%{delta}**"]
        return [types.TextContent(type="text", text="\n".join(lines))]

    if action == "clear":
        _save_rate_series([])
        return [types.TextContent(type="text", text="Rate series cleared.")]

    date = str(args.get("date", "")).strip()
    if not date or len(date) < 7:
        return [types.TextContent(type="text", text="`date` in YYYY-MM format is required.")]
    ym = date[:7]

    if action == "remove":
        before = len(rows)
        rows = [r for r in rows if not r.get("date", "").startswith(ym)]
        if len(rows) == before:
            return [types.TextContent(type="text", text=f"No observation matches month {ym}.")]
        _save_rate_series(rows)
        return [types.TextContent(type="text", text=f"Removed {ym}. {len(rows)} observations remaining.")]

    if action == "add":
        refi = args.get("refinance")
        if refi is None:
            return [types.TextContent(type="text", text="`refinance` is required for add.")]

        def _num(v):
            if v is None:
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        refi_f = _num(refi)
        if refi_f is None:
            return [types.TextContent(type="text", text=f"Invalid refinance: {refi}")]
        entry = {
            "date": ym,
            "refinance": refi_f,
            "interbank_on": _num(args.get("interbank_on")),
            "deposit_12m": _num(args.get("deposit_12m")),
            "source": str(args.get("source", "")).strip() or "manual entry",
            "note": str(args.get("note", "")).strip(),
        }
        rows = [r for r in rows if not r.get("date", "").startswith(ym)]
        rows.append(entry)
        rows.sort(key=lambda x: x.get("date", ""))
        _save_rate_series(rows)
        return [types.TextContent(
            type="text",
            text=f"Added rate for {ym}: refinance {refi_f:.2f}%. {len(rows)} observations total."
        )]

    return [types.TextContent(type="text", text=f"Unknown action: {action}. Use list/add/remove/clear.")]


def _classify_cpi(cpi_yoy: float | None, trend_3m: float | None) -> tuple[str, str]:
    if cpi_yoy is None:
        return "UNKNOWN", "No CPI series — call `manage_cpi_series(action='add', ...)` first."
    if cpi_yoy >= 4.5:
        v = "OVERHEATING"
    elif cpi_yoy >= 4.0:
        v = "RISING"
    elif cpi_yoy >= 2.0:
        v = "BENIGN"
    else:
        v = "DISINFLATION"
    trend_note = ""
    if trend_3m is not None:
        if trend_3m >= 0.3:
            trend_note = f" (accelerating, +{trend_3m:.2f}pp over 3M)"
        elif trend_3m <= -0.3:
            trend_note = f" (cooling, {trend_3m:.2f}pp over 3M)"
        else:
            trend_note = f" (stable, {trend_3m:+.2f}pp over 3M)"
    return v, f"CPI {cpi_yoy:+.2f}% YoY{trend_note}"


def _classify_fx(change_30d: float | None, change_7d: float | None) -> tuple[str, str]:
    if change_30d is None and change_7d is None:
        return "UNKNOWN", "No FX history yet — first `get_macro_pillars` call establishes baseline."
    d30 = change_30d if change_30d is not None else 0
    if abs(d30) >= 3.0:
        v = "STRESS"
    elif abs(d30) >= 1.0:
        v = "PRESSURED"
    else:
        v = "STABLE"
    parts = []
    if change_7d is not None:
        parts.append(f"7D {change_7d:+.2f}%")
    if change_30d is not None:
        parts.append(f"30D {change_30d:+.2f}%")
    return v, "USD/VND " + ", ".join(parts)


def _classify_rate(refinance: float | None, cpi_yoy: float | None, prior_refi: float | None) -> tuple[str, str, float | None]:
    if refinance is None:
        return "UNKNOWN", "No rate series — call `manage_rate_series(action='add', ...)` first.", None
    if refinance >= 5.5:
        v = "RESTRICTIVE"
    elif refinance >= 4.0:
        v = "NEUTRAL"
    else:
        v = "ACCOMMODATIVE"
    real_rate = refinance - cpi_yoy if cpi_yoy is not None else None
    move_note = ""
    if prior_refi is not None:
        d = refinance - prior_refi
        if abs(d) >= 0.05:
            move_note = f", Δ {d:+.2f}pp vs prior"
    real_note = f", real rate {real_rate:+.2f}%" if real_rate is not None else ""
    return v, f"Refinance {refinance:.2f}%{move_note}{real_note}", real_rate


def _combine_regime(cpi_v: str, fx_v: str, rate_v: str, real_rate: float | None) -> tuple[str, str, list[str]]:
    """Classify into 4 regimes and return (name, description, positioning bullets)."""
    if "UNKNOWN" in {cpi_v, rate_v}:
        return (
            "INCOMPLETE",
            "Missing CPI or rate data — cannot classify regime.",
            ["Add CPI + rate observations via `manage_cpi_series` and `manage_rate_series`."]
        )
    hot_cpi = cpi_v in {"RISING", "OVERHEATING"}
    fx_stress = fx_v in {"PRESSURED", "STRESS"}
    tight_rate = rate_v == "RESTRICTIVE"
    loose_rate = rate_v == "ACCOMMODATIVE"
    negative_real = real_rate is not None and real_rate < 0

    if cpi_v == "OVERHEATING" and fx_stress:
        return (
            "STAGFLATION RISK",
            "CPI overheating + FX under stress — SBV pressured to hike; equity multiples compress.",
            [
                "UNDERWEIGHT growth/tech; long-duration cash flows discounted harder",
                "UNDERWEIGHT USD-debt names (HVN, aviation, power)",
                "OVERWEIGHT exporters with natural USD hedge (FPT services, DGC, TCM, VHC)",
                "Raise cash allocation; tighten stop-losses",
            ]
        )
    if hot_cpi and loose_rate and negative_real:
        return (
            "REFLATION",
            "CPI rising while rates still accommodative — negative real rates fuel risk assets, but a policy pivot is the tail risk.",
            [
                "OVERWEIGHT banks (NIM expansion), commodities, cyclicals",
                "OVERWEIGHT real estate (leveraged to loose credit)",
                "MONITOR: gap between CPI and refinance — if it widens further, SBV must act",
                "Prepare rotation plan for a rate-hike surprise",
            ]
        )
    if cpi_v == "BENIGN" and not fx_stress and (loose_rate or rate_v == "NEUTRAL"):
        return (
            "GOLDILOCKS",
            "Low inflation + stable FX + non-restrictive policy — the ideal window for equity risk-on.",
            [
                "OVERWEIGHT growth (FPT, MWG, tech), midcaps",
                "OVERWEIGHT banks (credit growth without margin squeeze)",
                "NEUTRAL commodities (no reflation tailwind)",
                "Deploy cash reserves; loosen stop-losses within risk budget",
            ]
        )
    if tight_rate and cpi_v in {"BENIGN", "DISINFLATION"}:
        return (
            "TIGHT / DISINFLATION",
            "Restrictive rates + cooling CPI — policy easing on the horizon; front-run the pivot with quality.",
            [
                "OVERWEIGHT quality compounders (durable FCF weathers high rates)",
                "OVERWEIGHT long-duration bonds; equities that benefit from rate cuts (banks, REITs)",
                "UNDERWEIGHT deep cyclicals until easing confirmed",
                "Build watchlist of oversold quality for pivot entry",
            ]
        )
    if cpi_v == "DISINFLATION" and rate_v != "RESTRICTIVE":
        return (
            "DEFLATION RISK",
            "Falling CPI with policy already loose — demand weakness, not benign disinflation.",
            [
                "OVERWEIGHT consumer staples, utilities (defensive)",
                "UNDERWEIGHT banks (NIM compression + credit losses)",
                "UNDERWEIGHT commodities, cyclicals",
                "Cash-preservation mode",
            ]
        )
    return (
        "MIXED",
        f"Signals not aligned: CPI {cpi_v}, FX {fx_v}, Rate {rate_v}. No clear regime — trade with caution.",
        [
            "Reduce position sizing until signals converge",
            "Focus on stock-specific catalysts over macro beta",
        ]
    )


@register_tool(
    name='get_macro_pillars',
    description="Unified analysis of the three VN macro pillars — **CPI (lạm phát), USD/VND (tỷ giá), and interest rates (lãi suất)** — with a regime verdict (Goldilocks / Reflation / Stagflation risk / Tight / Deflation risk) and sector positioning. Pulls: (1) user-entered CPI from `.cpi_series.json` via `manage_cpi_series`, (2) live USD/VND from Vietcombank plus 7D/30D delta from `.fx_history.json` (auto-appended on each call), (3) user-entered rates from `.rate_series.json` via `manage_rate_series`. Computes real interest rate = refinance − CPI YoY. Call at the start of any top-down analysis or when reassessing portfolio positioning.",
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
async def _get_macro_pillars(_args: dict) -> list[types.TextContent]:
    cpi_rows = _load_cpi_series()
    rate_rows = _load_rate_series()
    cpi_info = _cpi_momentum(cpi_rows)
    rate_info = _rate_latest(rate_rows)

    usd_vnd, fx_ts = await _fetch_vcb_usd_vnd()
    fx_rows: list[dict] = []
    change_7d: float | None = None
    change_30d: float | None = None
    if usd_vnd is not None:
        fx_rows = _append_fx_snapshot(usd_vnd)
        change_7d = _fx_change_pct(fx_rows, 7)
        change_30d = _fx_change_pct(fx_rows, 30)

    cpi_verdict, cpi_desc = _classify_cpi(cpi_info["latest"], cpi_info["trend_3m"])
    fx_verdict, fx_desc = _classify_fx(change_30d, change_7d)
    rate_verdict, rate_desc, real_rate = _classify_rate(
        rate_info["refinance"], cpi_info["latest"], rate_info["prior_refinance"]
    )
    regime, regime_desc, positioning = _combine_regime(cpi_verdict, fx_verdict, rate_verdict, real_rate)

    lines = [
        "# Ba trụ cột vĩ mô Việt Nam",
        "",
        "## 1️⃣ Lạm phát (CPI)",
        f"- Verdict: **{cpi_verdict}**",
        f"- {cpi_desc}",
        f"- Latest observation: {cpi_info['latest_date'] or 'none'}",
        f"- Threshold: mục tiêu Quốc hội ≤4.5% YoY",
        "",
        "## 2️⃣ Tỷ giá (USD/VND)",
        f"- Verdict: **{fx_verdict}**",
        f"- {fx_desc}",
    ]
    if usd_vnd is not None:
        lines.append(f"- Spot: {usd_vnd:,.0f} VND/USD (Vietcombank sell, {fx_ts or 'now'})")
        lines.append(f"- History: {len(fx_rows)} daily snapshots in `.fx_history.json`")
    else:
        lines.append(f"- ⚠️ Live fetch failed: {fx_ts}")

    lines += [
        "",
        "## 3️⃣ Lãi suất",
        f"- Verdict: **{rate_verdict}**",
        f"- {rate_desc}",
        f"- Latest observation: {rate_info['date'] or 'none'}",
    ]
    if rate_info["interbank_on"] is not None:
        lines.append(f"- Interbank ON: {rate_info['interbank_on']:.2f}%")
    if rate_info["deposit_12m"] is not None:
        lines.append(f"- Deposit 12M: {rate_info['deposit_12m']:.2f}%")

    lines += [
        "",
        "---",
        "",
        f"## 🎯 Chế độ vĩ mô: **{regime}**",
        "",
        regime_desc,
        "",
        "### Positioning",
    ]
    lines += [f"- {p}" for p in positioning]

    lines += [
        "",
        "---",
        "*Freshness: CPI/rates via `manage_*_series` manual entry (as fresh as user updates). USD/VND live from Vietcombank on every call.*",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


@register_tool(
    name='get_quality_score',
    description='Compute a single 0-100 quality score for a VN stock from: ROIC, FCF/NI, debt/equity, revenue CAGR, and gross margin stability. Phase 4 pattern recognition tool — use to screen for compounders quickly and rank watchlist candidates.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN ticker symbol (e.g. FPT).'}}, 'required': ['ticker']},
)
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




def _lookup_sector_beta(sector: str) -> float:
    s = (sector or "").lower()
    for key, beta in _SECTOR_BETAS.items():
        if key in s:
            return beta
    return 1.0


@register_tool(
    name='stress_test_portfolio',
    description='Simulate portfolio P&L under three market shock scenarios: -10%, -20%, -30% VN-Index decline. Applies sector beta proxies (banking 1.1, real estate 1.4, tech 1.2, staples 0.7) to each holding. Returns total loss in VND, by-position breakdown, and triggers drawdown rule warnings.',
    input_schema={'type': 'object', 'properties': {'holdings': {'type': 'array', 'items': {'type': 'object', 'properties': {'ticker': {'type': 'string'}, 'shares': {'type': 'number'}, 'avg_cost': {'type': 'number'}}, 'required': ['ticker', 'shares', 'avg_cost']}, 'description': 'List of holdings, e.g. [{"ticker":"FPT","shares":1000,"avg_cost":130000}].'}}, 'required': ['holdings']},
)
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


@register_tool(
    name='manage_watchlist',
    description='Add, remove, or list tickers in your personal watchlist (stored in .watchlist.json). Use `check_watchlist` afterwards to scan all tickers for technical triggers.',
    input_schema={'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['add', 'remove', 'list', 'clear']}, 'ticker': {'type': 'string', 'description': 'Required for add/remove. Ignored for list/clear.'}}, 'required': ['action']},
)
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


@register_tool(
    name='check_watchlist',
    description="Scan every ticker in your watchlist for actionable technical triggers: RSI <30 (oversold), RSI >70 (overbought), MA50 break (above or below), and >5% daily moves. Run at the start of every session to surface what's worth attention.",
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
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


@register_tool(
    name='thesis_context',
    description='Bundle pre-thesis context for a VN ticker: recent news mentioning it, your existing analyses/theses, and matching sector principles from the knowledge base (Buffett, Marks, Damodaran). Call this FIRST when writing a new thesis or revisiting an existing position — it surfaces what you already know and what the corpus says about similar businesses, saving you 30-60 min of recall.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN ticker symbol (uppercase, e.g. FPT).'}, 'lookback_days': {'type': 'integer', 'default': 30, 'description': 'How far back to pull news (default 30).'}, 'max_articles': {'type': 'integer', 'default': 15, 'description': 'Cap on recent articles included (default 15).'}, 'include_sector_principles': {'type': 'boolean', 'default': True, 'description': 'Include matching passages from books/blogs (Buffett, Marks, etc.).'}}, 'required': ['ticker']},
)
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


@register_tool(
    name='compare_authors_on',
    description="Cross-reference engine: for a topic and a list of authors, pull every passage from each author's corpus discussing that topic. Use this to learn where investing legends actually DISAGREE — Marks vs Buffett on cyclicality, Damodaran vs Mauboussin on growth, etc. Returns a structured markdown block with passages grouped by author and ready for synthesis.",
    input_schema={'type': 'object', 'properties': {'topic': {'type': 'string', 'description': "Topic to compare, e.g. 'cyclicality' or 'intrinsic value'."}, 'authors': {'type': 'array', 'items': {'type': 'string'}, 'description': "List of author names (substring match, e.g. ['Warren Buffett', 'Howard Marks']).", 'minItems': 1}, 'keywords': {'type': 'array', 'items': {'type': 'string'}, 'description': 'Optional explicit keyword list. If omitted, the topic plus naive synonyms are used.'}, 'context_paragraphs': {'type': 'integer', 'default': 2, 'description': 'Paragraphs of surrounding context per match (default 2).'}, 'max_per_author': {'type': 'integer', 'default': 5, 'description': 'Cap on passages per author (default 5).'}}, 'required': ['topic', 'authors']},
)
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


@register_tool(
    name='correlate_news_to_price',
    description="Quantify how a ticker's news flow relates to its price action. Pulls articles + daily prices over the lookback window, scores each article's sentiment (keyword-based, VN + EN), then computes cross-correlation at lags −2/−1/0/+1/+2 days between news volume + net sentiment and returns. Tells you whether news LEADS price (information edge), REACTS to price (noise), or is uncorrelated (irrelevant). Surfaces spike days with headlines.",
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN ticker symbol (uppercase, e.g. FPT).'}, 'lookback_days': {'type': 'integer', 'default': 90, 'description': 'Trading days to analyze (default 90; min 30 for statistical signal).'}}, 'required': ['ticker']},
)
async def _correlate_news_to_price(args: dict) -> list[types.TextContent]:
    """K-NPC: news ↔ price correlation analysis with sentiment + lag cross-correlation."""
    ticker: str = args["ticker"].upper()
    lookback_days: int = int(args.get("lookback_days", 90))

    try:
        import pandas as pd
        from datetime import date, timedelta
        from scipy.stats import pearsonr

        from knowledge.lib.corpus import iter_sources, _parse_loose_date
        from knowledge.lib.sentiment import score_article, label_sentiment

        # ── 1. Pull articles for this ticker over the window ─────────────────
        articles = list(iter_sources(
            category="articles",
            tickers=[ticker],
            since_days=lookback_days,
        ))

        if len(articles) < 5:
            return [types.TextContent(type="text", text=(
                f"## News-Price Correlation — {ticker} ({lookback_days} days)\n\n"
                f"**Only {len(articles)} article(s) found mentioning {ticker} in the lookback window.**\n\n"
                f"Need ≥15 articles for meaningful correlation. Suggestions:\n"
                f"- Extend lookback_days (try 180)\n"
                f"- Run `ingest_rss` to refresh the corpus\n"
                f"- This ticker may not have material coverage in our VN news sources"
            ))]

        # Score sentiment — prefer cached LLM score in frontmatter, else keyword
        article_data: list[dict] = []
        llm_scored = 0
        for s in articles:
            text = (s.title or "") + " " + (s.body[:600] if s.body else "")
            sent = score_article(s, text)
            if sent.get("source") == "llm":
                llm_scored += 1
            dt = _parse_loose_date(s.pub_date) or _parse_loose_date(s.ingested_at)
            if dt is None:
                continue
            article_data.append({
                "date":      dt.date(),
                "title":     s.title,
                "url":       s.url,
                "source":    s.source_name,
                "sentiment": sent["score"],
            })

        if not article_data:
            return [types.TextContent(type="text", text=f"No parseable article dates for {ticker}.")]

        df_news = pd.DataFrame(article_data)

        # ── 2. Daily aggregation: per-trading-day article count + mean sentiment
        daily_news = df_news.groupby("date").agg(
            n_articles=("title", "count"),
            mean_sentiment=("sentiment", "mean"),
        ).reset_index()

        # ── 3. Pull daily prices over a slightly extended window for lag analysis
        raw_prices = await _vnstock_subprocess("quote_history_full",
                                                {"ticker": ticker, "days": lookback_days + 10})
        price_rows = json.loads(raw_prices)
        if not price_rows or isinstance(price_rows, dict):
            return [types.TextContent(type="text", text=f"No price data available for {ticker}.")]

        df_price = pd.DataFrame(price_rows)
        df_price["close"] = df_price["close"].astype(float) * 1000
        df_price["date"]  = pd.to_datetime(df_price["time"]).dt.date
        df_price = df_price.sort_values("date").reset_index(drop=True)
        df_price["return"]     = df_price["close"].pct_change() * 100
        df_price["abs_return"] = df_price["return"].abs()

        # ── 4. Merge on trading dates, fill non-news days with 0 articles
        merged = df_price.merge(daily_news, on="date", how="left")
        merged["n_articles"]      = merged["n_articles"].fillna(0)
        merged["mean_sentiment"]  = merged["mean_sentiment"].fillna(0)

        # Drop the very first row (no return) and warm-up gaps
        merged = merged.dropna(subset=["return"]).reset_index(drop=True)

        if len(merged) < 20:
            return [types.TextContent(type="text", text=(
                f"## News-Price Correlation — {ticker}\n\n"
                f"Insufficient overlap between news ({len(article_data)} articles) and "
                f"price data ({len(merged)} trading days). Try a longer lookback window."
            ))]

        # ── 5. Cross-correlation at lags
        def safe_pearson(x: list, y: list) -> tuple[float, float]:
            if len(x) < 10:
                return 0.0, 1.0
            try:
                r, p = pearsonr(x, y)
                return float(r), float(p)
            except Exception:
                return 0.0, 1.0

        # Build lag table: shift news by -2..+2 days against returns
        # Lag = -k means news leads price by k days (i.e. today's news vs k-day-forward return)
        # Lag = +k means news lags price by k days (i.e. today's news vs k-day-prior return)
        # We compute by shifting RETURNS instead, which is equivalent and easier to reason about.
        lag_table: list[dict] = []
        for lag in (-2, -1, 0, 1, 2):
            # When lag = -2: news on day t correlated with return on day t+2 (news LEADS by 2 days)
            shifted_return = merged["return"].shift(-lag)  # type: ignore
            valid_mask = ~shifted_return.isna()

            x_news  = merged.loc[valid_mask, "n_articles"].tolist()
            y_ret   = shifted_return[valid_mask].tolist()
            r_vol, p_vol = safe_pearson(x_news, y_ret)

            x_sent  = merged.loc[valid_mask, "mean_sentiment"].tolist()
            r_sent, p_sent = safe_pearson(x_sent, y_ret)

            lag_table.append({
                "lag":       lag,
                "r_volume":  r_vol,
                "p_volume":  p_vol,
                "r_sent":    r_sent,
                "p_sent":    p_sent,
                "n":         len(x_news),
            })

        # Determine lead/lag verdict
        r_lead  = next(r["r_volume"] for r in lag_table if r["lag"] == -1)
        r_react = next(r["r_volume"] for r in lag_table if r["lag"] == +1)
        r_same  = next(r["r_volume"] for r in lag_table if r["lag"] == 0)

        if abs(r_lead) > abs(r_react) and abs(r_lead) > 0.15:
            verdict = "🟢 NEWS LEADS PRICE — possible information edge. Reading these headlines early may help."
        elif abs(r_react) > abs(r_lead) and abs(r_react) > 0.15:
            verdict = "🔴 NEWS REACTS TO PRICE — headlines describe yesterday's moves. Don't over-trade on them."
        elif abs(r_same) > 0.25:
            verdict = "🟡 NEWS AND PRICE MOVE TOGETHER — contemporaneous, ambiguous causation."
        else:
            verdict = "⚪ NO CLEAR RELATIONSHIP — news for this ticker is uncorrelated with returns. Treat as noise."

        # ── 6. Spike days: highest news volume
        spike_days = merged.nlargest(5, "n_articles").copy()
        spike_days = spike_days[spike_days["n_articles"] > 0]

        # Date-keyed lookup of titles for spike days
        title_by_date: dict = {}
        for d, group in df_news.groupby("date"):
            title_by_date[d] = group.iloc[0]["title"]  # show first title; could show all

        # ── 7. Recent sentiment summary
        recent_30 = df_news[df_news["date"] >= (date.today() - timedelta(days=30))]
        net_sentiment_30 = float(recent_30["sentiment"].mean()) if not recent_30.empty else 0.0
        net_sentiment_all = float(df_news["sentiment"].mean()) if not df_news.empty else 0.0

        # ── 8. Format markdown ──────────────────────────────────────────────
        lines = [
            f"## News-Price Correlation — {ticker} ({lookback_days} days)",
            "",
            "### Coverage",
            f"- **{len(article_data)} articles** mentioning {ticker} in the lookback window",
            f"- Sentiment source: **{llm_scored} LLM-scored** / {len(articles) - llm_scored} keyword "
            f"({llm_scored / len(articles) * 100:.0f}% LLM coverage) "
            f"{'✅' if llm_scored / len(articles) >= 0.8 else '⚠️ run score_sentiment_llm for better accuracy'}",
            f"- Days with ≥1 article: {(merged['n_articles'] > 0).sum()} / {len(merged)} trading days "
            f"({(merged['n_articles'] > 0).mean() * 100:.0f}%)",
            f"- Max articles on a single day: {int(merged['n_articles'].max())}",
            "",
            "### Cross-correlation: news volume → returns",
            "*Lag −k = news *leads* price by k days (information edge). "
            "Lag +k = news *reacts* to price (noise).*",
            "",
            "| Lag | Pearson r | p-value | n | Reading |",
            "|---:|---:|---:|---:|---|",
        ]
        for row in lag_table:
            sig = "***" if row["p_volume"] < 0.01 else ("**" if row["p_volume"] < 0.05 else ("*" if row["p_volume"] < 0.10 else ""))
            label_map = {-2: "news leads 2d", -1: "news leads 1d", 0: "contemporaneous",
                         1: "news lags 1d (reacts)", 2: "news lags 2d (reacts)"}
            lines.append(f"| {row['lag']:+d} | {row['r_volume']:+.3f}{sig} | {row['p_volume']:.3f} | {row['n']} | {label_map[row['lag']]} |")

        lines += [
            "",
            f"**Verdict: {verdict}**",
            "",
            "*Significance: \\*\\*\\* p<0.01, \\*\\* p<0.05, \\* p<0.10*",
            "",
            "### Cross-correlation: net sentiment → returns",
            "| Lag | Pearson r | p-value | Reading |",
            "|---:|---:|---:|---|",
        ]
        for row in lag_table:
            sig = "***" if row["p_sent"] < 0.01 else ("**" if row["p_sent"] < 0.05 else ("*" if row["p_sent"] < 0.10 else ""))
            direction = "bullish-tone-with-positive-return" if row["r_sent"] > 0 else "bullish-tone-with-negative-return (contrarian)"
            lines.append(f"| {row['lag']:+d} | {row['r_sent']:+.3f}{sig} | {row['p_sent']:.3f} | {direction if abs(row['r_sent']) > 0.10 else 'weak'} |")

        lines += [
            "",
            "### Sentiment summary",
            f"- **30-day net sentiment**: {net_sentiment_30:+.3f} ({label_sentiment(net_sentiment_30)})",
            f"- **{lookback_days}-day net sentiment**: {net_sentiment_all:+.3f} ({label_sentiment(net_sentiment_all)})",
            f"- Delta: {(net_sentiment_30 - net_sentiment_all):+.3f} "
            f"({'improving' if net_sentiment_30 > net_sentiment_all else 'deteriorating' if net_sentiment_30 < net_sentiment_all else 'stable'} vs window average)",
            "",
            "### Top news-volume days",
            "| Date | Articles | Return | Net Sent | Top headline |",
            "|---|---:|---:|---:|---|",
        ]
        for _, row in spike_days.iterrows():
            ret_str = f"{row['return']:+.2f}%" if pd.notna(row["return"]) else "—"
            sent_str = f"{row['mean_sentiment']:+.2f}"
            title = title_by_date.get(row["date"], "")[:80].replace("|", "\\|")
            lines.append(f"| {row['date']} | {int(row['n_articles'])} | {ret_str} | {sent_str} | {title} |")

        sentiment_caveat = (
            "- LLM sentiment (~95% accuracy on context + sarcasm)."
            if llm_scored / len(articles) >= 0.8
            else f"- Sentiment is {llm_scored / len(articles) * 100:.0f}% LLM + "
                 f"{(len(articles) - llm_scored) / len(articles) * 100:.0f}% keyword (~70% accuracy). "
                 "Run `python -m knowledge.pipelines.score_sentiment_llm` to backfill."
        )
        lines += [
            "",
            "### Caveats",
            sentiment_caveat,
            "- 90 trading days ≈ 90 observations. Correlations of ±0.20 are borderline significant.",
            "- Causation ≠ correlation. \"News leads price\" could be edge OR shared response to a third variable (market regime, sector flow).",
            "- For tickers with <15 articles, results are noise.",
        ]

        text = "\n".join(lines)

    except Exception as e:
        text = f"News-price correlation failed for {ticker}: {type(e).__name__}: {e}"

    return [types.TextContent(type="text", text=text)]


@register_tool(
    name='get_money_flow_price_action',
    description='Analyze money flow (dòng tiền) and price action (hành động giá) for a VN-listed stock. Combines volume-based flow indicators (MFI, OBV, CMF, A/D line, up/down-day volume ratio, climax days) with pure price-action reading (recent candlestick patterns, HH/HL trend structure, gaps, 20-day range breakouts, Wyckoff Spring/Upthrust false-breakout events, and price vs OBV/MFI divergence). Complements get_technical_analysis (which focuses on MA/RSI/MACD/BB) by surfacing whether smart money is accumulating or distributing under the surface. Returns a scored verdict: ACCUMULATION / DISTRIBUTION / NEUTRAL.',
    input_schema={'type': 'object', 'properties': {'ticker': {'type': 'string', 'description': 'VN stock ticker symbol (uppercase, e.g. FPT).'}, 'days': {'type': 'integer', 'description': 'Number of trading days of history to use (default 180).', 'default': 180}}, 'required': ['ticker']},
)
async def _get_money_flow_price_action(args: dict) -> list[types.TextContent]:
    ticker = args["ticker"].upper()
    days = int(args.get("days", 180))

    try:
        import pandas as pd
        import pandas_ta as ta

        raw = await _vnstock_subprocess("quote_history_full", {"ticker": ticker, "days": days})
        rows = json.loads(raw)
        if not rows or isinstance(rows, dict):
            return [types.TextContent(type="text", text=f"No price data for {ticker}")]

        df = pd.DataFrame(rows)
        df["close"] = df["close"].astype(float) * 1000
        df["open"] = df["open"].astype(float) * 1000
        df["high"] = df["high"].astype(float) * 1000
        df["low"] = df["low"].astype(float) * 1000
        df["volume"] = df["volume"].astype(float)
        df = df.sort_values("time").reset_index(drop=True)
        n = len(df)

        if n < 30:
            return [types.TextContent(
                type="text",
                text=f"Insufficient data for {ticker} — need at least 30 trading days, got {n}."
            )]

        close, high, low, vol = df["close"], df["high"], df["low"], df["volume"]
        price = float(close.iloc[-1])

        # ── Money Flow Indicators ─────────────────────────────────────────
        mfi_s = ta.mfi(high, low, close, vol, length=14)
        mfi = float(mfi_s.iloc[-1]) if mfi_s is not None and not mfi_s.empty else None

        obv_s = ta.obv(close, vol)
        obv_slope = _slope_normalized(obv_s.tail(20)) if obv_s is not None else 0.0

        cmf_s = ta.cmf(high, low, close, vol, length=20)
        cmf = float(cmf_s.iloc[-1]) if cmf_s is not None and not cmf_s.empty else None

        ad_s = ta.ad(high, low, close, vol)
        ad_slope = _slope_normalized(ad_s.tail(20)) if ad_s is not None else 0.0

        # ── Volume Distribution (up-day vs down-day, last 30 sessions) ────
        recent = df.tail(30).copy()
        recent["ret"] = recent["close"].pct_change()
        up_vol = float(recent.loc[recent["ret"] > 0, "volume"].sum())
        down_vol = float(recent.loc[recent["ret"] < 0, "volume"].sum())
        vol_ratio = up_vol / down_vol if down_vol > 0 else float("inf")

        vol_ma = float(vol.rolling(20).mean().iloc[-1])
        climax_mask = recent["volume"] > 2 * vol_ma
        green_climax = int(((recent["ret"] > 0) & climax_mask).sum())
        red_climax = int(((recent["ret"] < 0) & climax_mask).sum())

        # ── Price Action Patterns ─────────────────────────────────────────
        candle_patterns = _detect_candle_patterns(df)
        structure, highs, lows = _pivot_structure(df)
        gaps = _detect_gaps(df)

        # ── Range Breakouts (20-day) ──────────────────────────────────────
        prev_20_high = float(df["high"].iloc[-21:-1].max()) if n >= 21 else float(df["high"].iloc[:-1].max())
        prev_20_low = float(df["low"].iloc[-21:-1].min()) if n >= 21 else float(df["low"].iloc[:-1].min())
        breakout_up = price > prev_20_high
        breakdown = price < prev_20_low
        last_vol_ratio = float(vol.iloc[-1]) / vol_ma if vol_ma else 1.0

        # ── Divergences ───────────────────────────────────────────────────
        obv_divergence = _detect_divergence(close, obv_s, "OBV") if obv_s is not None else None
        mfi_divergence = _detect_divergence(close, mfi_s, "MFI") if mfi_s is not None else None

        # ── Wyckoff events (Spring / Upthrust) ────────────────────────────
        wyckoff_events = _detect_wyckoff_events(df, lookback=10, range_window=20)
        latest_spring = next((e for e in reversed(wyckoff_events) if e["type"] == "spring"), None)
        latest_upthrust = next((e for e in reversed(wyckoff_events) if e["type"] == "upthrust"), None)

        # ── Verdict scoring ───────────────────────────────────────────────
        score = 0
        rationale: list[str] = []
        if mfi is not None:
            if mfi < 20:
                score += 2; rationale.append(f"MFI {mfi:.0f} <20 (oversold)")
            elif mfi > 80:
                score -= 2; rationale.append(f"MFI {mfi:.0f} >80 (overbought)")
        if cmf is not None:
            if cmf > 0.1:
                score += 2; rationale.append(f"CMF {cmf:+.2f} strong inflow")
            elif cmf < -0.1:
                score -= 2; rationale.append(f"CMF {cmf:+.2f} strong outflow")
        if obv_slope > 0.5:
            score += 1; rationale.append(f"OBV rising ({obv_slope:+.1f}%/bar)")
        elif obv_slope < -0.5:
            score -= 1; rationale.append(f"OBV falling ({obv_slope:+.1f}%/bar)")
        if vol_ratio > 1.5:
            score += 1; rationale.append(f"Up-vol / down-vol = {vol_ratio:.2f}x")
        elif vol_ratio < 0.67:
            score -= 1; rationale.append(f"Down-vol dominates ({vol_ratio:.2f}x up/down)")
        if green_climax > red_climax + 1:
            score += 1; rationale.append(f"{green_climax} green climax days vs {red_climax} red")
        elif red_climax > green_climax + 1:
            score -= 1; rationale.append(f"{red_climax} red climax days vs {green_climax} green")
        if obv_divergence and "Bearish" in obv_divergence:
            score -= 2; rationale.append("Bearish OBV divergence")
        if obv_divergence and "Bullish" in obv_divergence:
            score += 2; rationale.append("Bullish OBV divergence")
        if breakout_up and last_vol_ratio > 1.5:
            score += 2; rationale.append(f"20-day breakout on {last_vol_ratio:.1f}x volume")
        elif breakdown and last_vol_ratio > 1.5:
            score -= 2; rationale.append(f"20-day breakdown on {last_vol_ratio:.1f}x volume")

        # Wyckoff Spring = failed breakdown → accumulation. Upthrust = failed breakout → distribution.
        if latest_spring:
            bonus = 3 if latest_spring["vol_ratio"] > 1.5 else 2
            score += bonus
            rationale.append(
                f"Wyckoff Spring on {latest_spring['date']} "
                f"(broke -{latest_spring['depth_pct']:.1f}% below support, reclaimed on {latest_spring['vol_ratio']:.1f}× vol)"
            )
        if latest_upthrust:
            bonus = 3 if latest_upthrust["vol_ratio"] > 1.5 else 2
            score -= bonus
            rationale.append(
                f"Wyckoff Upthrust on {latest_upthrust['date']} "
                f"(broke +{latest_upthrust['depth_pct']:.1f}% above resistance, rejected on {latest_upthrust['vol_ratio']:.1f}× vol)"
            )

        if score >= 4: verdict = "🟢 ACCUMULATION"
        elif score >= 2: verdict = "🟢 MILD ACCUMULATION"
        elif score <= -4: verdict = "🔴 DISTRIBUTION"
        elif score <= -2: verdict = "🟠 MILD DISTRIBUTION"
        else: verdict = "⚪ NEUTRAL / CHURN"

        # ── Render ────────────────────────────────────────────────────────
        lines = [
            f"## Money Flow & Price Action — {ticker}",
            f"**Verdict: {verdict}** (score: {score:+d})",
            f"*Current price: {price:,.0f} VND — based on {n} trading days.*",
            "",
            "### Money Flow Indicators",
            "| Indicator | Value | Read |",
            "|---|---:|---|",
        ]
        if mfi is not None:
            mfi_read = "🟢 oversold" if mfi < 20 else "🔴 overbought" if mfi > 80 else "neutral"
            lines.append(f"| MFI (14) | {mfi:.1f} | {mfi_read} |")
        if cmf is not None:
            cmf_read = "🟢 strong inflow" if cmf > 0.1 else "🔴 strong outflow" if cmf < -0.1 else "neutral"
            lines.append(f"| CMF (20) | {cmf:+.3f} | {cmf_read} |")
        obv_read = "🟢 rising" if obv_slope > 0.5 else "🔴 falling" if obv_slope < -0.5 else "flat"
        lines.append(f"| OBV slope (20d) | {obv_slope:+.2f}%/bar | {obv_read} |")
        ad_read = "🟢 rising" if ad_slope > 0.5 else "🔴 falling" if ad_slope < -0.5 else "flat"
        lines.append(f"| A/D line slope (20d) | {ad_slope:+.2f}%/bar | {ad_read} |")

        lines += [
            "",
            "### Volume Distribution (last 30 sessions)",
            f"- Up-day volume: **{up_vol:,.0f}** vs Down-day volume: **{down_vol:,.0f}**",
            f"- Ratio: **{vol_ratio:.2f}x** {'(bulls control tape)' if vol_ratio > 1.5 else '(bears control tape)' if vol_ratio < 0.67 else '(balanced)'}",
            f"- Volume climax days (>2× avg): **{green_climax} green** / **{red_climax} red**",
        ]

        lines += ["", "### Recent Candlestick Patterns (last 5 sessions)"]
        lines += candle_patterns if candle_patterns else ["  *No notable patterns detected.*"]

        lines += [
            "",
            "### Trend Structure (30d pivot analysis)",
            f"  {structure}",
        ]
        if highs and lows:
            lines.append(f"  Recent pivot highs: {', '.join(f'{h:,.0f}' for h in highs[-3:])}")
            lines.append(f"  Recent pivot lows:  {', '.join(f'{l:,.0f}' for l in lows[-3:])}")

        lines += ["", "### Gaps (last 20 sessions, ≥1%)"]
        lines += gaps if gaps else ["  *No significant gaps.*"]

        lines += [
            "",
            "### Range Breakouts (20-day)",
            f"  Prior 20-day high: {prev_20_high:,.0f} — current price is {(price - prev_20_high) / prev_20_high * 100:+.1f}%",
            f"  Prior 20-day low:  {prev_20_low:,.0f} — current price is {(price - prev_20_low) / prev_20_low * 100:+.1f}%",
        ]
        if breakout_up:
            lines.append(f"  🟢 **Breakout above 20-day high** on {last_vol_ratio:.1f}× avg volume")
        if breakdown:
            lines.append(f"  🔴 **Breakdown below 20-day low** on {last_vol_ratio:.1f}× avg volume")

        lines += ["", "### Wyckoff Events — Spring / Upthrust (last 10 sessions, 20-day range)"]
        if wyckoff_events:
            for e in wyckoff_events:
                if e["type"] == "spring":
                    vol_flag = " · HIGH VOL 🔥" if e["vol_ratio"] > 1.5 else ""
                    lines.append(
                        f"  🟢 **Spring** on {e['date']} — dipped -{e['depth_pct']:.2f}% below support "
                        f"({e['prev_support']:,.0f}) to {e['session_low']:,.0f}, closed back at "
                        f"{e['session_close']:,.0f} on {e['vol_ratio']:.1f}× avg vol{vol_flag}"
                    )
                else:
                    vol_flag = " · HIGH VOL 🔥" if e["vol_ratio"] > 1.5 else ""
                    lines.append(
                        f"  🔴 **Upthrust** on {e['date']} — spiked +{e['depth_pct']:.2f}% above resistance "
                        f"({e['prev_resistance']:,.0f}) to {e['session_high']:,.0f}, closed back at "
                        f"{e['session_close']:,.0f} on {e['vol_ratio']:.1f}× avg vol{vol_flag}"
                    )
            lines.append("")
            lines.append(
                "*Wyckoff interpretation: Spring = failed breakdown, "
                "institutional accumulation catching the stops. Upthrust = failed breakout, "
                "supply overwhelming demand at resistance. High volume amplifies conviction — "
                "confirm with 2-3 sessions of follow-through before acting.*"
            )
        else:
            lines.append("  *No Spring/Upthrust events detected — price respecting the trading range.*")

        divergences = [d for d in (obv_divergence, mfi_divergence) if d]
        lines += ["", "### Divergences"]
        lines += [f"  {d}" for d in divergences] if divergences else ["  *No divergences detected in last 20 sessions.*"]

        lines += [
            "",
            "### Verdict Rationale",
        ]
        lines += [f"  - {r}" for r in rationale] if rationale else ["  *No decisive signals — sideways/quiet tape.*"]

        lines += [
            "",
            "### How to read this",
            "- **Accumulation** = money flowing in quietly (rising OBV, positive CMF, up-vol > down-vol) — often precedes a markup phase",
            "- **Distribution** = money flowing out under the surface (bearish divergence, red climax days, negative CMF) — often precedes a markdown",
            "- **Divergence** is the highest-conviction signal — when price and OBV/MFI disagree, the indicator usually wins",
            "- Combine with `get_technical_analysis` (trend + momentum) and `get_foreign_flow` (foreign net buy) for a complete tape read",
        ]

        text = "\n".join(lines)

    except Exception as e:
        text = f"Money flow / price action analysis failed for {ticker}: {type(e).__name__}: {e}"

    return [types.TextContent(type="text", text=text)]


@register_tool(
    name='manage_portfolio',
    description='Persistent portfolio manager — CRUD holdings in `.portfolio.json`. Actions: `list` (show all), `add` (create or replace ticker), `remove`, `set_cash` (set VND cash balance), `clear` (wipe holdings, keep cash). Portfolio state is the foundation for `get_portfolio_overview`, `get_portfolio_risk`, and `get_rebalancing_suggestions`.',
    input_schema={'type': 'object', 'properties': {'action': {'type': 'string', 'enum': ['list', 'add', 'remove', 'set_cash', 'clear'], 'description': 'Action to perform.'}, 'ticker': {'type': 'string', 'description': 'VN ticker for add/remove.'}, 'shares': {'type': 'number', 'description': 'Shares held (for add).'}, 'avg_cost': {'type': 'number', 'description': 'Average cost per share in VND (for add).'}, 'target_weight': {'type': 'number', 'description': 'Target portfolio weight in % (0-100) for rebalancing. Optional.'}, 'cash_vnd': {'type': 'number', 'description': 'Cash balance in VND (for set_cash).'}, 'notes': {'type': 'string', 'description': 'Free-form notes (for add). Optional.'}}, 'required': ['action']},
)
async def _manage_portfolio(args: dict) -> list[types.TextContent]:
    action = str(args.get("action", "")).lower().strip()
    portfolio = _load_portfolio()

    if action == "list":
        h = portfolio["holdings"]
        cash = portfolio["cash_vnd"]
        if not h and not cash:
            return [types.TextContent(
                type="text",
                text="Portfolio is empty. Add positions with `manage_portfolio(action='add', ticker='FPT', shares=1000, avg_cost=65000)`."
            )]
        lines = [
            f"## Portfolio ({len(h)} positions, cash {cash/1e6:,.1f}M VND)",
            "",
            "| Ticker | Shares | Avg Cost | Target % | Notes |",
            "|---|---:|---:|---:|---|",
        ]
        for row in sorted(h, key=lambda x: x.get("ticker", "")):
            tgt = row.get("target_weight")
            tgt_str = f"{tgt:.1f}%" if tgt is not None else "—"
            notes = (row.get("notes") or "")[:40]
            lines.append(
                f"| {row['ticker']} | {row['shares']:,.0f} | {row['avg_cost']:,.0f} | {tgt_str} | {notes} |"
            )
        return [types.TextContent(type="text", text="\n".join(lines))]

    if action == "clear":
        portfolio["holdings"] = []
        _save_portfolio(portfolio)
        return [types.TextContent(type="text", text=f"Holdings cleared. Cash preserved: {portfolio['cash_vnd']/1e6:,.1f}M VND.")]

    if action == "set_cash":
        cash = args.get("cash_vnd")
        if cash is None:
            return [types.TextContent(type="text", text="`cash_vnd` is required for set_cash.")]
        portfolio["cash_vnd"] = float(cash)
        _save_portfolio(portfolio)
        return [types.TextContent(type="text", text=f"Cash set to {float(cash)/1e6:,.1f}M VND.")]

    ticker = str(args.get("ticker", "")).upper().strip()
    if not ticker:
        return [types.TextContent(type="text", text="`ticker` is required for add/remove.")]

    if action == "remove":
        before = len(portfolio["holdings"])
        portfolio["holdings"] = [h for h in portfolio["holdings"] if h["ticker"] != ticker]
        if len(portfolio["holdings"]) == before:
            return [types.TextContent(type="text", text=f"{ticker} not in portfolio.")]
        _save_portfolio(portfolio)
        return [types.TextContent(type="text", text=f"Removed {ticker}. Portfolio now {len(portfolio['holdings'])} positions.")]

    if action == "add":
        shares = args.get("shares")
        avg_cost = args.get("avg_cost")
        if shares is None or avg_cost is None:
            return [types.TextContent(type="text", text="`shares` and `avg_cost` are required for add.")]
        from datetime import date
        entry = {
            "ticker": ticker,
            "shares": float(shares),
            "avg_cost": float(avg_cost),
            "opened_at": date.today().isoformat(),
        }
        if args.get("target_weight") is not None:
            entry["target_weight"] = float(args["target_weight"])
        if args.get("notes"):
            entry["notes"] = str(args["notes"])
        portfolio["holdings"] = [h for h in portfolio["holdings"] if h["ticker"] != ticker]
        portfolio["holdings"].append(entry)
        _save_portfolio(portfolio)
        return [types.TextContent(
            type="text",
            text=f"Added {ticker}: {float(shares):,.0f} shares @ {float(avg_cost):,.0f} VND. Portfolio now {len(portfolio['holdings'])} positions."
        )]

    return [types.TextContent(type="text", text=f"Unknown action: {action}. Use list/add/remove/set_cash/clear.")]


async def _enrich_holdings(portfolio: dict) -> list[dict]:
    """Fetch current price + sector for each holding and combine with stored data."""
    holdings = portfolio.get("holdings", [])
    if not holdings:
        return []
    snapshots = await asyncio.gather(*[
        _fetch_holding_snapshot(h["ticker"]) for h in holdings
    ])
    snap_by_ticker = {s["ticker"]: s for s in snapshots}
    rows = []
    for h in holdings:
        snap = snap_by_ticker.get(h["ticker"], {})
        current = float(snap.get("current_price") or h["avg_cost"])
        rows.append({
            **h,
            "sector": snap.get("sector", "N/A"),
            "current_price": current,
            "market_value": current * float(h["shares"]),
            "cost_basis": float(h["avg_cost"]) * float(h["shares"]),
        })
    return rows


@register_tool(
    name='get_portfolio_overview',
    description='Show current portfolio state: total value, cost basis, unrealized P&L, per-position table (ticker, sector, weight%, market value, P&L), cash %, sector allocation, top holdings, current drawdown from peak. Fetches live prices from vnstock. Also updates the persisted peak_value when a new all-time high is reached.',
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
async def _get_portfolio_overview(_args: dict) -> list[types.TextContent]:
    portfolio = _load_portfolio()
    holdings = portfolio.get("holdings", [])
    cash = float(portfolio.get("cash_vnd", 0))

    if not holdings and cash == 0:
        return [types.TextContent(
            type="text",
            text="Portfolio is empty. Add positions with `manage_portfolio(action='add', ...)` first."
        )]

    rows = await _enrich_holdings(portfolio)
    equity_value = sum(r["market_value"] for r in rows)
    cost_basis_equity = sum(r["cost_basis"] for r in rows)
    total_value = equity_value + cash
    pnl_abs = equity_value - cost_basis_equity
    pnl_pct = pnl_abs / cost_basis_equity * 100 if cost_basis_equity else 0.0

    # Update peak if we made a new high
    from datetime import date
    peak = float(portfolio.get("peak_value", 0))
    peak_date = portfolio.get("peak_date", "")
    if total_value > peak:
        portfolio["peak_value"] = total_value
        portfolio["peak_date"] = date.today().isoformat()
        _save_portfolio(portfolio)
        peak = total_value
        peak_date = portfolio["peak_date"]
    drawdown_pct = (total_value - peak) / peak * 100 if peak else 0.0

    _append_snapshot(total_value, equity_value, cash)

    lines = [
        "## Portfolio Overview",
        f"**Total value: {total_value/1e6:,.2f}M VND** "
        f"(equity {equity_value/1e6:,.2f}M + cash {cash/1e6:,.2f}M)",
        f"Cost basis (equity): {cost_basis_equity/1e6:,.2f}M VND | "
        f"**Unrealized P&L: {pnl_abs/1e6:+,.2f}M VND ({pnl_pct:+.2f}%)**",
        f"Peak value: {peak/1e6:,.2f}M VND on {peak_date or 'N/A'} | "
        f"**Drawdown from peak: {drawdown_pct:+.2f}%**",
        "",
        "### Positions",
        "| Ticker | Sector | Shares | Avg Cost | Current | Mkt Value | Weight | P&L | P&L % |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sorted(rows, key=lambda x: -x["market_value"]):
        weight = r["market_value"] / total_value * 100 if total_value else 0
        pnl_i = r["market_value"] - r["cost_basis"]
        pnl_pct_i = pnl_i / r["cost_basis"] * 100 if r["cost_basis"] else 0
        sector_short = (r["sector"][:16] + "…") if len(r["sector"]) > 16 else r["sector"]
        lines.append(
            f"| {r['ticker']} | {sector_short} | {r['shares']:,.0f} | "
            f"{r['avg_cost']:,.0f} | {r['current_price']:,.0f} | "
            f"{r['market_value']/1e6:,.2f}M | {weight:.1f}% | "
            f"{pnl_i/1e6:+,.2f}M | {pnl_pct_i:+.1f}% |"
        )
    cash_weight = cash / total_value * 100 if total_value else 0
    lines.append(f"| CASH | — | — | — | — | {cash/1e6:,.2f}M | {cash_weight:.1f}% | — | — |")

    # Sector allocation
    sector_alloc: dict[str, float] = {}
    for r in rows:
        sector_alloc[r["sector"]] = sector_alloc.get(r["sector"], 0) + r["market_value"]
    lines += [
        "",
        "### Sector Allocation",
        "| Sector | Value | Weight |",
        "|---|---:|---:|",
    ]
    for sector, val in sorted(sector_alloc.items(), key=lambda x: -x[1]):
        w = val / total_value * 100 if total_value else 0
        lines.append(f"| {sector} | {val/1e6:,.2f}M | {w:.1f}% |")
    lines.append(f"| Cash | {cash/1e6:,.2f}M | {cash_weight:.1f}% |")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _fetch_returns(ticker: str, days: int = 90) -> list[float] | None:
    """Fetch daily close-to-close returns for correlation. Returns None on failure."""
    raw = await _vnstock_subprocess("quote_history_full", {"ticker": ticker, "days": days})
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not rows or isinstance(rows, dict) or len(rows) < 20:
        return None
    closes = [float(r["close"]) for r in rows if r.get("close") is not None]
    if len(closes) < 20:
        return None
    return [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]


@register_tool(
    name='get_portfolio_risk',
    description='Compute portfolio-level risk metrics: concentration (single position + sector vs 20% / 35% limits), beta-weighted exposure vs VN-Index, correlation matrix of top holdings (60-day returns, flags pairs ≥ 0.7), dry powder %, current drawdown from peak, and a scored risk verdict (LOW / MODERATE / ELEVATED / HIGH). Use before adding new positions or when portfolio drifts.',
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
async def _get_portfolio_risk(_args: dict) -> list[types.TextContent]:
    portfolio = _load_portfolio()
    holdings = portfolio.get("holdings", [])
    cash = float(portfolio.get("cash_vnd", 0))

    if not holdings:
        return [types.TextContent(
            type="text",
            text="Portfolio is empty. Add positions with `manage_portfolio` first."
        )]

    rows = await _enrich_holdings(portfolio)
    equity_value = sum(r["market_value"] for r in rows)
    total_value = equity_value + cash

    # ── Concentration ────────────────────────────────────────────────────
    max_pos = max((r["market_value"] / total_value * 100 for r in rows), default=0.0)
    max_pos_ticker = max(rows, key=lambda x: x["market_value"])["ticker"] if rows else "—"
    sector_alloc: dict[str, float] = {}
    for r in rows:
        sector_alloc[r["sector"]] = sector_alloc.get(r["sector"], 0) + r["market_value"]
    max_sector = max(sector_alloc.items(), key=lambda x: x[1]) if sector_alloc else ("N/A", 0)
    max_sector_pct = max_sector[1] / total_value * 100 if total_value else 0

    # ── Beta-weighted exposure ───────────────────────────────────────────
    beta_weighted = sum(
        (r["market_value"] / total_value) * _lookup_sector_beta(r["sector"])
        for r in rows
    ) if total_value else 0.0

    # ── Correlation matrix (top 6 holdings by weight) ────────────────────
    top = sorted(rows, key=lambda x: -x["market_value"])[:6]
    top_tickers = [r["ticker"] for r in top]
    returns_list = await asyncio.gather(*[_fetch_returns(t) for t in top_tickers])
    returns_map = {t: r for t, r in zip(top_tickers, returns_list) if r}

    high_corr_pairs: list[tuple[str, str, float]] = []
    for i, t1 in enumerate(top_tickers):
        for t2 in top_tickers[i + 1:]:
            if t1 in returns_map and t2 in returns_map:
                c = _correlation(returns_map[t1], returns_map[t2])
                if c is not None and c >= 0.7:
                    high_corr_pairs.append((t1, t2, c))

    # ── Drawdown ─────────────────────────────────────────────────────────
    peak = float(portfolio.get("peak_value", 0)) or total_value
    drawdown_pct = (total_value - peak) / peak * 100 if peak else 0.0
    cash_pct = cash / total_value * 100 if total_value else 0

    # ── Risk score ───────────────────────────────────────────────────────
    score = 0
    findings: list[str] = []
    if max_pos > 25:
        score += 3; findings.append(f"{max_pos_ticker} at {max_pos:.1f}% (severe overweight)")
    elif max_pos > 20:
        score += 2; findings.append(f"{max_pos_ticker} at {max_pos:.1f}% (over 20% cap)")
    if max_sector_pct > 40:
        score += 3; findings.append(f"{max_sector[0]} sector at {max_sector_pct:.1f}% (severe concentration)")
    elif max_sector_pct > 35:
        score += 2; findings.append(f"{max_sector[0]} sector at {max_sector_pct:.1f}% (over 35% cap)")
    if beta_weighted > 1.3:
        score += 2; findings.append(f"portfolio beta {beta_weighted:.2f} (high market sensitivity)")
    elif beta_weighted > 1.15:
        score += 1; findings.append(f"portfolio beta {beta_weighted:.2f} (moderate leverage to market)")
    if len(high_corr_pairs) >= 3:
        score += 2; findings.append(f"{len(high_corr_pairs)} correlated pairs ≥0.7 (diversification illusion)")
    elif high_corr_pairs:
        score += 1; findings.append(f"{len(high_corr_pairs)} correlated pair(s) ≥0.7")
    if drawdown_pct < -15:
        score += 3; findings.append(f"in {drawdown_pct:.1f}% drawdown from peak — capital preservation mode")
    elif drawdown_pct < -8:
        score += 1; findings.append(f"in {drawdown_pct:.1f}% drawdown — trim losers")
    if cash_pct < 5 and len(rows) > 3:
        score += 1; findings.append(f"cash only {cash_pct:.1f}% — no dry powder for opportunities")

    if score >= 8: verdict = "🔴 HIGH"
    elif score >= 5: verdict = "🟠 ELEVATED"
    elif score >= 2: verdict = "🟡 MODERATE"
    else: verdict = "🟢 LOW"

    # ── Render ───────────────────────────────────────────────────────────
    lines = [
        "## Portfolio Risk Dashboard",
        f"**Risk Level: {verdict}** (score: {score})",
        f"*Portfolio value: {total_value/1e6:,.2f}M VND | {len(rows)} positions | Cash {cash_pct:.1f}%*",
        "",
        "### Concentration",
        "| Metric | Value | Limit | Status |",
        "|---|---:|---:|---|",
        f"| Largest position ({max_pos_ticker}) | {max_pos:.1f}% | 20% | "
        f"{'🔴 breach' if max_pos > 20 else '🟢 within limit'} |",
        f"| Largest sector ({max_sector[0]}) | {max_sector_pct:.1f}% | 35% | "
        f"{'🔴 breach' if max_sector_pct > 35 else '🟢 within limit'} |",
        f"| Cash / dry powder | {cash_pct:.1f}% | 5-20% | "
        f"{'🔴 no dry powder' if cash_pct < 5 else '🟡 low' if cash_pct < 10 else '🟢 healthy'} |",
        "",
        "### Market Sensitivity",
        f"- **Portfolio beta (sector-weighted): {beta_weighted:.2f}** — a 10% VN-Index move implies ~{beta_weighted*10:.1f}% portfolio move",
        f"- **Drawdown from peak: {drawdown_pct:+.2f}%** " + (
            "(new high territory — stay disciplined)" if drawdown_pct >= 0 else
            "(within normal band)" if drawdown_pct > -8 else
            "(elevated — trim underperformers)" if drawdown_pct > -15 else
            "(deep — capital preservation mode)"
        ),
        "",
        "### Correlation Risk (top 6 holdings, 90-day returns)",
    ]
    if high_corr_pairs:
        lines.append("| Pair | Correlation |")
        lines.append("|---|---:|")
        for t1, t2, c in sorted(high_corr_pairs, key=lambda x: -x[2]):
            lines.append(f"| {t1} ↔ {t2} | {c:+.2f} |")
        lines.append("")
        lines.append("*Highly correlated pairs move together — diversification is weaker than position count suggests.*")
    else:
        lines.append("*No pairs above 0.7 correlation — decent diversification.*")

    lines += ["", "### Findings"]
    lines += [f"- {f}" for f in findings] if findings else ["- *No material risk findings.*"]

    lines += [
        "",
        "### Risk Rules (VN market defaults)",
        "- Single position: max 20% of portfolio",
        "- Sector concentration: max 35%",
        "- Portfolio beta: aim ≤ 1.15 for balanced exposure",
        "- Cash: 5-20% dry powder for opportunities",
        "- Drawdown: >15% triggers capital preservation (halve position sizes)",
    ]
    return [types.TextContent(type="text", text="\n".join(lines))]


@register_tool(
    name='get_rebalancing_suggestions',
    description='Compare current portfolio weights vs target_weight set for each holding. Flags deviations above threshold (default 3%). Suggests trim/add trades sized in shares and VND to move each position back toward its target. Only considers holdings with a target_weight defined.',
    input_schema={'type': 'object', 'properties': {'threshold_pct': {'type': 'number', 'description': 'Deviation threshold in % that triggers a rebalance (default 3.0).', 'default': 3.0}}, 'required': []},
)
async def _get_rebalancing_suggestions(args: dict) -> list[types.TextContent]:
    threshold = float(args.get("threshold_pct", 3.0))
    portfolio = _load_portfolio()
    holdings = portfolio.get("holdings", [])
    cash = float(portfolio.get("cash_vnd", 0))

    with_targets = [h for h in holdings if h.get("target_weight") is not None]
    if not with_targets:
        return [types.TextContent(
            type="text",
            text=(
                "No holdings have `target_weight` set. Add targets with "
                "`manage_portfolio(action='add', ticker='FPT', shares=..., avg_cost=..., target_weight=15)`."
            )
        )]

    rows = await _enrich_holdings(portfolio)
    equity_value = sum(r["market_value"] for r in rows)
    total_value = equity_value + cash

    target_sum = sum(float(h["target_weight"]) for h in with_targets)
    warnings: list[str] = []
    if abs(target_sum - 100) > 1 and abs(target_sum - (100 - cash / total_value * 100 if total_value else 0)) > 1:
        warnings.append(f"Target weights sum to {target_sum:.1f}% — expected ~100% (or 100% minus cash target)")

    suggestions: list[dict] = []
    for r in rows:
        target = r.get("target_weight")
        if target is None:
            continue
        current_weight = r["market_value"] / total_value * 100 if total_value else 0
        deviation = current_weight - float(target)
        if abs(deviation) < threshold:
            continue
        target_value = float(target) / 100 * total_value
        delta_vnd = target_value - r["market_value"]
        delta_shares = delta_vnd / r["current_price"] if r["current_price"] else 0
        action = "TRIM" if delta_shares < 0 else "ADD"
        suggestions.append({
            "ticker": r["ticker"],
            "current_weight": current_weight,
            "target_weight": float(target),
            "deviation": deviation,
            "action": action,
            "delta_shares": abs(delta_shares),
            "delta_vnd": abs(delta_vnd),
            "current_price": r["current_price"],
        })

    lines = [
        "## Rebalancing Suggestions",
        f"*Portfolio value: {total_value/1e6:,.2f}M VND | Threshold: ±{threshold:.1f}%*",
        "",
    ]
    if warnings:
        lines += [f"⚠️  {w}" for w in warnings] + [""]

    if not suggestions:
        lines.append(f"✅ All targeted positions within ±{threshold:.1f}% of target. No rebalancing needed.")
        return [types.TextContent(type="text", text="\n".join(lines))]

    lines += [
        "### Trades",
        "| Ticker | Current | Target | Deviation | Action | Shares | VND | @ Price |",
        "|---|---:|---:|---:|---|---:|---:|---:|",
    ]
    for s in sorted(suggestions, key=lambda x: -abs(x["deviation"])):
        icon = "🔴 SELL" if s["action"] == "TRIM" else "🟢 BUY"
        lines.append(
            f"| {s['ticker']} | {s['current_weight']:.1f}% | {s['target_weight']:.1f}% | "
            f"{s['deviation']:+.1f}% | {icon} | {s['delta_shares']:,.0f} | "
            f"{s['delta_vnd']/1e6:,.2f}M | {s['current_price']:,.0f} |"
        )

    trim_total = sum(s["delta_vnd"] for s in suggestions if s["action"] == "TRIM")
    add_total = sum(s["delta_vnd"] for s in suggestions if s["action"] == "ADD")
    lines += [
        "",
        f"**Total to trim: {trim_total/1e6:,.2f}M VND** | **Total to add: {add_total/1e6:,.2f}M VND**",
        f"Net cash impact: {(trim_total - add_total)/1e6:+,.2f}M VND",
        "",
        "*Execute trims first to free cash before adds. Consider tax/transaction costs — "
        "if a deviation is small and stable, ignore it and revisit next quarter.*",
    ]
    return [types.TextContent(type="text", text="\n".join(lines))]


async def _fetch_vnindex_history(days: int) -> list[dict]:
    """Fetch VN-Index daily history via vnstock. Returns list of {date, close}."""
    raw = await _vnstock_subprocess("quote_history_full", {"ticker": "VNINDEX", "days": days})
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not rows or isinstance(rows, dict):
        return []
    result = []
    for r in rows:
        t = r.get("time", "")
        close = r.get("close")
        if not t or close is None:
            continue
        result.append({"date": str(t)[:10], "close": float(close)})
    return result


@register_tool(
    name='get_portfolio_returns',
    description='Compute portfolio return metrics from daily snapshots (auto-saved to `.portfolio_snapshots.json` each time `get_portfolio_overview` runs). Returns: simple return since inception, annualized CAGR, Time-Weighted Return (TWR), period returns (YTD / 1M / 3M / 6M), max & current drawdown, annualized volatility, Sharpe ratio, and comparison vs VN-Index (alpha). Requires ≥2 snapshots — run `get_portfolio_overview` daily to build history.',
    input_schema={'type': 'object', 'properties': {'risk_free_rate': {'type': 'number', 'description': 'Annual risk-free rate for Sharpe (default 4.0% — VN 10-year govt bond).', 'default': 4.0}}, 'required': []},
)
async def _get_portfolio_returns(args: dict) -> list[types.TextContent]:
    from datetime import date
    risk_free = float(args.get("risk_free_rate", 4.0)) / 100

    snapshots = _load_snapshots()
    if len(snapshots) < 2:
        return [types.TextContent(
            type="text",
            text=(
                f"Need at least 2 daily snapshots to compute returns (have {len(snapshots)}). "
                "Run `get_portfolio_overview` each trading day to build history — a snapshot is "
                "auto-saved to `.portfolio_snapshots.json` on every call."
            )
        )]

    first = snapshots[0]
    last = snapshots[-1]
    first_v = float(first["total_value"])
    last_v = float(last["total_value"])
    total_days = (date.fromisoformat(last["date"]) - date.fromisoformat(first["date"])).days or 1

    simple_return = (last_v / first_v - 1) if first_v > 0 else 0.0
    cagr = _annualize(simple_return, total_days)

    daily_returns = _daily_returns_from_snapshots(snapshots)
    twr = _twr(daily_returns)
    twr_cagr = _annualize(twr, total_days)

    # Volatility (annualized, using trading days ≈ 252)
    if len(daily_returns) >= 5:
        mean_r = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1) if len(daily_returns) > 1 else 0
        daily_vol = variance ** 0.5
        annual_vol = daily_vol * (252 ** 0.5)
    else:
        annual_vol = 0.0

    sharpe = (twr_cagr - risk_free) / annual_vol if annual_vol > 0 else 0.0
    max_dd, curr_dd = _rolling_drawdown(snapshots)

    # YTD / 1M / 3M / 6M
    period_returns: dict[str, tuple[float | None, int]] = {
        "1M": period_return_from_snapshots(snapshots, 30),
        "3M": period_return_from_snapshots(snapshots, 90),
        "6M": period_return_from_snapshots(snapshots, 180),
    }
    year_start = f"{last['date'][:4]}-01-01"
    ytd_start = _find_snapshot_at_or_before(snapshots, year_start) or first
    ytd_return = (last_v / float(ytd_start["total_value"]) - 1) if float(ytd_start["total_value"]) > 0 else None
    ytd_days = (date.fromisoformat(last["date"]) - date.fromisoformat(ytd_start["date"])).days

    # vs VN-Index (spanning entire snapshot history)
    lookback_days = max(total_days + 10, 30)
    vnindex_hist = await _fetch_vnindex_history(lookback_days)
    vn_return: float | None = None
    vn_alpha: float | None = None
    if vnindex_hist:
        vn_start = _find_snapshot_at_or_before(vnindex_hist, first["date"]) or vnindex_hist[0]
        vn_end = _find_snapshot_at_or_before(vnindex_hist, last["date"]) or vnindex_hist[-1]
        vs, ve = float(vn_start["close"]), float(vn_end["close"])
        if vs > 0:
            vn_return = ve / vs - 1
            vn_alpha = simple_return - vn_return

    # ── Render ──────────────────────────────────────────────────────────
    lines = [
        "## Portfolio Returns",
        f"*Based on {len(snapshots)} daily snapshots from {first['date']} to {last['date']} "
        f"({total_days} calendar days)*",
        "",
        "### Headline",
        "| Metric | Value |",
        "|---|---:|",
        f"| Starting value | {first_v/1e6:,.2f}M VND |",
        f"| Current value | {last_v/1e6:,.2f}M VND |",
        f"| **Simple return** | **{simple_return*100:+.2f}%** |",
        f"| **Annualized (CAGR)** | **{cagr*100:+.2f}%** |",
        f"| Time-Weighted Return (TWR) | {twr*100:+.2f}% |",
        f"| TWR annualized | {twr_cagr*100:+.2f}% |",
        "",
        "### Period Returns",
        "| Period | Return | Actual days |",
        "|---|---:|---:|",
    ]
    lines.append(
        f"| YTD | {ytd_return*100:+.2f}% | {ytd_days} |" if ytd_return is not None
        else "| YTD | — | — |"
    )
    for label, (ret, actual) in period_returns.items():
        lines.append(
            f"| {label} | {ret*100:+.2f}% | {actual} |" if ret is not None
            else f"| {label} | — (insufficient history) | — |"
        )

    lines += [
        "",
        "### Risk-Adjusted",
        "| Metric | Value |",
        "|---|---:|",
        f"| Annualized volatility | {annual_vol*100:.2f}% |",
        f"| Sharpe ratio (rf={risk_free*100:.1f}%) | {sharpe:+.2f} |",
        f"| Max drawdown | {max_dd*100:+.2f}% |",
        f"| Current drawdown | {curr_dd*100:+.2f}% |",
    ]

    lines += ["", "### vs VN-Index"]
    if vn_return is not None:
        lines += [
            "| Metric | Portfolio | VN-Index | Alpha |",
            "|---|---:|---:|---:|",
            f"| Return over period | {simple_return*100:+.2f}% | {vn_return*100:+.2f}% | **{vn_alpha*100:+.2f}pp** |",
        ]
    else:
        lines.append("*VN-Index history unavailable — check network / vnstock.*")

    lines += [
        "",
        "### How to read",
        "- **Simple return** measures dollar growth including cash flows — distorted by deposits/withdrawals",
        "- **TWR** links daily returns; closer to \"pure investment performance\" but still assumes no explicit cash flow tracking",
        "- **Alpha vs VN-Index** > 0 means you beat the market; < 0 means index would've done better (consider indexing)",
        "- **Sharpe > 1** is good, > 2 is excellent for retail — but needs 6+ months of data to be meaningful",
        f"- Snapshots stored in `.portfolio_snapshots.json` — currently {len(snapshots)} points, need 60+ for stable Sharpe",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


@register_tool(
    name='get_sector_rotation',
    description='Compute VN sector rotation: equal-weighted sector returns over 1M / 3M / 6M / YTD for ~10 sectors (Banking, Real Estate, Technology, Steel/Materials, Consumer Staples, Retail, Aviation, Industrial, Energy, Telecom). Ranks sectors by relative strength vs VN-Index (alpha). Identifies leaders, laggards, and whether cyclicals or defensives are leading — foundation for market cycle positioning.',
    input_schema={'type': 'object', 'properties': {'rank_by': {'type': 'string', 'enum': ['1M', '3M', '6M', 'YTD'], 'default': '3M', 'description': "Which period's RS to rank by (default 3M)."}}, 'required': []},
)
async def _get_sector_rotation(args: dict) -> list[types.TextContent]:
    rank_by = args.get("rank_by", "3M")
    if rank_by not in {"1M", "3M", "6M", "YTD"}:
        rank_by = "3M"

    all_tickers = sorted({t for tickers in _VN_SECTORS.values() for t in tickers} | {"VNINDEX"})
    histories = await asyncio.gather(*[
        _vnstock_subprocess("quote_history_full", {"ticker": t, "days": 220})
        for t in all_tickers
    ])
    hist_map = {t: _parse_price_series(h) for t, h in zip(all_tickers, histories)}

    vn_series = hist_map.get("VNINDEX", [])
    if not vn_series:
        return [types.TextContent(
            type="text",
            text="Failed to fetch VN-Index data — cannot compute sector rotation without benchmark."
        )]

    def _period_lookup(days_back: int, is_ytd: bool):
        return (lambda s: _ytd_return(s)) if is_ytd else (lambda s: period_return_from_series(s, days_back))

    period_config: list[tuple[str, callable]] = [
        ("1M",  _period_lookup(21, False)),
        ("3M",  _period_lookup(63, False)),
        ("6M",  _period_lookup(126, False)),
        ("YTD", _period_lookup(0, True)),
    ]

    # VN-Index returns per period
    vn_returns = {label: fn(vn_series) for label, fn in period_config}

    # Sector aggregate returns per period (equal-weighted mean of member tickers)
    sector_returns: dict[str, dict[str, float | None]] = {}
    # Also keep per-ticker returns so we can surface top individuals within each sector
    ticker_returns: dict[str, dict[str, float | None]] = {}  # {ticker: {"1M": ret, ...}}
    ticker_sector: dict[str, str] = {}
    for sector, tickers in _VN_SECTORS.items():
        period_map: dict[str, float | None] = {}
        for label, fn in period_config:
            rets = [fn(hist_map.get(t, [])) for t in tickers]
            rets_valid = [r for r in rets if r is not None]
            period_map[label] = sum(rets_valid) / len(rets_valid) if rets_valid else None
        sector_returns[sector] = period_map
        for t in tickers:
            ticker_sector[t] = sector
            ticker_returns[t] = {label: fn(hist_map.get(t, [])) for label, fn in period_config}

    # Relative strength = sector − VN-Index
    def rs(sector: str, period: str) -> float | None:
        s = sector_returns[sector].get(period)
        v = vn_returns.get(period)
        return (s - v) if s is not None and v is not None else None

    ranked = sorted(
        _VN_SECTORS.keys(),
        key=lambda s: rs(s, rank_by) if rs(s, rank_by) is not None else -999,
        reverse=True,
    )

    # ── Cyclical vs Defensive leadership ─────────────────────────────
    top3 = ranked[:3]
    bottom3 = ranked[-3:]
    top3_cyclical = sum(1 for s in top3 if s in _CYCLICAL_SECTORS)
    top3_defensive = sum(1 for s in top3 if s in _DEFENSIVE_SECTORS)
    if top3_cyclical >= 2:
        leadership = "🟢 CYCLICAL leadership"
        leadership_note = "risk-on, credit expansion typically underway"
    elif top3_defensive >= 2:
        leadership = "🔴 DEFENSIVE leadership"
        leadership_note = "risk-off, capital preservation regime"
    else:
        leadership = "⚪ MIXED leadership"
        leadership_note = "rotation in progress, no clear regime"

    # ── Render ────────────────────────────────────────────────────────
    lines = [
        "## VN Sector Rotation",
        f"*Ranked by **{rank_by} relative strength** vs VN-Index. "
        f"Equal-weighted returns across {sum(len(v) for v in _VN_SECTORS.values())} tickers "
        f"in {len(_VN_SECTORS)} sectors.*",
        "",
        f"**Leadership regime: {leadership}** — {leadership_note}",
        f"*Top 3: {', '.join(top3)} · Bottom 3: {', '.join(bottom3)}*",
        "",
        "### Sector Returns vs VN-Index",
        "| Rank | Sector | 1M | 3M | 6M | YTD | Type |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for i, sector in enumerate(ranked, 1):
        cells = []
        for label, _ in period_config:
            s_ret = sector_returns[sector].get(label)
            v_ret = vn_returns.get(label)
            if s_ret is None:
                cells.append("—")
            elif v_ret is None:
                cells.append(f"{s_ret*100:+.1f}%")
            else:
                alpha = (s_ret - v_ret) * 100
                icon = "🟢" if alpha > 3 else "🔴" if alpha < -3 else ""
                cells.append(f"{s_ret*100:+.1f}% ({alpha:+.1f}pp) {icon}")
        sector_type = "cyclical" if sector in _CYCLICAL_SECTORS else "defensive" if sector in _DEFENSIVE_SECTORS else "mixed"
        lines.append(f"| {i} | **{sector}** | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} | {sector_type} |")

    # VN-Index benchmark row
    lines.append(
        f"| — | *VN-Index* | "
        f"{vn_returns['1M']*100:+.1f}% | " if vn_returns['1M'] is not None else "| — | *VN-Index* | — |"
    )
    # Actually build proper row
    lines[-1] = "| — | *VN-Index (benchmark)* | " + " | ".join(
        f"{vn_returns[p]*100:+.1f}%" if vn_returns[p] is not None else "—"
        for p, _ in period_config
    ) + " | — |"

    # ── Top tickers per leading sector ────────────────────────────────
    lines += [
        "",
        f"### Top Tickers per Leading Sector (by {rank_by} return)",
        "*Individual tickers driving the sector performance — these are your candidates for Stock Selection (Tier 4).*",
        "",
    ]
    for sector in top3:
        sector_tickers = _VN_SECTORS.get(sector, [])
        # Rank tickers by chosen period, filter out None
        ranked_tickers = sorted(
            [(t, ticker_returns[t].get(rank_by)) for t in sector_tickers if ticker_returns[t].get(rank_by) is not None],
            key=lambda x: x[1],
            reverse=True,
        )
        if not ranked_tickers:
            continue
        vn_ret = vn_returns.get(rank_by)
        lines.append(f"**{sector}** ({sector_returns[sector].get(rank_by, 0)*100:+.1f}% {rank_by} sector avg)")
        lines.append("")
        lines.append("| Ticker | 1M | 3M | 6M | YTD | Alpha vs VNI |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for t, _ret in ranked_tickers[:5]:  # top 5 per sector
            ret_by_p = ticker_returns[t]
            cells = []
            for p, _ in period_config:
                r = ret_by_p.get(p)
                cells.append(f"{r*100:+.1f}%" if r is not None else "—")
            r_focus = ret_by_p.get(rank_by)
            alpha = (r_focus - vn_ret) * 100 if r_focus is not None and vn_ret is not None else None
            alpha_str = f"{alpha:+.1f}pp" if alpha is not None else "—"
            lines.append(f"| **{t}** | {cells[0]} | {cells[1]} | {cells[2]} | {cells[3]} | {alpha_str} |")
        lines.append("")

    lines += [
        "### How to read",
        "- **Cyclical leadership** = investors are risk-on, betting on credit expansion (banks/real estate/materials/retail leading)",
        "- **Defensive leadership** = risk-off, hiding in earnings stability (staples/telecom leading)",
        "- **Relative strength (alpha)**: sector return minus VN-Index return. Positive = outperforming market",
        "- **Rotation signal**: when cyclicals start leading after defensive regime, historical entry point for VN bull markets",
        "- **Top tickers per sector** are your Tier 4 candidates — deep-dive them with `get_quality_score` + `get_dcf_valuation` + `get_technical_analysis`",
        "- Combine with `get_money_supply` (credit direction) and `get_market_cycle` (phase classification)",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


async def _get_top_tickers_by_sector(args: dict) -> dict:
    """Structured (non-MCP) helper: returns top-N tickers per top-M sectors by chosen period.

    Returns: {rank_by, vni_return, sectors: [{name, sector_return, alpha_vs_vni, type, tickers: [{ticker, returns_by_period, alpha_vs_vni}]}]}
    """
    rank_by = args.get("rank_by", "3M")
    if rank_by not in {"1M", "3M", "6M", "YTD"}:
        rank_by = "3M"
    top_sectors_n = int(args.get("top_sectors", 3))
    top_tickers_n = int(args.get("top_tickers", 5))

    all_tickers = sorted({t for tickers in _VN_SECTORS.values() for t in tickers} | {"VNINDEX"})
    histories = await asyncio.gather(*[
        _vnstock_subprocess("quote_history_full", {"ticker": t, "days": 220})
        for t in all_tickers
    ])
    hist_map = {t: _parse_price_series(h) for t, h in zip(all_tickers, histories)}
    vn_series = hist_map.get("VNINDEX", [])
    if not vn_series:
        return {"error": "VN-Index data unavailable"}

    def _fn(days: int, is_ytd: bool):
        return (lambda s: _ytd_return(s)) if is_ytd else (lambda s: period_return_from_series(s, days))

    period_map = {"1M": _fn(21, False), "3M": _fn(63, False), "6M": _fn(126, False), "YTD": _fn(0, True)}
    vni_returns = {p: fn(vn_series) for p, fn in period_map.items()}
    vni_focus = vni_returns[rank_by]

    sector_scores: list[tuple[str, float, dict[str, float]]] = []  # (sector, focus_ret, all_period_returns)
    per_sector_tickers: dict[str, list[dict]] = {}

    for sector, tickers in _VN_SECTORS.items():
        rets_by_ticker: list[tuple[str, dict[str, float]]] = []
        for t in tickers:
            ticker_rets = {p: fn(hist_map.get(t, [])) for p, fn in period_map.items()}
            if ticker_rets.get(rank_by) is not None:
                rets_by_ticker.append((t, ticker_rets))
        if not rets_by_ticker:
            continue
        # Sector average by chosen period
        avg_focus = sum(r[1][rank_by] for r in rets_by_ticker) / len(rets_by_ticker)
        # Full sector avg by period
        sector_avg_by_period: dict[str, float] = {}
        for p in period_map:
            vals = [r[1][p] for r in rets_by_ticker if r[1].get(p) is not None]
            if vals:
                sector_avg_by_period[p] = sum(vals) / len(vals)
        sector_scores.append((sector, avg_focus, sector_avg_by_period))

        # Top N tickers within sector, ranked by focus period
        sorted_ticks = sorted(rets_by_ticker, key=lambda x: x[1][rank_by], reverse=True)[:top_tickers_n]
        per_sector_tickers[sector] = [
            {
                "ticker": t,
                "returns": {p: (round(v * 100, 2) if v is not None else None) for p, v in rets.items()},
                "alpha_vs_vni": (round((rets[rank_by] - vni_focus) * 100, 2) if vni_focus is not None else None),
            }
            for t, rets in sorted_ticks
        ]

    # Rank sectors by chosen period
    sector_scores.sort(key=lambda x: x[1], reverse=True)
    top_sectors = sector_scores[:top_sectors_n]

    result = {
        "rank_by": rank_by,
        "vni_returns": {p: (round(v * 100, 2) if v is not None else None) for p, v in vni_returns.items()},
        "sectors": [
            {
                "name": name,
                "sector_returns": {p: (round(v * 100, 2) if v is not None else None) for p, v in per_period.items()},
                "alpha_vs_vni": (round((focus - vni_focus) * 100, 2) if vni_focus is not None else None),
                "type": "cyclical" if name in _CYCLICAL_SECTORS else "defensive" if name in _DEFENSIVE_SECTORS else "mixed",
                "tickers": per_sector_tickers.get(name, []),
            }
            for name, focus, per_period in top_sectors
        ],
    }
    return result


@register_tool(
    name='get_market_cycle',
    description='Classify VN market cycle phase by combining credit conditions, VN-Index trend, and sector leadership. Returns one of 8 phases (Bottom / Early Recovery / Mid Expansion / Late Cycle / Peak / Distribution / Bear / Stabilization) with recommended sector positioning. Uses `get_money_supply` credit signal + VNINDEX MA200 trend + sector rotation leadership.',
    input_schema={'type': 'object', 'properties': {}, 'required': []},
)
async def _get_market_cycle(_args: dict) -> list[types.TextContent]:
    # ── 1. Credit signal: reuse top-5 banks aggregate ────────────────────
    bank_annual = await asyncio.gather(*[_fetch_bank_loan_series(t, "year") for t in _M2_BANKS])
    bank_map = dict(zip(_M2_BANKS, bank_annual))
    all_periods: set[str] = set()
    for s in bank_map.values():
        all_periods.update(s.keys())
    periods_sorted = sorted(all_periods, reverse=True)
    aggregates: list[tuple[str, float]] = []
    for p in periods_sorted:
        total = sum(s.get(p, 0) for s in bank_map.values() if s.get(p))
        if total > 0:
            aggregates.append((p, total))
    credit_yoy: float | None = None
    if len(aggregates) >= 2 and aggregates[1][1] > 0:
        credit_yoy = (aggregates[0][1] / aggregates[1][1] - 1) * 100
    credit_state = "LOOSE" if credit_yoy is not None and credit_yoy > 15 else \
                    "TIGHT" if credit_yoy is not None and credit_yoy < 10 else \
                    "NEUTRAL"

    # ── 2. VN-Index trend: price vs MA200 ─────────────────────────────
    vn_raw = await _vnstock_subprocess("quote_history_full", {"ticker": "VNINDEX", "days": 400})
    vn_series = _parse_price_series(vn_raw)
    trend_state = "UNKNOWN"
    vn_last: float | None = None
    ma200: float | None = None
    if len(vn_series) >= 200:
        closes = [c for _, c in vn_series]
        vn_last = closes[-1]
        ma200 = sum(closes[-200:]) / 200
        trend_state = "BULL" if vn_last > ma200 else "BEAR"

    # ── 3. Sector leadership: fetch quickly, compute 3M RS ────────────
    all_sector_tickers = sorted({t for ts in _VN_SECTORS.values() for t in ts})
    hists = await asyncio.gather(*[
        _vnstock_subprocess("quote_history_full", {"ticker": t, "days": 90})
        for t in all_sector_tickers
    ])
    hist_map = {t: _parse_price_series(h) for t, h in zip(all_sector_tickers, hists)}

    def _sector_3m_ret(tickers: list[str]) -> float | None:
        rets = [period_return_from_series(hist_map.get(t, []), 63) for t in tickers]
        rets = [r for r in rets if r is not None]
        return sum(rets) / len(rets) if rets else None

    sector_3m = {s: _sector_3m_ret(ts) for s, ts in _VN_SECTORS.items()}
    ranked = sorted(sector_3m.items(), key=lambda x: x[1] if x[1] is not None else -999, reverse=True)
    top3 = [s for s, _ in ranked[:3]]
    cyclical_count = sum(1 for s in top3 if s in _CYCLICAL_SECTORS)
    defensive_count = sum(1 for s in top3 if s in _DEFENSIVE_SECTORS)
    if cyclical_count >= 2:
        leadership = "CYCLICAL"
    elif defensive_count >= 2:
        leadership = "DEFENSIVE"
    else:
        leadership = "MIXED"

    # ── 4. Classify phase (8-phase framework) ─────────────────────────
    phase_map: dict[tuple[str, str, str], tuple[str, str, str]] = {
        # (credit, trend, leadership) -> (phase, color, positioning)
        ("LOOSE", "BULL", "CYCLICAL"):
            ("MID EXPANSION", "🟢",
             "Fully invested. Overweight cyclicals (banks, real estate, materials). Ride the trend."),
        ("LOOSE", "BULL", "DEFENSIVE"):
            ("EARLY RECOVERY", "🟡",
             "Rotation incomplete. Contrarian bet on cyclicals often works — take starter positions."),
        ("LOOSE", "BULL", "MIXED"):
            ("EARLY EXPANSION", "🟢",
             "Momentum building. Add to cyclical leaders, keep some cash for pullbacks."),
        ("LOOSE", "BEAR", "CYCLICAL"):
            ("BOTTOM", "🟢",
             "Credit turning + cyclicals leading despite bear = major turnaround. High-conviction long entry."),
        ("LOOSE", "BEAR", "DEFENSIVE"):
            ("STABILIZATION", "🟡",
             "Credit improving but market lagging. Wait for cyclical breakout before adding equity risk."),
        ("LOOSE", "BEAR", "MIXED"):
            ("STABILIZATION", "🟡",
             "Credit improving but market mixed. Wait for clearer cyclical leadership."),
        ("TIGHT", "BULL", "CYCLICAL"):
            ("LATE CYCLE", "🟠",
             "Dangerous — cyclicals leading with tightening credit. Trim positions, raise cash to 20-30%."),
        ("TIGHT", "BULL", "DEFENSIVE"):
            ("DISTRIBUTION", "🔴",
             "Peak forming. Rotate aggressively to staples/telecom. Raise cash to 40%+."),
        ("TIGHT", "BULL", "MIXED"):
            ("LATE CYCLE", "🟠",
             "Trend still up but credit tight — leadership rotation coming. Reduce risk."),
        ("TIGHT", "BEAR", "CYCLICAL"):
            ("DECLINE", "🔴",
             "Early bear — cyclicals still leading but trend broken. Cut equity to 30-40%."),
        ("TIGHT", "BEAR", "DEFENSIVE"):
            ("BEAR", "🔴",
             "Capital preservation. 50%+ cash, defensives only, no cyclical exposure."),
        ("TIGHT", "BEAR", "MIXED"):
            ("BEAR", "🔴",
             "Capital preservation. 50%+ cash. Wait for credit to loosen before deploying."),
        ("NEUTRAL", "BULL", "CYCLICAL"):
            ("MID EXPANSION", "🟡",
             "Credit not decisively loose. Stay invested but avoid leverage."),
        ("NEUTRAL", "BULL", "DEFENSIVE"):
            ("TRANSITION", "🟡",
             "Late-cycle behavior emerging. Rotate half of cyclical exposure to defensives."),
        ("NEUTRAL", "BEAR", "CYCLICAL"):
            ("STABILIZATION", "🟡",
             "Bear market rally likely. Wait for credit signal before adding risk."),
        ("NEUTRAL", "BEAR", "DEFENSIVE"):
            ("BEAR", "🟠",
             "Defensive positioning. Cash 40%, defensives, wait for credit loosening."),
    }
    key = (credit_state, trend_state, leadership)
    phase, color, positioning = phase_map.get(key, ("UNKNOWN", "⚪", "Insufficient signal to classify."))

    # ── Render ────────────────────────────────────────────────────────
    # Individual signal explanations
    if credit_state == "LOOSE":
        credit_read = f"🟢 **LOOSE** — banks lending aggressively (>15% YoY). This puts new money into the economy which flows to asset prices. Historically preceded 2016-18 and 2020-21 VN bull markets."
    elif credit_state == "TIGHT":
        credit_read = f"🔴 **TIGHT** — banks lending cautiously (<10% YoY). Money leaves risk assets. Historically preceded 2018 correction and 2022 bear."
    else:
        credit_read = f"⚪ **NEUTRAL** — banks lending moderately (10-15% YoY). No decisive signal. Wait for clearer direction."

    if trend_state == "BULL":
        trend_read = f"🟢 **BULL** — VN-Index above its 200-day moving average (a smoothed average of ~10 months of closing prices). Institutional consensus is buyers > sellers on a longer horizon. Historically, staying long above MA200 captures most of VN market's gains."
    elif trend_state == "BEAR":
        trend_read = f"🔴 **BEAR** — VN-Index below its 200-day moving average. Institutional consensus is sellers > buyers. Historically, staying long below MA200 leads to drawdowns."
    else:
        trend_read = "⚪ **UNKNOWN** — insufficient price history to compute MA200."

    if leadership == "CYCLICAL":
        lead_read = (
            f"🟢 **CYCLICAL leadership** — Banks / Real Estate / Materials / Retail / Aviation among top-3 sectors. "
            f"Investors are risk-on, betting on economic expansion. This is what you WANT to see in a bull market. "
            f"Top 3: {', '.join(top3)}."
        )
    elif leadership == "DEFENSIVE":
        lead_read = (
            f"🔴 **DEFENSIVE leadership** — Consumer Staples / Telecom / Utilities among top-3. "
            f"Investors hiding in stable earnings, avoiding cyclical risk. This precedes or accompanies market weakness. "
            f"Top 3: {', '.join(top3)}."
        )
    else:
        lead_read = f"🟡 **MIXED leadership** — no clear cyclical or defensive tilt. Rotation in progress. Top 3: {', '.join(top3)}."

    # Positioning implications
    def _cash_range_from_phase(p: str) -> str:
        return {
            "MID EXPANSION": "5-15% cash. Fully invested with a small dry-powder reserve.",
            "EARLY RECOVERY": "15-25% cash. Take starter positions in cyclicals; don't fully deploy.",
            "EARLY EXPANSION": "10-20% cash. Add to cyclical leaders on pullbacks.",
            "BOTTOM": "5-15% cash. High-conviction long entry — this is where multi-year gains start.",
            "STABILIZATION": "30-40% cash. Wait for confirmation before deploying.",
            "LATE CYCLE": "25-35% cash. Rotate half of cyclical exposure to defensives.",
            "DISTRIBUTION": "40-50% cash. Aggressive rotation to defensives.",
            "TRANSITION": "25-35% cash. Half cyclical / half defensive.",
            "DECLINE": "50-60% cash. Cut cyclical exposure sharply.",
            "BEAR": "50-70% cash. Defensives only. Wait for credit to loosen.",
        }.get(p, "Depends on your risk tolerance.")

    sector_moves = {
        "MID EXPANSION": "Overweight: Banks, Real Estate, Materials, Retail. Underweight: Staples, Telecom.",
        "EARLY RECOVERY": "Rotate: Staples/Telecom → Banks/Materials. Small starter positions in beaten-down cyclicals.",
        "EARLY EXPANSION": "Overweight: cyclical leaders showing strongest RS (see `get_sector_rotation`). Add on 3-5% pullbacks.",
        "BOTTOM": "Buy: Banks, Real Estate, quality cyclicals with strong balance sheets. Skip: high-debt turnarounds until confirmed.",
        "STABILIZATION": "Hold: existing quality positions. Do not add new cyclical exposure yet.",
        "LATE CYCLE": "Trim: your best-performing cyclicals (they'll drop first). Add: defensive dividend payers.",
        "DISTRIBUTION": "Sell: cyclicals aggressively. Buy: Consumer Staples (VNM, SAB, MSN), Telecom (VGI, CTR).",
        "TRANSITION": "Rebalance: 50/50 cyclical/defensive. Take profits on winners.",
        "DECLINE": "Sell: any cyclical still in green. Add: only defensive names showing relative strength.",
        "BEAR": "Hold: cash + short-duration bonds + defensive dividends. No cyclicals.",
    }
    sector_moves_line = sector_moves.get(phase, "Follow the positioning line above.")

    lines = [
        f"## VN Market Cycle Phase: {color} **{phase}**",
        "",
        "> **How this framework works**: In VN market (and globally), 3 signals — credit conditions,",
        "> index trend, and sector leadership — combine to describe where we are in the cycle.",
        "> Each phase implies a **specific portfolio posture** (cash %, cyclical vs defensive tilt).",
        "> Read each section below to understand WHY the framework says what it says.",
        "",
        "---",
        "",
        "### 📊 SIGNAL 1 — Credit Conditions",
        "",
        "**What it measures**: Are Vietnam's largest 5 banks (VCB/BID/CTG/TCB/MBB) growing their loan books aggressively (loose credit)",
        "or cautiously (tight credit)? Loans are the mechanism by which new money enters the economy.",
        "",
        "**Why it matters**: In VN, credit growth **leads M2 growth** by 1-2 quarters, and M2 growth **leads asset prices**.",
        "So bank credit YoY is one of the earliest signals for market direction. SBV sets an annual credit growth target",
        "(historically 14-15%) — banks hitting the cap = liquidity being pumped into economy.",
        "",
        f"**Current reading**: {credit_read}",
        f"- Metric: Top-5 bank aggregate loans YoY = **{f'{credit_yoy:+.2f}%' if credit_yoy is not None else '—'}**",
        f"- Thresholds: >15% = LOOSE (bullish), 10-15% = NEUTRAL, <10% = TIGHT (bearish)",
        "",
        "---",
        "",
        "### 📈 SIGNAL 2 — VN-Index Trend",
        "",
        "**What it measures**: Is the VN-Index above or below its 200-day moving average (MA200)?",
        "MA200 = the average of the last 200 daily closes ≈ 10 months of price data.",
        "",
        "**Why it matters**: MA200 is a **regime filter** used by institutional investors globally. It smooths out",
        "short-term noise. Above MA200 = uptrend intact, participants are buying dips. Below MA200 = downtrend,",
        "participants are selling rallies. Historically 80%+ of VN market gains happen while VNINDEX > MA200.",
        "",
        f"**Current reading**: {trend_read}",
    ]
    if vn_last is not None and ma200 is not None:
        gap_pct = (vn_last - ma200) / ma200 * 100
        lines.append(f"- VN-Index: **{vn_last:,.1f}** vs MA200: **{ma200:,.1f}** → gap **{gap_pct:+.1f}%**")
    lines += [
        "- Interpretation: above MA200 by >2% = strong bull, within ±2% = uncertain/whipsaw, below by >2% = strong bear",
        "",
        "---",
        "",
        "### 🎯 SIGNAL 3 — Sector Leadership",
        "",
        "**What it measures**: Which sectors are leading over the last 3 months? We split VN sectors into two groups:",
        "- **Cyclical**: Banks, Real Estate, Steel/Materials, Retail, Aviation, Industrial, Energy — profits depend on economic growth",
        "- **Defensive**: Consumer Staples, Telecom — profits stable regardless of economy (people always buy food, use phones)",
        "",
        "**Why it matters**: When investors are OPTIMISTIC, they buy cyclicals (leveraged to growth). When PESSIMISTIC,",
        "they hide in defensives. Leadership tells you the market's mood beyond just the index level. A rising market",
        "led by defensives is FRAGILE; a rising market led by cyclicals is HEALTHY.",
        "",
        f"**Current reading**: {lead_read}",
        f"- Rule: 2 of top-3 sectors cyclical → CYCLICAL, 2 of top-3 defensive → DEFENSIVE, otherwise MIXED",
        "",
        "---",
        "",
        "### 🧭 PHASE CLASSIFICATION",
        "",
        f"Combining the 3 signals → **{color} {phase}**",
        "",
        "**Full framework table** (bold shows current phase):",
        "",
        "| Credit | Trend | Leadership | Phase | Interpretation |",
        "|---|---|---|---|---|",
    ]

    phase_rows = [
        (("LOOSE", "BULL", "CYCLICAL"), "🟢 Mid Expansion", "Ride the trend. Fully invested."),
        (("LOOSE", "BULL", "DEFENSIVE"), "🟡 Early Recovery", "Contrarian — rotate to cyclicals early."),
        (("LOOSE", "BULL", "MIXED"), "🟢 Early Expansion", "Momentum building. Add to leaders."),
        (("LOOSE", "BEAR", "CYCLICAL"), "🟢 Bottom", "High-conviction long entry. Multi-year gains often start here."),
        (("LOOSE", "BEAR", "DEFENSIVE"), "🟡 Stabilization", "Credit turning but market lagging — wait."),
        (("LOOSE", "BEAR", "MIXED"), "🟡 Stabilization", "Wait for clearer leadership."),
        (("TIGHT", "BULL", "CYCLICAL"), "🟠 Late Cycle", "Dangerous. Trim positions, raise cash."),
        (("TIGHT", "BULL", "DEFENSIVE"), "🔴 Distribution", "Peak forming. Aggressive rotation to defensives."),
        (("TIGHT", "BULL", "MIXED"), "🟠 Late Cycle", "Reduce risk."),
        (("TIGHT", "BEAR", "CYCLICAL"), "🔴 Decline", "Early bear. Cut cyclicals sharply."),
        (("TIGHT", "BEAR", "DEFENSIVE"), "🔴 Bear", "Capital preservation. 50%+ cash."),
        (("TIGHT", "BEAR", "MIXED"), "🔴 Bear", "Capital preservation. Wait for credit loosening."),
        (("NEUTRAL", "BULL", "CYCLICAL"), "🟡 Mid Expansion*", "Stay invested but no leverage."),
        (("NEUTRAL", "BULL", "DEFENSIVE"), "🟡 Transition", "Rotate half cyclical → defensive."),
        (("NEUTRAL", "BEAR", "CYCLICAL"), "🟡 Stabilization", "Bear rally likely; wait."),
        (("NEUTRAL", "BEAR", "DEFENSIVE"), "🟠 Bear", "Defensive positioning."),
    ]
    for row_key, name, interp in phase_rows:
        c, t, l = row_key
        is_current = row_key == key
        row_str = f"| {'**' if is_current else ''}{c.title()}{'**' if is_current else ''} | {'**' if is_current else ''}{t.title()}{'**' if is_current else ''} | {'**' if is_current else ''}{l.title()}{'**' if is_current else ''} | {'**' if is_current else ''}{name}{'**' if is_current else ''} | {'👉 ' if is_current else ''}{interp} |"
        lines.append(row_str)

    lines += [
        "",
        "---",
        "",
        "### 💼 POSITIONING RECOMMENDATION",
        "",
        f"**{positioning}**",
        "",
        f"**Suggested cash range**: {_cash_range_from_phase(phase)}",
        "",
        f"**Sector moves**: {sector_moves_line}",
        "",
        "**How to execute**:",
        "1. Check your current portfolio at `/portfolio` — is your cash/equity mix aligned with this phase?",
        "2. If overweight cyclicals in a DISTRIBUTION/BEAR phase → trim aggressively.",
        "3. If underweight cyclicals in a BOTTOM/EARLY RECOVERY → deploy cash into leading sectors from `get_sector_rotation`.",
        "4. Do NOT flip 100% based on one reading. Confirm with 2-3 weeks of consistent signals before major moves.",
        "",
        "---",
        "",
        "### ⚠️ LIMITATIONS & CAVEATS",
        "",
        "- **Single-signal model**: Real cycles have false transitions. Signals often disagree for weeks before resolving.",
        "- **Lookback**: MA200 needs 200+ trading days of data. Sector RS needs ≥3 months per sector.",
        "- **Credit is annual proxy**: Uses top-5 banks — smaller banks and non-bank credit not captured.",
        "- **VN-specific**: Framework calibrated for VN market's cyclical structure. Not directly applicable to other emerging markets.",
        "- **Confirmation bias risk**: If your current portfolio matches the recommended phase, don't just nod — re-check whether you'd take these actions if starting fresh.",
        "",
        "**Best practice**: Run this weekly. Note the phase in your journal. Only act on transitions after 2-3 weeks of consistent readings.",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


_MACRO_KEY_METRIC_PATTERNS = [
    ("GDP",           [r"GDP.{0,40}?[0-9]+[.,][0-9]+\s*%", r"tăng trưởng.{0,30}?[0-9]+[.,][0-9]+\s*%"]),
    ("CPI",           [r"CPI.{0,40}?[0-9]+[.,][0-9]+\s*%", r"lạm phát.{0,30}?[0-9]+[.,][0-9]+\s*%"]),
    ("M2 / credit",   [r"M2.{0,40}?[0-9]+[.,][0-9]+\s*%", r"tín dụng.{0,40}?[0-9]+[.,][0-9]+\s*%", r"credit growth.{0,40}?[0-9]+[.,][0-9]+\s*%"]),
    ("Interest rate", [r"lãi suất.{0,40}?[0-9]+[.,][0-9]+\s*%", r"policy rate.{0,40}?[0-9]+[.,][0-9]+\s*%"]),
    ("USD/VND",       [r"USD/VND.{0,20}?[0-9][0-9,\.]+", r"tỷ giá.{0,30}?[0-9][0-9,\.]+"]),
    ("FDI",           [r"FDI.{0,40}?[0-9][0-9,\.]+\s*(tỷ|billion|USD)"]),
    ("Trade",         [r"xuất khẩu.{0,40}?[0-9][0-9,\.]+\s*(tỷ|billion|USD)", r"cán cân.{0,30}?[0-9][0-9,\.]+"]),
]


def _extract_macro_highlights(text: str, max_per_metric: int = 3) -> dict[str, list[str]]:
    import re
    highlights: dict[str, list[str]] = {}
    for label, patterns in _MACRO_KEY_METRIC_PATTERNS:
        matches: list[str] = []
        for pat in patterns:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                snippet = m.group(0).strip()
                if snippet not in matches:
                    matches.append(snippet)
                if len(matches) >= max_per_metric:
                    break
            if len(matches) >= max_per_metric:
                break
        if matches:
            highlights[label] = matches
    return highlights


def _detect_report_sections(text: str) -> list[str]:
    """Best-effort: return headings that look like macro-report sections."""
    import re
    section_keywords = [
        "kinh tế vĩ mô", "vĩ mô", "GDP", "lạm phát", "CPI", "tỷ giá", "lãi suất",
        "tín dụng", "chính sách tiền tệ", "xuất khẩu", "nhập khẩu", "FDI",
        "thị trường chứng khoán", "triển vọng", "khuyến nghị", "rủi ro",
        "macro", "outlook", "monetary policy", "fiscal", "inflation",
    ]
    lines = text.split("\n")
    hits: list[str] = []
    seen: set[str] = set()
    for line in lines:
        s = line.strip()
        if not (6 < len(s) < 120):
            continue
        low = s.lower()
        if any(kw in low for kw in section_keywords):
            key = low
            if key not in seen:
                seen.add(key)
                hits.append(s)
        if len(hits) >= 15:
            break
    return hits


@register_tool(
    name='load_macro_report',
    description='Load and read a Vietnam macro analysis report (broker macro PDF, SBV/GSO policy paper). Extracts text via pymupdf, returns a structured markdown preview showing detected sections and key numeric mentions (GDP/CPI/M2/exchange rate figures). Optionally saves to `knowledge/sources/macro/` for the knowledge base. For scanned PDFs with no text layer, use `load_financial_pdf` instead for visual reading.',
    input_schema={'type': 'object', 'properties': {'source': {'type': 'string', 'description': 'HTTPS URL or local absolute path to the macro report PDF.'}, 'save': {'type': 'boolean', 'description': 'If true, ingest the extracted text into knowledge/sources/macro/ (default false).', 'default': False}, 'broker': {'type': 'string', 'description': "Broker/publisher name (e.g. 'SSI Research', 'VCBS', 'Mirae Asset', 'BVSC').", 'default': ''}, 'title': {'type': 'string', 'description': 'Report title override. If empty, extracted from PDF metadata.', 'default': ''}, 'language': {'type': 'string', 'enum': ['vi', 'en'], 'default': 'vi', 'description': 'Report language (default vi for Vietnamese broker reports).'}}, 'required': ['source']},
)
async def _load_macro_report(args: dict) -> list[types.TextContent]:
    from pathlib import Path as _P
    source: str = args["source"]
    save: bool = bool(args.get("save", False))
    broker: str = str(args.get("broker", "")).strip()
    title_override: str = str(args.get("title", "")).strip()
    language: str = str(args.get("language", "vi")).lower() or "vi"

    # ── 1. Fetch bytes ───────────────────────────────────────────────
    if source.startswith("http://") or source.startswith("https://"):
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            try:
                resp = await client.get(source)
                resp.raise_for_status()
                pdf_bytes = resp.content
            except Exception as e:
                return [types.TextContent(type="text", text=f"Failed to download {source}: {e}")]
    else:
        p = _P(source)
        if not p.exists():
            return [types.TextContent(type="text", text=f"File not found: {source}")]
        pdf_bytes = p.read_bytes()

    if not pdf_bytes:
        return [types.TextContent(type="text", text="Empty PDF payload.")]

    # ── 2. Extract text via pymupdf ──────────────────────────────────
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        return [types.TextContent(type="text", text=f"Failed to open PDF: {e}")]

    page_count = doc.page_count
    pages_text: list[str] = []
    for page in doc:
        pages_text.append(page.get_text("text"))
    full_text = "\n\n".join(t.strip() for t in pages_text if t.strip())
    doc.close()

    if not full_text or len(full_text) < 200:
        return [types.TextContent(type="text", text=(
            f"PDF has {page_count} pages but only {len(full_text)} chars of extractable text — "
            "likely a scanned/image-only report. Use `load_financial_pdf` for visual reading instead."
        ))]

    # ── 3. Infer title if not provided ───────────────────────────────
    inferred_title = title_override
    if not inferred_title:
        first_lines = [ln.strip() for ln in full_text.split("\n")[:20] if 8 < len(ln.strip()) < 200]
        inferred_title = first_lines[0] if first_lines else "Macro Report"

    # ── 4. Extract highlights ────────────────────────────────────────
    highlights = _extract_macro_highlights(full_text)
    sections = _detect_report_sections(full_text)

    # ── 5. Save to knowledge base if requested ───────────────────────
    saved_path: str | None = None
    if save:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from knowledge.pipelines._common import (
                load_manifest, save_manifest, already_ingested, content_hash,
                write_source, manifest_entry,
            )
            chash = content_hash(full_text)
            manifest = load_manifest()
            if already_ingested(manifest, chash):
                saved_path = "(already in knowledge base — content hash match)"
            else:
                from datetime import date as _d
                display_source = broker or "Macro Report"
                sid, out_path = write_source(
                    category="macro",
                    source_name=display_source,
                    title=inferred_title,
                    body=full_text,
                    pub_date=_d.today().isoformat(),
                    language=language,
                    doc_type="pdf",
                    extra={"pages": page_count, "size_bytes": len(pdf_bytes)},
                )
                manifest["ingested"][sid] = manifest_entry(
                    source_name=display_source,
                    source_url=source if source.startswith("http") else "",
                    url=source if source.startswith("http") else "",
                    title=inferred_title,
                    pub_date=_d.today().isoformat(),
                    chash=chash,
                    path=out_path,
                    category="macro",
                    doc_type="pdf",
                )
                save_manifest(manifest)
                saved_path = str(out_path.relative_to(Path(__file__).parent))
        except Exception as e:
            saved_path = f"Save failed: {e}"

    # ── 6. Build preview response ────────────────────────────────────
    preview_len = 2500
    text_preview = full_text[:preview_len]
    if len(full_text) > preview_len:
        text_preview += f"\n\n*... (truncated at {preview_len} chars; full text {len(full_text)} chars)*"

    lines = [
        f"## Macro Report Loaded",
        f"**Title:** {inferred_title}",
        f"**Source:** {broker or 'Unspecified'} · **Pages:** {page_count} · **Chars:** {len(full_text):,} · **Language:** {language}",
    ]
    if saved_path:
        lines.append(f"**Saved to:** `{saved_path}`")
    lines.append("")

    if highlights:
        lines += ["### Key Metric Mentions", ""]
        for label, matches in highlights.items():
            lines.append(f"- **{label}**: " + "; ".join(matches))
    else:
        lines.append("*No obvious macro metric patterns detected — read text below.*")

    if sections:
        lines += ["", "### Detected Section Headings", ""]
        lines += [f"- {s}" for s in sections[:12]]

    lines += [
        "",
        "### Text Preview",
        "```",
        text_preview,
        "```",
        "",
        "### Next Steps",
        "- Read the full text above and identify the report's stance on M2 / credit / rates / VND",
        "- Cross-check quoted figures against `get_vn_macro_indicators` and `get_money_supply` for reconciliation",
        "- If `save=true` was used, this report is now in the knowledge base — future `list_macro_reports` will surface it",
    ]

    return [types.TextContent(type="text", text="\n".join(lines))]


_MASVN_MACRO_CAT_ID = 28


@register_tool(
    name='fetch_macro_reports',
    description='Fetch the latest VN broker macro reports from public broker portals. Currently supports Mirae Asset Securities Vietnam (masvn) — their macro/strategy reports (Kinh tế Việt Nam, Triển vọng thị trường, Đánh giá xu hướng). Returns a list with title, date, PDF URL. Pair with `load_macro_report(source=<pdf_url>, save=true)` to ingest into the knowledge base.',
    input_schema={'type': 'object', 'properties': {'broker': {'type': 'string', 'enum': ['masvn'], 'default': 'masvn', 'description': 'Broker source (currently only Mirae Asset supported).'}, 'limit': {'type': 'integer', 'default': 10, 'description': 'Max reports to return (default 10).'}}, 'required': []},
)
async def _fetch_macro_reports(args: dict) -> list[types.TextContent]:
    broker = str(args.get("broker", "masvn")).lower()
    limit = int(args.get("limit", 10))

    if broker != "masvn":
        return [types.TextContent(type="text", text=f"Broker '{broker}' not supported yet. Available: masvn (Mirae Asset).")]

    url = (
        f"https://masvn.com/api/categories/fe/{_MASVN_MACRO_CAT_ID}/article"
        f"?paging=1&sort=published_at&direction=desc&active=1&page=1&limit={limit}"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.masvn.com",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        return [types.TextContent(type="text", text=f"Failed to fetch MASVN feed: {e}")]

    def _parse_title(raw) -> dict:
        if isinstance(raw, str):
            try: return json.loads(raw)
            except json.JSONDecodeError: return {"vi": raw}
        return raw or {}

    items = payload.get("data", [])
    total = payload.get("total", 0)

    if not items:
        return [types.TextContent(type="text", text=f"No macro reports returned from Mirae Asset (total={total}).")]

    lines = [
        f"## Latest Macro Reports — Mirae Asset Securities Vietnam",
        f"*Category id {_MASVN_MACRO_CAT_ID} · {total} total published · showing {min(len(items), limit)}*",
        "",
        "| Date | Title | PDF |",
        "|---|---|---|",
    ]
    for item in items[:limit]:
        title_obj = _parse_title(item.get("title", {}))
        title_vi = title_obj.get("vi") or title_obj.get("en") or "(untitled)"
        pub = str(item.get("published_at", ""))[:10]
        file_path = item.get("file_path")
        pdf_url = f"https://www.masvn.com{file_path}" if file_path else ""
        pdf_cell = f"[Load]({pdf_url})" if pdf_url else "—"
        lines.append(f"| {pub} | {title_vi[:90]} | {pdf_cell} |")

    lines += [
        "",
        "### Next Steps",
        "- Copy a PDF URL above and call `load_macro_report(source=<url>, save=true, broker='Mirae Asset', title=<title>)`",
        "- Or use the `/macro` UI page's 'Fetch from broker' section for one-click load-and-save",
        "",
        "*Data source: masvn.com public API. Reports may require Vietnamese language capability to read.*",
    ]
    return [types.TextContent(type="text", text="\n".join(lines))]


@register_tool(
    name='list_macro_reports',
    description='List macro reports saved in `knowledge/sources/macro/` — the persistent library of ingested Vietnam macro analyses. Returns metadata (title, broker, pub_date, path) sorted by ingestion date. Use to browse existing knowledge before requesting a fresh read.',
    input_schema={'type': 'object', 'properties': {'limit': {'type': 'integer', 'default': 20, 'description': 'Max number of reports to return (default 20).'}}, 'required': []},
)
async def _list_macro_reports(args: dict) -> list[types.TextContent]:
    limit = int(args.get("limit", 20))
    macro_dir = Path(__file__).parent / "knowledge" / "sources" / "macro"
    if not macro_dir.exists():
        return [types.TextContent(
            type="text",
            text="No macro reports library yet. Load one with `load_macro_report(source=..., save=true)` to start."
        )]

    files = sorted(macro_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    if not files:
        return [types.TextContent(type="text", text="Macro library is empty. Use `load_macro_report(save=true)` to populate.")]

    lines = [f"## Saved Macro Reports ({len(files)})", "", "| Date | Broker | Title | Path |", "|---|---|---|---|"]
    for f in files:
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            continue
        # Parse minimal frontmatter
        meta: dict[str, str] = {}
        if content.startswith("---"):
            end = content.find("---", 3)
            if end > 0:
                for line in content[3:end].split("\n"):
                    if ":" in line:
                        k, _, v = line.partition(":")
                        meta[k.strip()] = v.strip().strip('"')
        title = meta.get("title", f.stem)[:60]
        broker = meta.get("source_name") or meta.get("source", "—")
        pub_date = meta.get("pub_date", "")[:10] or "—"
        rel = f.relative_to(Path(__file__).parent)
        lines.append(f"| {pub_date} | {broker} | {title} | `{rel}` |")

    return [types.TextContent(type="text", text="\n".join(lines))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
