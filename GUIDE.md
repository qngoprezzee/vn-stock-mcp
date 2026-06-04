# VN Stock MCP — User Guide

A practical how-to-use guide. For project overview and installation, see [README.md](README.md). For agent routing and tool reference, see [CLAUDE.md](CLAUDE.md).

---

## How this works

You don't call MCP tools directly. You **talk to Claude Code in plain language** and Claude routes your request to the right skill and tool. This guide shows you the prompts that trigger each workflow and the disciplined sequences that compound over time.

The project covers a 4-phase investing curriculum:

| Phase | Focus | Key tools |
|---|---|---|
| **1. Foundation** | Read before you invest | `get_financial_data`, `get_stock_overview`, `get_vn_macro_indicators`, `get_macro_data` |
| **2. Analytical** | Value businesses, not tickers | `get_analysis_prompt`, `get_dcf_valuation`, `get_earnings_quality`, `get_quality_score`, `compare_stocks` |
| **3. Execution & risk** | Deploy capital with discipline | `get_position_sizing`, `save_investment_thesis`, `save_decision_log`, `stress_test_portfolio` |
| **4. Mastery** | Refine your edge | `review_performance`, `check_watchlist`, theses & decision log |

---

## Daily workflow (5 minutes)

Start every session with a market pulse and watchlist scan:

```
What's happening in the VN market today?
```
→ Triggers `get_market_overview` (indices, top movers) + headlines.

```
Check my watchlist
```
→ Triggers `check_watchlist` — surfaces RSI <30/>70, MA50 breaks, >5% moves.

```
Any news on FPT, VCB, and HPG?
```
→ Triggers `get_market_news` for each ticker.

---

## Position lifecycle (the disciplined flow)

This is the core sequence. Every new position should follow it.

### Step 1 — Initial screen

```
Quality score for FPT
```
→ `get_quality_score` returns a 0-100 verdict. Skip stocks below 60 unless you have a specific reason.

```
How does FPT compare to CMG and VGI?
```
→ `compare_stocks` with ROIC, EV/EBITDA, FCF, balance-sheet metrics side-by-side.

### Step 2 — Deep dive (only if screen passes)

```
Do a full equity research analysis on FPT
```
→ Triggers `vn-equity-analyst` skill, which orchestrates ~6 tools and produces a 10-section institutional research note. Auto-saves to `analyses/`.

For PDF-based deep dive:
```
Analyze /Users/me/Downloads/fpt_annual_2025.pdf
```
→ Triggers `vn-report-reader` skill — visually reads the scanned report, cross-references with `get_financial_data`, extracts notes-to-accounts.

### Step 3 — Valuation

```
DCF FPT with 15% base growth and 12% discount rate
```
→ `get_dcf_valuation` returns bull/base/bear intrinsic value with margin of safety.

```
How's FPT's earnings quality?
```
→ `get_earnings_quality` checks FCF vs net income, accruals, working-capital discipline — surfaces accounting-driven profit.

### Step 4 — Foreign signal check

```
What's the foreign flow on FPT?
```
→ `get_foreign_flow` shows current ownership, foreign room remaining, today's net buy/sell.

### Step 5 — Size the position

```
I have 500M VND. How many shares of FPT should I buy with high conviction?
```
→ `get_position_sizing` returns:
- ATR-based stop-loss
- Max shares based on 2% risk × 1.5 (high conviction)
- Portfolio weight (capped at 20%)
- R/R table at 1:1, 2:1, 3:1 targets

### Step 6 — Write the thesis BEFORE you buy

```
Save my thesis for FPT: bought at 130k, target 170k, stop 118k, high conviction.
Falsification: revenue growth <10% for 2Q, or core IT margin compresses below 8%.
Catalysts: Q2 earnings in August, US expansion announcement.
Strongest bias: confirmation bias — I've been bullish on FPT for years.
If wrong in 12 months: most likely reason is US/EU IT services slowdown.
```
→ `save_investment_thesis` writes to `theses/FPT_thesis_<date>.md` with all fields rendered including R/R ratio.

