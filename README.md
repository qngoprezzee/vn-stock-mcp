# VN Stock Financial Analysis MCP Server

An MCP (Model Context Protocol) server for **Claude Code** that turns Claude into an institutional-grade Vietnamese stock analyst. No Anthropic API key needed — runs entirely through your Claude subscription.

## Features

- **8 MCP tools** covering fundamentals, technicals, news, PDF reports, and peer comparison
- **5 specialized agent skills** with expert personas, trigger routing, and anti-patterns
- **Multimodal PDF reading** — loads scanned Vietnamese financial reports visually
- **Rate-limit resilient** — subprocess isolation prevents vnstock `sys.exit()` crashes
- Covers all Vietnamese exchanges: **HOSE, HNX, UPCOM**

## Quick Start

```bash
git clone https://github.com/qngoprezzee/vn-stock-mcp
cd vn-stock-mcp
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
claude mcp add vn-stock-mcp $(pwd)/.venv/bin/python -- $(pwd)/server.py
```

Then in Claude Code:
```
analyze FPT and compare with CMG and VGI
```

## Agent Skills

| Skill | Trigger |
|---|---|
| `vn-equity-analyst` | "analyze FPT", "is VNM a good buy?", "deep dive HPG" |
| `vn-technical-analyst` | "RSI on FPT", "is it oversold?", "entry point for VNM" |
| `vn-portfolio-manager` | "compare FPT vs VNM vs MWG", "best tech stock?" |
| `vn-news-analyst` | "any news on FPT?", "when is the dividend?", "insider activity" |
| `vn-report-reader` | `analyze /path/to/annual_report.pdf` |

## MCP Tools

| Tool | Description |
|---|---|
| `get_analysis_prompt` | 10-section expert research framework |
| `get_technical_analysis` | MA, RSI, MACD, Bollinger Bands, ATR, support/resistance |
| `fetch_broker_news` | Analyst consensus, events, insider trades, news |
| `compare_stocks` | Side-by-side peer comparison table |
| `get_financial_data` | Income statement, balance sheet, cash flow |
| `get_stock_overview` | Price, 52W range, market cap, analyst rating |
| `load_financial_pdf` | Multimodal reading of scanned PDF reports |
| `save_analysis` | Persist analysis as Markdown with auto-index |

## Data Sources

- **vnstock** (VCI source) — financial data, price history, company info
- **FiinGroup** (via vnstock) — news aggregation from SSI, TCBS, Mirae Asset, VCBS
- **User-supplied PDFs** — broker research reports via `load_financial_pdf`

## Requirements

- Python 3.10+
- Claude Code with an active Claude subscription
- macOS / Linux (tested on macOS 15)

## Rate Limits

vnstock guest tier: 20 requests/minute. The server auto-retries after 65s.
For heavier use, register at [vnstocks.com](https://vnstocks.com) for the Community tier (60 req/min).
