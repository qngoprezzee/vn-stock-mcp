"""Pandas-based technical primitives.

Operate on OHLCV DataFrames. Returns human-readable strings or lists of dicts
suitable for direct rendering into markdown. No I/O.
"""
from __future__ import annotations


def detect_candle_patterns(df) -> list[str]:
    """Detect candle patterns in the last 5 sessions. Returns human-readable pattern lines."""
    patterns: list[str] = []
    if len(df) < 6:
        return patterns

    tail = df.tail(6).reset_index(drop=True)  # 5 sessions + 1 prior for engulfing lookback
    for i in range(1, len(tail)):
        o, h, l, c = tail.loc[i, ["open", "high", "low", "close"]]
        po, pc = tail.loc[i - 1, ["open", "close"]]
        body = abs(c - o)
        rng = h - l
        if rng <= 0:
            continue
        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        date = str(tail.loc[i, "time"])[:10]

        if body / rng < 0.1:
            patterns.append(f"  {date}: ⚪ Doji — indecision, potential reversal signal")
            continue

        if body / rng > 0.9:
            direction = "🟢 Bullish Marubozu — strong buying" if c > o else "🔴 Bearish Marubozu — strong selling"
            patterns.append(f"  {date}: {direction}")
            continue

        if c > o and pc < po and o < pc and c > po and body > abs(pc - po):
            patterns.append(f"  {date}: 🟢 Bullish Engulfing — reversal signal after down move")
            continue

        if c < o and pc > po and o > pc and c < po and body > abs(pc - po):
            patterns.append(f"  {date}: 🔴 Bearish Engulfing — reversal signal after up move")
            continue

        if lower_shadow > 2 * body and upper_shadow < body * 0.3:
            patterns.append(f"  {date}: 🟢 Hammer — buyers rejected lower prices")
            continue

        if upper_shadow > 2 * body and lower_shadow < body * 0.3:
            patterns.append(f"  {date}: 🔴 Shooting Star — sellers rejected higher prices")
            continue

    return patterns


def pivot_structure(df, window: int = 5) -> tuple[str, list[float], list[float]]:
    """Identify pivots in last 30 days and classify trend structure (HH/HL/LH/LL/sideways)."""
    recent = df.tail(30).reset_index(drop=True)
    if len(recent) < window * 2 + 1:
        return "insufficient data", [], []

    highs, lows = [], []
    for i in range(window, len(recent) - window):
        segment_high = recent["high"].iloc[i - window : i + window + 1]
        segment_low = recent["low"].iloc[i - window : i + window + 1]
        if recent["high"].iloc[i] == segment_high.max():
            highs.append(float(recent["high"].iloc[i]))
        if recent["low"].iloc[i] == segment_low.min():
            lows.append(float(recent["low"].iloc[i]))

    if len(highs) < 2 or len(lows) < 2:
        return "no clear pivots (choppy)", highs, lows

    hh = highs[-1] > highs[-2]
    hl = lows[-1] > lows[-2]
    if hh and hl:
        return "🟢 Uptrend — Higher Highs + Higher Lows", highs, lows
    if not hh and not hl:
        return "🔴 Downtrend — Lower Highs + Lower Lows", highs, lows
    return "🟡 Sideways / transitioning", highs, lows


def detect_gaps(df, min_pct: float = 1.0) -> list[str]:
    """Find gap ups/downs in the last 20 sessions above min_pct threshold."""
    gaps: list[str] = []
    recent = df.tail(21).reset_index(drop=True)
    for i in range(1, len(recent)):
        prev_high = recent["high"].iloc[i - 1]
        prev_low = recent["low"].iloc[i - 1]
        today_low = recent["low"].iloc[i]
        today_high = recent["high"].iloc[i]
        date = str(recent["time"].iloc[i])[:10]

        if today_low > prev_high:
            pct = (today_low - prev_high) / prev_high * 100
            if pct >= min_pct:
                gaps.append(f"  {date}: 🟢 Gap up +{pct:.1f}%")
        elif today_high < prev_low:
            pct = (prev_low - today_high) / prev_low * 100
            if pct >= min_pct:
                gaps.append(f"  {date}: 🔴 Gap down -{pct:.1f}%")
    return gaps


def detect_wyckoff_events(
    df,
    lookback: int = 10,
    range_window: int = 20,
    min_depth_pct: float = 0.5,
) -> list[dict]:
    """Detect Wyckoff Spring (failed breakdown) and Upthrust (failed breakout).

    Spring:
      - session_low pierced prior support by >= min_depth_pct
      - session_close reclaimed back above support AND finished in upper half of the day's range
    Upthrust: mirror. Upper-half / lower-half check filters out marginal wicks.
    """
    events: list[dict] = []
    if len(df) < range_window + lookback + 1:
        return events

    vol_ma = df["volume"].rolling(range_window).mean()

    for offset in range(1, lookback + 1):
        idx = len(df) - offset
        window = df.iloc[idx - range_window : idx]
        if len(window) < range_window:
            continue

        prev_high = float(window["high"].max())
        prev_low = float(window["low"].min())
        row = df.iloc[idx]
        session_high = float(row["high"])
        session_low = float(row["low"])
        session_close = float(row["close"])
        session_vol = float(row["volume"])
        session_date = str(row["time"])[:10]
        session_range = session_high - session_low
        if session_range <= 0:
            continue
        close_position = (session_close - session_low) / session_range  # 0 = at low, 1 = at high
        avg_vol = float(vol_ma.iloc[idx]) if vol_ma.iloc[idx] and vol_ma.iloc[idx] > 0 else 0
        vol_ratio = session_vol / avg_vol if avg_vol > 0 else 1.0

        # Spring: pierced support meaningfully, reclaimed with close in upper half of day
        if session_low < prev_low and session_close > prev_low:
            depth_pct = (prev_low - session_low) / prev_low * 100
            if depth_pct >= min_depth_pct and close_position >= 0.5:
                events.append({
                    "date": session_date,
                    "type": "spring",
                    "depth_pct": depth_pct,
                    "vol_ratio": vol_ratio,
                    "close_position": close_position,
                    "prev_support": prev_low,
                    "session_low": session_low,
                    "session_close": session_close,
                })

        # Upthrust: pierced resistance meaningfully, rejected with close in lower half of day
        elif session_high > prev_high and session_close < prev_high:
            depth_pct = (session_high - prev_high) / prev_high * 100
            if depth_pct >= min_depth_pct and close_position <= 0.5:
                events.append({
                    "date": session_date,
                    "type": "upthrust",
                    "depth_pct": depth_pct,
                    "vol_ratio": vol_ratio,
                    "close_position": close_position,
                    "prev_resistance": prev_high,
                    "session_high": session_high,
                    "session_close": session_close,
                })

    return sorted(events, key=lambda e: e["date"])