### Step 7 — Execute and log

After placing the order in your broker:
```
Log a BUY of FPT at 130,000 for 1,000 shares: broke above MA50 on volume, DCF base = 165k, Q2 catalyst in 6 weeks
```
→ `save_decision_log` appends to `decisions/LOG.md`.

---

## While holding (weekly review)

```
Check my watchlist
```
Look for technical triggers on holdings.

```
Has the FPT thesis been broken? Compare current state against falsification criteria.
```
→ Claude reads `theses/FPT_thesis_*.md`, fetches fresh data, and reports whether any criterion is triggered.

```
Stress test my portfolio: FPT 1000 shares at 130k, VCB 500 at 80k, HPG 4000 at 25k
```
→ `stress_test_portfolio` runs -10/-20/-30% shocks with sector betas, flags drawdown rules and concentration warnings.

---

## Exit decision

There are only two valid sell reasons. Be explicit about which one:

**Thesis break (sell now, regardless of price):**
```
FPT just announced they lost the Vietcombank contract. Does that break my thesis?
```
→ Claude reads the thesis, confirms which falsification criterion is hit, recommends action.

**Overvaluation exit (price-driven, trim):**
```
FPT hit 170k — my target. What should I do?
```
→ Claude reads the thesis and recommends: typically sell 50%, raise stop on remainder.

Then log it:
```
Log SELL of FPT at 170,000 for 500 shares: hit 12-month target, trimming half
```

---

## Monthly performance review

```
Review my performance for the last 90 days
```
→ `review_performance` returns:
- Win rate, expectancy, avg winner vs avg loser
- Verdict: 🟢 Profitable / 🟡 Marginal / 🟠 Triage / 🔴 Process leak
- Stale pending decisions (>90 days no outcome)
- Loss clustering by ticker and hold period
- Pattern flags (e.g. "many quick losses — momentum chasing")

**If verdict is 🔴 or 🟠**, follow the triage framework:
1. Cut all position sizes by 50%
2. No new positions for 30 days
3. For every losing trade, classify: good-process-bad-outcome (variance, no change) vs. bad-process-bad-outcome (fix the process)
4. Pick ONE systematic error to fix — change one rule
5. Paper-trade the fix for 90 days before resuming

---

## Quarterly deep audit

```
Show me Vietnam's macro indicators
```
→ `get_vn_macro_indicators` — GDP, CPI, real rates, current account from World Bank. Has the regime shifted?

```
Show me all my active theses
```
→ Reads `theses/INDEX.md`. For each, ask: "Does the thesis still hold given current data?"

```
Full performance review for the year, including pattern analysis
```
→ `review_performance` with `lookback_days=365`.

---

## Common prompts by intent

| Intent | Prompt example |
|---|---|
| Quick price check | `What's FPT's current price and 52W range?` |
| Market pulse | `How is the VN market doing today?` |
| Screen for quality | `Quality score for FPT, VNM, HPG, VCB — rank them` |
| Deep research | `Analyze FPT — full institutional research note` |
| Compare peers | `Compare FPT vs CMG vs VGI on fundamentals` |
| Valuation | `DCF for FPT with conservative growth` |
| Earnings quality | `Is FPT's profit backed by cash?` |
| Foreign signal | `Foreign flow on VCB this week` |
| Position sizing | `I want to buy FPT — size the position for 500M portfolio, medium conviction` |
| Write thesis | `Save my thesis for FPT with the following details...` |
| Log a trade | `Log BUY of FPT at 130k for 1000 shares — reasoning: ...` |
| Watchlist scan | `Check my watchlist` |
| Stress test | `Stress test my portfolio of: ...` |
| Performance review | `Review my performance for the last 90 days` |
| Add to watchlist | `Add FPT and VCB to my watchlist` |
| Macro context | `Vietnam macro indicators` |
| Currency / commodity | `What's the USD/VND rate and gold price today?` |
| Economy news | `What's happening in the Vietnamese economy today?` |

