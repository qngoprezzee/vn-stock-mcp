"""File-based cache for vnstock responses.

Cache entries live under `.cache/` at repo root and are keyed by a hash of the
function name + kwargs. Per-function TTLs live in `vn_stock.config.CACHE_TTL`.
"""
from __future__ import annotations

import hashlib
import json
import time

from vn_stock.config import CACHE_DIR, CACHE_TTL, DEFAULT_TTL
from vn_stock.logging import get_logger

_log = get_logger("cache")


def cache_key(func_name: str, kwargs: dict) -> str:
    payload = func_name + "|" + json.dumps(kwargs, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def cache_get(func_name: str, kwargs: dict) -> str | None:
    path = CACHE_DIR / f"{cache_key(func_name, kwargs)}.json"
    if not path.exists():
        return None
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _log.debug("cache_read_failed func=%s", func_name)
        return None
    ttl = CACHE_TTL.get(func_name, DEFAULT_TTL)
    if time.time() - entry.get("timestamp", 0) > ttl:
        return None
    return entry.get("data")


def cache_set(func_name: str, kwargs: dict, data: str) -> None:
    # Never cache error payloads
    if data.lstrip().startswith("{") and '"error"' in data[:100]:
        return
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{cache_key(func_name, kwargs)}.json"
    try:
        path.write_text(
            json.dumps({"timestamp": time.time(), "data": data}),
            encoding="utf-8",
        )
    except OSError as e:
        _log.warning("cache_write_failed func=%s err=%s", func_name, e)
