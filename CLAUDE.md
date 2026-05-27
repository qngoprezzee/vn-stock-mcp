# VN Stock Financial Analysis MCP Server

A Model Context Protocol (MCP) server for Claude Code that provides
institutional-grade financial analysis tools for Vietnam-listed stocks
(HOSE / HNX / UPCOM). No Anthropic API key required — runs entirely
through your Claude Code subscription.

## Setup

```bash
# Python 3.10+ required
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Register with Claude Code (one-time, already done if you cloned this)
claude mcp add vn-stock-mcp $(pwd)/.venv/bin/python -- $(pwd)/server.py
```

## Tools (8 total)

| Tool | Description |
|---|---|
| `get_analysis_prompt` | Returns a 10-section expert research framework. Call this FIRST when asked to analyze a stock. |
| `get_technical_analysis` | MA20/50/200, RSI, MACD, Bollinger Bands, ATR, volume, support/resistance, overall signal. |
| `fetch_broker_news` | Analyst consensus, corporate events, insider trades, recent news (FiinGroup). Optionally load a broker PDF. |
| `compare_stocks` | Side-by-side peer comparison: P/E, EV/EBITDA, PEG, ROE, margins, health ratios. |
| `get_financial_data` | Income statement, balance sheet, cash flow — annual or quarterly, in billions VND. |
| `get_stock_overview` | Current price, 52W range, market cap, analyst rating, target price. |
| `load_financial_pdf` | Load a scanned PDF (local path or URL) and return pages as images for visual reading. |
| `save_analysis` | Save completed analysis as Markdown to `analyses/` with an auto-updated index. |

## Typical Usage

Ask Claude Code naturally — the `get_analysis_prompt` tool instructs Claude
to gather all data automatically:

```
analyze FPT and compare with CMG and VGI
```

```
analyze FPT using /path/to/FPT_annual_report.pdf
```

```
get a stock overview for VNM
```

```
compare stocks FPT, MWG, VNM
```

Claude will:
1. Call `get_analysis_prompt` for the 10-section framework
2. Fetch overview + financials (annual + quarterly) + news + technical analysis in parallel
3. Run `compare_stocks` against peers
4. Write a full institutional research note
5. Save it to `analyses/<TICKER>_<period>_<date>.md`

## Architecture

```
vn-stock-mcp/
├── server.py              # MCP server — all 8 tool definitions and handlers
├── _vnstock_worker.py     # Subprocess worker — isolates vnstock sys.exit() crashes
├── requirements.txt       # Python dependencies
├── analyses/
│   ├── INDEX.md           # Auto-updated index of all saved analyses
│   └── <TICKER>_*.md      # Saved analysis reports (gitignored)
├── .mcp.json              # Project-level MCP registration (portable)
└── .claude/
    └── settings.json      # Pre-approved tool permissions
```

## Key Implementation Notes

- **Subprocess isolation (`_vnstock_worker.py`):** vnstock's quota library calls
  `sys.exit()` on rate-limit, which would kill the MCP server process. Every
  vnstock call runs in a child subprocess so exits are contained. The server
  retries automatically after 65s.

- **JSON extraction:** vnstock prints promotional banners to stdout alongside
  JSON output. `_vnstock_subprocess()` extracts only the last line starting
  with `[` or `{`.

- **Rate limits:** vnstock guest tier = 20 req/min. Run fetches sequentially
  if hitting limits, or register at vnstocks.com for Community tier (60 req/min).

- **PDF loading:** All VN company financial reports are scanned PDFs (zero text
  layer). `load_financial_pdf` converts pages to 2x-zoom PNG images returned
  as `ImageContent` — Claude reads them visually via multimodal.

- **Prices:** vnstock Quote history returns prices in thousands VND (multiply
  × 1000). Company overview fields are already in full VND — do not scale.

## Data Sources

| Source | Data |
|---|---|
| vnstock VCI | Financial statements, price history, company overview |
| FiinGroup (via vnstock) | News aggregation: SSI, TCBS, Mirae Asset, VCBS disclosures |
| User-supplied PDFs | Broker research reports via `load_financial_pdf` |

## Saved Analyses

All analyses saved to `analyses/` and indexed in `analyses/INDEX.md`.
Read `analyses/INDEX.md` at the start of a session to see prior work.

## VN Market Tickers Reference

| Sector | Key Tickers |
|---|---|
| Technology | FPT, CMG |
| Telecom | VGI, CTR |
| Banking | VCB, BID, CTG, TCB, MBB, VPB, ACB |
| Consumer | VNM, MWG, MSN, SAB |
| Real Estate | VIC, VHM, NLG, KDH |
| Steel/Materials | HPG, HSG, NKG |
| Aviation | VJC, HVN |