---

## Where everything lives

```
vn-stock-mcp/
├── analyses/              # Saved research notes (one per analysis)
│   ├── INDEX.md           # Auto-updated list
│   └── FPT_Q1-2026_2026-05-27.md
├── theses/                # Investment theses with falsification + pre-mortem
│   ├── INDEX.md
│   └── FPT_thesis_2026-06-03.md
├── decisions/
│   └── LOG.md             # Append-only decision journal (BUY/SELL/ADD/TRIM)
├── .watchlist.json        # Your personal ticker watchlist
├── .cache/                # vnstock response cache (24h for statements, 5min for prices)
```

All four are **gitignored** by default (your personal trading data shouldn't be committed). The `INDEX.md` files inside `analyses/` and `theses/` are tracked.

---

## Curriculum phase quick reference

### Phase 1 — Foundation
- `get_stock_overview`, `get_financial_data` — read every line of the statements
- `compare_stocks` — see the ratios (P/E, P/B, EV/EBITDA, ROE, ROIC) in context
- `get_macro_data`, `get_commodity_prices`, `get_vn_macro_indicators` — macro environment

### Phase 2 — Analytical
- `get_analysis_prompt` + `vn-equity-analyst` skill — 10-section framework
- `get_dcf_valuation` — intrinsic value with bull/base/bear
- `get_earnings_quality` — FCF vs accruals, working capital quality
- `get_quality_score` — fast quality screen across many stocks
- `fetch_broker_news` — analyst consensus + corporate events

### Phase 3 — Execution & risk
- `get_position_sizing` — ATR-based sizing before every trade
- `save_investment_thesis` — write before you buy, with pre-mortem
- `save_decision_log` — log every action
- `stress_test_portfolio` — what happens in a -20% market?
- `manage_watchlist` + `check_watchlist` — monitoring discipline

### Phase 4 — Mastery
- `review_performance` — monthly verdict + triage
- Read `theses/` regularly — track which still hold
- Quarterly audit: aggregate decisions, find systematic errors

---

## Gotchas & limits

- **VN trading hours: 09:00–15:00 VNT (UTC+7)**. Outside these, "current price" is the last close. Foreign flow snapshot from `get_foreign_flow` is only meaningful during market hours.
- **vnstock rate limit: 20 req/min on guest tier.** The cache layer absorbs most of this; financial statements cache for 24h. Heavy peer comparisons may still hit the limit — wait 60s and retry. For heavier use, register at vnstocks.com for the Community tier (60 req/min).
- **PDF reports are scanned.** Vietnamese companies publish PDFs with no text layer. The `load_financial_pdf` tool converts pages to images for visual reading.
- **DCF is highly assumption-sensitive.** Always sanity-check with `compare_stocks` peer multiples. Treat DCF as one input among many.
- **Sector betas in `stress_test_portfolio` are heuristic proxies**, not historical regressions. Use as directional, not precise.
- **World Bank macro data is annual and lagged 1–2 years.** For real-time SBV base rate or monthly CPI, check sbv.gov.vn / gso.gov.vn directly.
- **Foreign flow snapshot is point-in-time.** For historical foreign-flow time series, check cafef.vn or fireant.vn.
- **`compare_stocks` for 8 tickers can take 30–60s** on a cold cache because each ticker fires 5 vnstock subprocesses in parallel.
- **`review_performance` needs ≥5 closed trades** to produce a meaningful verdict. Below that, you're looking at noise.

---

## The disciplined sequence in one diagram

```
Screen          →  Deep dive       →  Value          →  Size           →  Write thesis   →  Execute       →  Log
quality_score      vn-equity-       dcf_valuation     position_sizing    save_investment    (broker)         save_decision_
                   analyst          earnings_quality                     _thesis                             log
                                    foreign_flow

                                                                                                            ↓
Quarterly      ←   Monthly         ←   Weekly         ←   While holding ←   ...
audit              review_              check_              thesis check
                   performance          watchlist           vs falsification
```

This is the loop. Compound it for years.
