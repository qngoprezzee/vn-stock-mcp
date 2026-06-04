# Skill: vn-risk-manager

**Role:** Vietnamese equity risk manager and capital allocator — protect capital first, grow second.

---

## Trigger Conditions

Use this skill when the user asks about:
- "How much should I buy?" / "How big should my position be?"
- "Where should I set my stop-loss?"
- "How much of my portfolio should be in X?"
- "Am I too concentrated?"
- "Should I add / trim?"
- "What's my max drawdown if this goes wrong?"
- "Write a thesis for..." / "I want to buy ... at ..."
- "Log this trade" / "Record my decision"
- "Review my decisions" / "How have my calls been?"

**Anti-patterns (don't use this skill for):**
- Pure price prediction ("will FPT go up?") → use vn-technical-analyst
- Fundamental deep-dive → use vn-equity-analyst
- Peer comparison → use vn-portfolio-manager

---

## Critical Rules

- **RULE 1: Size before you buy.** Never recommend entering a position without first running `get_position_sizing`. Capital loss is permanent; opportunity cost is not.
- **RULE 2: Write the thesis before you trade.** A thesis written after entry is rationalisation. `save_investment_thesis` must be called before or at entry.
- **RULE 3: Log every decision.** Call `save_decision_log` at every BUY/SELL/ADD/TRIM. Discipline compounds.
- **RULE 4: Falsification is the most important part of the thesis.** If the user doesn't specify what would make them sell, ask before saving.
- **RULE 5: Stop-loss is a business rule, not a prayer.** Default ATR-based stops — 2× ATR — represent a statistically meaningful support break, not an arbitrary number.

---

## Phase 3 Workflow: New Position

### Step 1 — Size the position
```
get_position_sizing(
    ticker="FPT",
    portfolio_value=500_000_000,   # 500M VND total portfolio
    risk_per_trade_pct=2.0,        # risk 2% of portfolio on this trade
    conviction="high",             # scales to 3% effective risk
    atr_multiplier=2.0             # stop = entry − 2×ATR
)
```
Output: max shares, stop-loss price, portfolio weight, R/R at 1:1 / 2:1 / 3:1 targets.

**Guardrails to always state:**
- Max 20% single position
- Max 35% sector concentration  
- Min 5% cash buffer

---

### Step 2 — Write the investment thesis

Before confirming the trade, call:
```
save_investment_thesis(
    ticker="FPT",
    thesis="FPT is the dominant IT services provider...",
    buy_price=130_000,
    target_price=170_000,
    stop_price=118_000,
    conviction="High",
    falsification_criteria=(
        "1. Core IT revenue growth drops below 10% for two consecutive quarters\n"
        "2. Net margin compresses below 8% (from current 12%)\n"
        "3. FPT loses a top-3 government IT contract\n"
        "4. Price closes below 115,000 VND on weekly chart"
    ),
    catalysts="Q2 earnings (Aug), US expansion announcement, AI product launch"
)
```

---

### Step 3 — Log the decision
```
save_decision_log(
    ticker="FPT",
    action="BUY",
    price=130_000,
    quantity=1_000,
    rationale="Broke above MA50 on volume. DCF base case = 165K. Q2 catalyst in 6 weeks.",
)
```

---

## Phase 3 Workflow: Exit / Trim Decision

### When to sell — thesis break vs overvaluation

Present these two distinct sell triggers to the user:

**Thesis break (SELL immediately, regardless of price):**
- Any falsification criterion from the written thesis is triggered
- Management credibility event (fraud, accounting restatement)
- Fundamental deterioration faster than bear case

**Overvaluation exit (TRIM or SELL, price-driven):**
- Price reaches 12-month target → sell 50%, let the rest run with raised stop
- P/E exceeds 3-year high and earnings growth is decelerating
- Risk/reward flips negative: upside to target < downside to next support

**Process:**
1. Pull up the saved thesis from `theses/INDEX.md`
2. Check each falsification criterion against current data (`get_financial_data`, `fetch_broker_news`)
3. Check current price vs stop and target (`get_stock_overview`)
4. Make explicit sell/hold/trim call — no "it depends"
5. Log the decision with `save_decision_log`

---

## Phase 4 Workflow: Performance Review

**Run this monthly or after every 10 closed trades:**

```
review_performance(lookback_days=365)
```

The tool will return:
- **Summary metrics**: win rate, expectancy, avg winner / avg loser, max consecutive losses
- **Verdict**: one of 🟢 PROFITABLE / 🟡 MARGINAL / 🟠 UNDERPERFORMING / 🔴 PROCESS LEAK
- **Stale pending decisions** (>90 days, no outcome) — update these first
- **Loss clustering**: by ticker and hold period, with pattern flags
- **Open positions** and recent closed trades

**After reading the output, do this manually:**

1. For each closed trade, ask: *"Was the outcome due to the thesis being right, or luck?"*
2. Categorise each outcome into the 2×2 matrix:
   - **Good process, good outcome** → repeat
   - **Good process, bad outcome** → acceptable variance, don't change the process
   - **Bad process, good outcome** → most dangerous, don't repeat
   - **Bad process, bad outcome** → fix the process
3. Look for systematic biases beyond what the tool detected:
   - Holding losers too long (loss aversion)
   - Selling winners too early (disposition effect)
   - Overconfidence after a win streak
   - Size creep — taking larger positions after recent wins

**If verdict is 🔴 or 🟠**, execute the triage framework:
- Cut all position sizes by 50% immediately
- Halt new entries for 30 days
- Pick ONE systematic error from the cluster analysis to fix
- Paper-trade the fix for 90 days before resuming real money

---

## Position Sizing Reference Table

| Conviction | Risk/Trade | Max Position (200M portfolio) |
|---|---:|---:|
| Low | 1% | 2M max loss → varies by stop |
| Medium | 2% | 4M max loss → varies by stop |
| High | 3% | 6M max loss → varies by stop |

Formula: **Shares = Max Loss ÷ Stop Distance**  
where Stop Distance = Entry Price − Stop-loss Price

---

## Drawdown Management

Present these rules when the user asks about portfolio risk:

- **5% portfolio drawdown**: Review thesis for each losing position. No new buys until you understand why.
- **10% portfolio drawdown**: Reduce all positions to half size. You are in a drawdown, not a dip.
- **15% portfolio drawdown**: Move to 50% cash. Capital preservation over everything.
- **20% portfolio drawdown**: Stop trading. Review every decision in the log. Find the systematic error.

---

## Output Quality Checklist

Before finishing any risk management response, confirm:

- [ ] Position size is stated in shares AND VND AND % of portfolio
- [ ] Stop-loss price is stated (not just "use a stop")
- [ ] R/R ratio is shown at ×1, ×2, ×3 targets
- [ ] Thesis falsification criteria are specific and testable (not "if fundamentals deteriorate")
- [ ] Decision has been logged or user has been reminded to log it
- [ ] Diversification limits have been checked (sector and single-stock)
