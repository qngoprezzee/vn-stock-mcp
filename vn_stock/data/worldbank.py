"""World Bank Open Data client with disk cache, retry and stale-fallback.

WB annual data changes at most weekly. We cache successful responses for 7
days and accept up to 90-day-old cached responses if WB is unreachable (502
outages are common). Callers get a freshness label so they can annotate
their output.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from vn_stock.config import CACHE_DIR, WB_CACHE_TTL_SEC, WB_STALE_MAX_SEC
from vn_stock.logging import get_logger

_log = get_logger("worldbank")


def _wb_cache_path(code: str) -> Path:
    return CACHE_DIR / f"wb_{code.replace('.', '_')}.json"


def wb_cache_read(code: str) -> tuple[list[tuple[int, float]] | None, float]:
    """Return (series_or_none, age_seconds). None if cache missing."""
    path = _wb_cache_path(code)
    if not path.exists():
        return None, 0.0
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None, 0.0
    ts = float(entry.get("timestamp", 0))
    age = time.time() - ts
    series = [(int(y), float(v)) for y, v in entry.get("series", [])]
    return sorted(series, key=lambda x: x[0], reverse=True), age


def wb_cache_write(code: str, series: list[tuple[int, float]]) -> None:
    if not series:
        return
    CACHE_DIR.mkdir(exist_ok=True)
    _wb_cache_path(code).write_text(
        json.dumps({"timestamp": time.time(), "series": [[y, v] for y, v in series]}),
        encoding="utf-8",
    )


async def fetch_wb_indicator(
    client: httpx.AsyncClient, label: str, code: str,
) -> tuple[str, list[tuple[int, float]], str]:
    """Fetch a Vietnam WB indicator with cache/retry/stale-fallback.

    Returns (label, series, freshness) where freshness ∈ {"fresh", "cached", "stale", "unavailable"}.
    """
    cached, age = wb_cache_read(code)
    if cached is not None and age < WB_CACHE_TTL_SEC:
        return label, cached, "cached"

    url = f"https://api.worldbank.org/v2/country/VNM/indicator/{code}?format=json&date=2015:2026&per_page=30"
    payload = None
    for attempt in range(3):
        try:
            resp = await client.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
            break
        except Exception as e:
            _log.info("wb_fetch_retry code=%s attempt=%d err=%s", code, attempt, type(e).__name__)
            if attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
            continue

    if payload is not None and isinstance(payload, list) and len(payload) >= 2:
        series: list[tuple[int, float]] = []
        for entry in payload[1]:
            year = entry.get("date")
            val = entry.get("value")
            if year and val is not None:
                try:
                    series.append((int(year), float(val)))
                except (TypeError, ValueError):
                    continue
        if series:
            sorted_series = sorted(series, key=lambda x: x[0], reverse=True)
            wb_cache_write(code, sorted_series)
            return label, sorted_series, "fresh"

    if cached is not None and age < WB_STALE_MAX_SEC:
        _log.warning("wb_serving_stale code=%s age_days=%.1f", code, age / 86400)
        return label, cached, "stale"

    _log.warning("wb_unavailable code=%s", code)
    return label, [], "unavailable"
