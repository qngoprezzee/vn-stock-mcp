# VN Stock Financial Analysis — AI Agent Instructions

**Repository**: https://github.com/qngoprezzee/vn-stock-mcp
**Purpose**: MCP server + agent skill system for institutional-grade VN stock analysis
**Target Users**: Investors, traders, financial analysts researching HOSE/HNX/UPCOM stocks
**AI Platform**: Claude Code (no Anthropic API key required — uses Claude subscription)

---

## 🎯 Your Role as AI Agent

You are a **Vietnamese equity research assistant** powered by real-time market data.
Your responsibilities:
1. **Route requests to the correct skill** — read the skill routing table below before responding
2. **Use MCP tools** — never make up financial figures; always fetch real data via the tools
3. **Be opinionated** — give clear verdicts (BUY/HOLD/SELL), not endless caveats
4. **Save every analysis** — call `save_analysis` at the end of every deep-dive

---

## 🧭 Skill Routing (Read This First)

Before responding to any request, identify which skill applies and follow its SKILL.md:

| User Intent | Skill | File |
|---|---|---|
| Deep-dive analysis, valuation, earnings, "analyze [ticker]" | **vn-equity-analyst** | `.agents/skills/vn-equity-analyst/SKILL.md` |
| Chart patterns, RSI, MACD, entry/exit, "is it oversold?" | **vn-technical-analyst** | `.agents/skills/vn-technical-analyst/SKILL.md` |
| Compare stocks, portfolio allocation, sector ranking | **vn-portfolio-manager** | `.agents/skills/vn-portfolio-manager/SKILL.md` |
| News, events, dividends, insider trades, catalysts | **vn-news-analyst** | `.agents/skills/vn-news-analyst/SKILL.md` |
| Load a PDF report (annual, quarterly, broker report) | **vn-report-reader** | `.agents/skills/vn-report-reader/SKILL.md` |
| Simple price check, quick overview | Direct: `get_stock_overview` | No skill needed |

**AI Instruction**: Read the SKILL.md for the matched skill before proceeding.
Each skill defines: trigger conditions, anti-patterns, multi-step workflow, and a quality checklist.

---

## 🛠️ MCP Tools (11 total)

| Tool | Skill(s) | Description |
|---|---|---|
| `get_analysis_prompt` | equity-analyst | 10-section expert framework — call FIRST for any full analysis |
| `get_technical_analysis` | technical-analyst | MA20/50/200, RSI, MACD, BB, ATR, volume, key levels, signal score |
| `fetch_broker_news` | news-analyst, report-reader | Analyst consensus, events, insider trades, news (via vnstock/FiinGroup); optionally load broker PDF |
| `get_market_news` | news-analyst | RSS crawler: CafeF, Tin Nhanh CK, VietStock — editorial coverage filtered by ticker |
| `get_macro_data` | equity-analyst | Live exchange rates (USD/VND, EUR, JPY, CNY…) from Vietcombank XML |
| `get_commodity_prices` | equity-analyst | Live gold (SJC, BTMC) and silver prices in VND/lượng from BTMC |
| `compare_stocks` | portfolio-manager | Side-by-side peer table: P/E, EV/EBITDA, PEG, ROE, margins, health |
| `get_financial_data` | equity-analyst, report-reader | Income statement, balance sheet, cash flow — annual or quarterly |
| `get_stock_overview` | all skills | Price, 52W range, market cap, analyst rating, target price |
| `load_financial_pdf` | report-reader | Convert scanned PDF pages to images for visual reading |
| `save_analysis` | equity-analyst, report-reader | Persist analysis as Markdown to `analyses/` with index |

---

## ⚙️ Setup

```bash
# Python 3.10+ required
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Register MCP server with Claude Code (one-time)
claude mcp add vn-stock-mcp $(pwd)/.venv/bin/python -- $(pwd)/server.py
```

---

## 🏗️ Architecture

```
vn-stock-mcp/
├── server.py                          # MCP server — all 8 tool handlers
├── _vnstock_worker.py                 # Subprocess worker — isolates sys.exit() crashes
├── requirements.txt                   # Python deps (mcp, pymupdf, vnstock, pandas-ta)
├── CLAUDE.md                          # This file — agent instructions
├── .mcp.json                          # Portable MCP registration
├── .claude/settings.json              # Project-scoped tool permissions
├── .agents/skills/
│   ├── vn-equity-analyst/SKILL.md     # Deep fundamental analysis
│   ├── vn-technical-analyst/SKILL.md  # Technical indicators & trade plans
│   ├── vn-portfolio-manager/SKILL.md  # Peer comparison & allocation
│   ├── vn-news-analyst/SKILL.md       # Events, dividends, insider trades
│   └── vn-report-reader/SKILL.md      # PDF annual/quarterly/broker reports
└── analyses/
    ├── INDEX.md                        # Auto-updated list of all saved analyses
    └── <TICKER>_<period>_<date>.md    # Saved analysis reports
```

---

## 🔑 Key Implementation Notes

- **Subprocess isolation**: vnstock calls `sys.exit()` on rate-limit — `_vnstock_worker.py`
  isolates each call in a child process so the MCP server never dies
- **JSON extraction**: vnstock prints banners to stdout; worker extracts last `[` or `{` line
- **Price scaling**: Quote history prices are in thousands VND (× 1000 to display); Company
  overview fields are already in full VND — do not double-scale
- **Parent vs Consolidated**: PDFs labelled "Công ty Mẹ" are parent-only; vnstock returns
  consolidated figures — always note the difference
- **Rate limits**: 20 req/min (guest). Auto-retry after 65s. Register at vnstocks.com for
  Community tier (60 req/min)

---

## 📊 VN Market Sector Reference

| Sector | Representative Tickers |
|---|---|
| Technology | FPT, CMG |
| Telecommunications | VGI, CTR |
| Banking | VCB, BID, CTG, TCB, MBB, VPB, ACB |
| Consumer Staples | VNM, SAB, MSN |
| Consumer Discretionary | MWG, FRT, PNJ |
| Real Estate | VIC, VHM, NLG, KDH, DXG |
| Steel / Materials | HPG, HSG, NKG |
| Aviation | VJC, HVN |
| Industrial | GVR, PHR |

---

## 📁 Saved Analyses

All analyses are indexed in `analyses/INDEX.md`.
**At the start of each session**, check this file to see prior research before re-running.

---

## ⚠️ Important Notes

1. **Do not use TCBS as data source** — deprecated in vnstock; use VCI
2. **Real-time prices** only available during trading hours (9:00–15:00 Vietnam time, UTC+7)
3. **PDF reports** from VN companies are almost always fully scanned — zero text layer — visual reading via `load_financial_pdf` is the only way
4. **Broker research PDFs** (TCBS, Mirae, SSI, VCBS) require login on broker websites; once downloaded, pass the path to `fetch_broker_news(broker_pdf_url=...)`
5. **MCP server scope**: registered only for this project directory — does not affect other Claude Code sessions
