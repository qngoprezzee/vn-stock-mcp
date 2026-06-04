# VN Stock Financial Analysis MCP Server

An MCP (Model Context Protocol) server for **Claude Code** that turns Claude into an institutional-grade Vietnamese stock analyst. No Anthropic API key needed — runs entirely through your Claude subscription.

## Features

- **25 MCP tools** spanning data, news, analysis, valuation, risk management, journaling, and performance review
- **6 specialized agent skills** with expert personas, trigger routing, and anti-patterns
- **End-to-end investing discipline** — covers all 4 phases of a structured investing curriculum (foundation → analytical → execution & risk → mastery)
- **Web UI for non-technical users** — Next.js dashboard for screening, sizing, and performance review (see [web/README.md](web/README.md))
- **Multimodal PDF reading** — loads scanned Vietnamese financial reports visually
- **Rate-limit resilient** — subprocess isolation + file-based response cache (24h for statements, 5min for prices)
- Covers all Vietnamese exchanges: **HOSE, HNX, UPCOM**

📖 **[Read the User Guide](GUIDE.md)** for workflows, position lifecycle, and prompt examples.
🌐 **[Web UI docs](web/README.md)** to run the Next.js frontend.

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
| `vn-risk-manager` | "size this position", "write a thesis", "review my performance" |

## MCP Tools (25)

Grouped by what they do:

**Data & overview** — `get_stock_overview`, `get_financial_data`, `get_market_overview`, `get_macro_data`, `get_commodity_prices`, `get_vn_macro_indicators`, `get_foreign_flow`

**News & catalysts** — `fetch_broker_news`, `get_market_news`, `get_economy_news`

**Analysis & valuation** — `get_analysis_prompt`, `get_technical_analysis`, `compare_stocks`, `get_dcf_valuation`, `get_earnings_quality`, `get_quality_score`, `load_financial_pdf`

**Risk & portfolio** — `get_position_sizing`, `stress_test_portfolio`, `manage_watchlist`, `check_watchlist`

**Journaling & review** — `save_analysis`, `save_investment_thesis`, `save_decision_log`, `review_performance`

For full descriptions of each tool, see [CLAUDE.md](CLAUDE.md). For workflow examples and prompt patterns, see [GUIDE.md](GUIDE.md).

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
