import asyncio
import base64
import io
import json
import subprocess
import sys
import tempfile
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

async def _vnstock_subprocess(func_name: str, kwargs: dict, retries: int = 3) -> str:
    """Run a named vnstock function in an isolated subprocess. Returns JSON string."""
    for attempt in range(retries):
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(_VNSTOCK_HELPER), func_name, json.dumps(kwargs),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            # vnstock prints banners to stdout; extract only the JSON line
            lines = stdout.decode(errors="ignore").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if line.startswith("[") or line.startswith("{"):
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
            name="get_market_news",
            description=(
                "Crawl Vietnamese financial news sites (CafeF, Tin Nhanh Chứng Khoán, NDH) via RSS "
                "and return recent articles that mention the stock ticker. "
                "Complements fetch_broker_news (which pulls from vnstock/FiinGroup) with broader "
                "editorial coverage from independent news outlets. "
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
    elif name == "get_market_news":
        return await _get_market_news(arguments)
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


ANALYSES_DIR = Path(__file__).parent / "analyses"


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


async def _vnstock_call(fn, *args, **kwargs):
    """Run a synchronous vnstock call under the semaphore with one retry on rate-limit."""
    for attempt in range(2):
        async with _vnstock_sem:
            try:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
            except SystemExit:
                if attempt == 0:
                    await asyncio.sleep(15)
                else:
                    raise
            except Exception:
                raise


async def _fetch_metrics_for_ticker(ticker: str, period: str) -> dict:
    """Return a flat dict of key metrics for one ticker. Failures return partial data."""
    from vnstock import Quote, Company, Finance
    from datetime import date
    result = {"ticker": ticker}
    try:
        co = Company(symbol=ticker, source="VCI")
        ov = (await asyncio.get_event_loop().run_in_executor(None, co.overview)).iloc[0]
        def _f(v, default=0.0):
            try:
                return float(v) if v is not None else default
            except (TypeError, ValueError):
                return default

        result.update({
            "name": str(ov.get("organ_short_name") or ov.get("organ_name", ticker)),
            "sector": str(ov.get("sector", "N/A")),
            "market_cap_b": _f(ov.get("market_cap")) / 1e9,
            "current_price": _f(ov.get("current_price")),
            "target_price": _f(ov.get("target_price")),
            "rating": str(ov.get("rating") or "N/A"),
            "52w_high": _f(ov.get("highest_price1_year")),
            "52w_low": _f(ov.get("lowest_price1_year")),
            "foreign_pct": _f(ov.get("foreigner_percentage")) * 100,
        })
    except Exception as e:
        result["overview_error"] = str(e)

    try:
        today = date.today().isoformat()
        q = Quote(symbol=ticker, source="VCI")
        hist = q.history(start="2026-01-01", end=today, interval="1D")
        if not hist.empty:
            result["latest_price"] = float(hist["close"].iloc[-1]) * 1000
    except Exception:
        result["latest_price"] = result.get("current_price", 0)

    try:
        fi = Finance(symbol=ticker, source="VCI")
        inc = fi.income_statement(period=period, lang="en")
        bal = fi.balance_sheet(period=period, lang="en")
        cf = fi.cash_flow(period=period, lang="en")

        def latest(df, item_id):
            row = df[df["item_id"] == item_id] if "item_id" in df.columns else df[df.index == item_id]
            if row.empty:
                return None
            year_cols = [c for c in df.columns if str(c).isdigit() or (isinstance(c, int))]
            if not year_cols:
                return None
            val = row.iloc[0][year_cols[0]]
            try:
                return float(val)
            except Exception:
                return None

        net_sales = latest(inc, "net_sales")
        gross_profit = latest(inc, "gross_profit")
        op_profit = latest(inc, "operating_profit_loss")
        net_profit = latest(inc, "net_profit_loss_after_tax")
        net_profit_parent = latest(inc, "attributable_to_parent_company")
        eps = latest(inc, "eps_basic_vnd")

        total_assets = latest(bal, "total_assets")
        current_assets = latest(bal, "current_assets")
        current_liab = latest(bal, "current_liabilities")
        st_borrow = latest(bal, "short_term_borrowings")
        lt_borrow = latest(bal, "long_term_borrowings")
        equity = latest(bal, "owners_equity")
        cash = latest(bal, "cash_and_cash_equivalents")
        receivables = latest(bal, "accounts_receivable")

        op_cf = latest(cf, "net_cash_inflows_outflows_from_op") or latest(cf, "net_cash_from_operating_activities")
        capex_row = cf[cf["item_id"] == "purchases_of_fixed_assets_and_other"] if "item_id" in cf.columns else cf.iloc[:0]
        capex = float(capex_row.iloc[0][[c for c in cf.columns if str(c).isdigit() or isinstance(c, int)][0]]) if not capex_row.empty else None

        # Compute ratios
        price = result.get("latest_price") or result.get("current_price", 0)
        mktcap = result.get("market_cap_b", 0) * 1e9

        gross_margin = gross_profit / net_sales if net_sales else None
        op_margin = op_profit / net_sales if net_sales else None
        net_margin = net_profit_parent / net_sales if net_sales else None
        roe = net_profit_parent / equity if equity else None
        roa = net_profit / total_assets if total_assets else None
        current_ratio = current_assets / current_liab if current_liab else None
        de_ratio = (st_borrow + (lt_borrow or 0)) / equity if equity else None
        pe = price / eps if eps and eps > 0 else None
        pb = mktcap / equity if equity else None
        ev = mktcap + (st_borrow or 0) + (lt_borrow or 0) - (cash or 0)
        ebitda = op_profit + latest(cf, "depreciation_and_amortization") if op_profit else None
        ev_ebitda = ev / ebitda if ebitda and ebitda > 0 else None
        peg = (pe / (roe * 100)) if pe and roe and roe > 0 else None
        fcf = (op_cf + capex) if op_cf and capex else None

        result.update({
            "net_sales_b": net_sales / 1e9 if net_sales else None,
            "gross_margin_pct": gross_margin * 100 if gross_margin else None,
            "op_margin_pct": op_margin * 100 if op_margin else None,
            "net_margin_pct": net_margin * 100 if net_margin else None,
            "roe_pct": roe * 100 if roe else None,
            "roa_pct": roa * 100 if roa else None,
            "current_ratio": current_ratio,
            "de_ratio": de_ratio,
            "pe": pe,
            "pb": pb,
            "ev_ebitda": ev_ebitda,
            "peg": peg,
            "eps": eps,
            "equity_b": equity / 1e9 if equity else None,
            "fcf_b": fcf / 1e9 if fcf else None,
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


_RSS_FEEDS = [
    ("CafeF - Thị trường CK", "https://cafef.vn/thi-truong-chung-khoan.rss"),
    ("CafeF - Doanh nghiệp",  "https://cafef.vn/doanh-nghiep.rss"),
    ("CafeF - Tài chính NH",  "https://cafef.vn/tai-chinh-ngan-hang.rss"),
    ("CafeF - Đầu tư",        "https://cafef.vn/dau-tu.rss"),
    ("VietStock",             "https://vietstock.vn/830/chung-khoan.rss"),
    ("Tin Nhanh CK",          "https://tinnhanhchungkhoan.vn/rss/"),
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


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
