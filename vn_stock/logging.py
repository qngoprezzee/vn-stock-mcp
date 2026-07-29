"""Structured logging helper.

Provides a single `get_logger(name)` entry-point. Logs to stderr in a
key=value format so subprocess / cache / error events are greppable.
The MCP server writes to stdout for protocol messages, so stderr is safe.
"""
from __future__ import annotations

import logging
import os
import sys


_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level_name = os.environ.get("VN_STOCK_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    root = logging.getLogger("vn_stock")
    root.setLevel(level)
    # Avoid duplicating handlers if imported twice
    root.handlers = [handler]
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger. Names are conventionally 'vn_stock.<subsystem>'."""
    _configure()
    if not name.startswith("vn_stock"):
        name = f"vn_stock.{name}"
    return logging.getLogger(name)
