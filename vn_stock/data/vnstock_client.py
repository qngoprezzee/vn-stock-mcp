"""Subprocess-isolated vnstock client.

vnstock's quota library calls sys.exit() on rate limit — running each call in a
child subprocess prevents that from killing the MCP server.
"""
from __future__ import annotations

import asyncio
import json
import sys

from vn_stock.config import SUBPROCESS_CONCURRENCY, VNSTOCK_HELPER
from vn_stock.data.cache import cache_get, cache_set
from vn_stock.logging import get_logger

_log = get_logger("vnstock")

# Bound concurrent subprocess spawns to avoid system overload and rate limits.
_SEM = asyncio.Semaphore(SUBPROCESS_CONCURRENCY)


async def vnstock_subprocess(func_name: str, kwargs: dict, retries: int = 3) -> str:
    """Run a named vnstock function in an isolated subprocess. Returns JSON string. Cached."""
    cached = cache_get(func_name, kwargs)
    if cached is not None:
        return cached

    async with _SEM:
        for attempt in range(retries):
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(VNSTOCK_HELPER), func_name, json.dumps(kwargs),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                lines = stdout.decode(errors="ignore").splitlines()
                for line in reversed(lines):
                    line = line.strip()
                    if line.startswith("[") or line.startswith("{"):
                        cache_set(func_name, kwargs, line)
                        return line
                return "[]"
            err = stderr.decode()
            if "Rate limit" in err or "RateLimit" in err:
                wait = 65 if attempt == 0 else 30
                _log.info("vnstock_rate_limited func=%s attempt=%d wait_s=%d", func_name, attempt, wait)
                await asyncio.sleep(wait)
            else:
                _log.warning("vnstock_error func=%s err=%s", func_name, err[:200])
                return json.dumps({"error": err[:300]})
    _log.error("vnstock_gave_up func=%s retries=%d", func_name, retries)
    return json.dumps({"error": "Rate limit persisted after retries"})
