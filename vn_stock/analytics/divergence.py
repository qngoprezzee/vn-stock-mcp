"""Divergence detection between price and momentum/flow indicators."""
from __future__ import annotations


def detect_divergence(price_series, indicator_series, label: str) -> str | None:
    """Compare last 20-day price extremes vs indicator extremes to spot divergence.

    Args:
        price_series: pandas Series of prices (uses .tail(20) + integer indexing after reset).
        indicator_series: pandas Series of indicator values (same length as price).
        label: name of the indicator (e.g. "OBV", "MFI") for display.

    Returns a markdown-ready string when a bullish or bearish divergence is
    detected, or None if the last 20 sessions don't show one.
    """
    if len(price_series) < 20 or len(indicator_series.dropna()) < 20:
        return None
    price_recent = price_series.tail(20).reset_index(drop=True)
    ind_recent = indicator_series.tail(20).reset_index(drop=True)

    half = len(price_recent) // 2
    p_first_max, p_second_max = price_recent[:half].max(), price_recent[half:].max()
    p_first_min, p_second_min = price_recent[:half].min(), price_recent[half:].min()
    i_first_max, i_second_max = ind_recent[:half].max(), ind_recent[half:].max()
    i_first_min, i_second_min = ind_recent[:half].min(), ind_recent[half:].min()

    if p_second_max > p_first_max and i_second_max < i_first_max:
        return f"🔴 Bearish divergence: price making higher highs while {label} making lower highs (distribution warning)"
    if p_second_min < p_first_min and i_second_min > i_first_min:
        return f"🟢 Bullish divergence: price making lower lows while {label} making higher lows (accumulation signal)"
    return None
