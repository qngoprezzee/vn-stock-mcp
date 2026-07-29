"""Pure return / correlation / drawdown primitives.

Pure functions — no I/O, no globals. Easy to unit-test.

**Note on _period_return**: server.py currently has two module-level definitions
of this function that shadow each other (line 6356 for snapshot-list-of-dicts,
line 6555 for price-series-of-tuples). This module disambiguates them:
  - `period_return_from_snapshots(snapshots, days_back) -> (float|None, int)`
  - `period_return_from_series(series, trading_days_back) -> float|None`
Callers in the original server.py that used the tuple-return version (only
_get_portfolio_returns) were silently broken; this refactor restores correct
behaviour for that tool.
"""
from __future__ import annotations

from datetime import date, timedelta


# ── Return computation on price series (list of (date, close) tuples) ───────

def parse_price_series(raw: str) -> list[tuple[str, float]]:
    """Return sorted [(date, close)] pairs from a quote_history_full JSON payload."""
    import json
    try:
        rows = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []
    out: list[tuple[str, float]] = []
    for r in rows:
        t = r.get("time")
        c = r.get("close")
        if t and c is not None:
            try:
                out.append((str(t)[:10], float(c)))
            except (TypeError, ValueError):
                continue
    return sorted(out, key=lambda x: x[0])


def period_return_from_series(series: list[tuple[str, float]], trading_days_back: int) -> float | None:
    """Simple return over N trading days from a (date, close) price series."""
    if len(series) < trading_days_back + 1:
        return None
    latest = series[-1][1]
    earlier = series[-trading_days_back - 1][1]
    if earlier <= 0:
        return None
    return latest / earlier - 1


def ytd_return(series: list[tuple[str, float]]) -> float | None:
    """Simple return from first trading day of the year to latest close."""
    if not series:
        return None
    year = series[-1][0][:4]
    ytd_start_str = f"{year}-01-01"
    for date_str, close in series:
        if date_str >= ytd_start_str:
            if close <= 0:
                return None
            return series[-1][1] / close - 1
    return None


# ── Return computation on portfolio snapshots (list of {date, total_value}) ─

def find_snapshot_at_or_before(snapshots: list[dict], target_date: str) -> dict | None:
    candidates = [s for s in snapshots if s.get("date", "") <= target_date]
    return candidates[-1] if candidates else None


def daily_returns_from_snapshots(snapshots: list[dict]) -> list[float]:
    """Daily total-value returns. Cash flows (set_cash) show up as spurious returns."""
    returns: list[float] = []
    for i in range(1, len(snapshots)):
        prev = float(snapshots[i - 1]["total_value"])
        curr = float(snapshots[i]["total_value"])
        if prev > 0:
            returns.append(curr / prev - 1)
    return returns


def period_return_from_snapshots(snapshots: list[dict], days_back: int) -> tuple[float | None, int]:
    """Return (return_pct, actual_days) for the period ending at last snapshot."""
    if len(snapshots) < 2:
        return None, 0
    latest_date_str = snapshots[-1]["date"]
    target = (date.fromisoformat(latest_date_str) - timedelta(days=days_back)).isoformat()
    start = find_snapshot_at_or_before(snapshots, target)
    if not start or start["date"] == latest_date_str:
        return None, 0
    start_v = float(start["total_value"])
    end_v = float(snapshots[-1]["total_value"])
    if start_v <= 0:
        return None, 0
    actual_days = (date.fromisoformat(latest_date_str) - date.fromisoformat(start["date"])).days
    return (end_v / start_v - 1), actual_days


def rolling_drawdown(snapshots: list[dict]) -> tuple[float, float]:
    """Return (max_drawdown_pct, current_drawdown_pct) — both negative or zero."""
    if not snapshots:
        return 0.0, 0.0
    peak = 0.0
    max_dd = 0.0
    for s in snapshots:
        v = float(s["total_value"])
        peak = max(peak, v)
        if peak > 0:
            dd = (v - peak) / peak
            max_dd = min(max_dd, dd)
    curr_v = float(snapshots[-1]["total_value"])
    curr_dd = (curr_v - peak) / peak if peak > 0 else 0.0
    return max_dd, curr_dd


# ── Aggregate metrics ───────────────────────────────────────────────────────

def twr(daily_returns: list[float]) -> float:
    """Time-Weighted Return: geometric linking of daily returns."""
    result = 1.0
    for r in daily_returns:
        result *= (1 + r)
    return result - 1


def annualize(total_return: float, days: int) -> float:
    """Convert a total return over `days` calendar days to CAGR."""
    if days <= 0 or total_return <= -1:
        return 0.0
    return (1 + total_return) ** (365 / days) - 1


def correlation(x: list[float], y: list[float]) -> float | None:
    """Pearson correlation on aligned return series (trim to min length)."""
    n = min(len(x), len(y))
    if n < 10:
        return None
    x, y = x[-n:], y[-n:]
    mx = sum(x) / n
    my = sum(y) / n
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = sum((x[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((y[i] - my) ** 2 for i in range(n)) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def slope_normalized(series) -> float:
    """Linear-regression slope of a series, normalized by mean magnitude.

    Returns % change per bar, relative to mean level. Returns 0 if degenerate.
    Accepts a pandas Series (uses .dropna() and .to_numpy()).
    """
    import numpy as np
    s = series.dropna()
    if len(s) < 3:
        return 0.0
    y = s.to_numpy(dtype=float)
    x = np.arange(len(y), dtype=float)
    slope = float(np.polyfit(x, y, 1)[0])
    denom = float(np.mean(np.abs(y))) or 1.0
    return slope / denom * 100
