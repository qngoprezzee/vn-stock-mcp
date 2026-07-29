"""Pydantic schemas for persistent JSON state.

These live in one place so schema drift becomes structurally impossible: any
handler that loads/saves state uses these models. Validation on load is
defensive — malformed files log a warning and yield a fresh empty state
instead of crashing.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Portfolio ───────────────────────────────────────────────────────────────

class Holding(BaseModel):
    ticker: str
    shares: float
    avg_cost: float
    target_weight: float | None = None
    opened_at: str = ""  # ISO date string
    notes: str = ""

    class Config:
        extra = "ignore"  # Tolerate legacy fields


class Portfolio(BaseModel):
    holdings: list[Holding] = Field(default_factory=list)
    cash_vnd: float = 0.0
    peak_value: float = 0.0
    peak_date: str = ""

    class Config:
        extra = "ignore"


# ── Portfolio snapshots (daily value log) ───────────────────────────────────

class Snapshot(BaseModel):
    date: str  # ISO date string
    total_value: float
    equity_value: float
    cash: float

    class Config:
        extra = "ignore"


# ── User-entered M2 series ──────────────────────────────────────────────────

class M2Point(BaseModel):
    date: str  # YYYY-MM
    value_trillion_vnd: float
    source: str = ""
    note: str = ""

    class Config:
        extra = "ignore"


# ── Investment thesis (structured; see saved-thesis markdown for prose form) ──

class Thesis(BaseModel):
    ticker: str
    thesis: str
    buy_price: float
    target_price: float
    stop_price: float
    conviction: str = "Medium"
    falsification_criteria: str = ""
    catalysts: str = ""
    strongest_bias: str = ""
    premortem_reason: str = ""

    class Config:
        extra = "ignore"


# ── Decision log entry ──────────────────────────────────────────────────────

class Decision(BaseModel):
    ticker: str
    action: str  # buy | sell | add | trim | hold
    price: float
    rationale: str
    quantity: int = 0
    outcome: str = ""

    class Config:
        extra = "ignore"


# ── Helpers ─────────────────────────────────────────────────────────────────

def parse_list(raw: Any, model: type[BaseModel]) -> list[BaseModel]:
    """Best-effort validation of a JSON list into a list of model instances.

    Invalid entries are silently dropped (with a logger warning by caller).
    """
    if not isinstance(raw, list):
        return []
    out: list[BaseModel] = []
    for item in raw:
        try:
            out.append(model.model_validate(item))
        except Exception:
            continue
    return out
