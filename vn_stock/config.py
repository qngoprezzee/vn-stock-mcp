"""Centralized configuration: paths, TTLs, sector definitions.

All storage locations and static lookup tables live here so handlers can import
them by name and future code doesn't rediscover the conventions.
"""
from __future__ import annotations

from pathlib import Path

# ── Repo root ────────────────────────────────────────────────────────────────
# The package sits one level below the repo root. Storage paths are anchored to
# the repo root so behaviour matches the previous inline `Path(__file__).parent`
# calls in server.py (which resolved to the repo root because server.py sits at
# the top level).
REPO_ROOT = Path(__file__).resolve().parent.parent


# ── Storage paths (persistent JSON state, all gitignored) ───────────────────
WATCHLIST_PATH = REPO_ROOT / ".watchlist.json"
PORTFOLIO_PATH = REPO_ROOT / ".portfolio.json"
SNAPSHOTS_PATH = REPO_ROOT / ".portfolio_snapshots.json"
M2_SERIES_PATH = REPO_ROOT / ".m2_series.json"
CPI_SERIES_PATH = REPO_ROOT / ".cpi_series.json"
RATE_SERIES_PATH = REPO_ROOT / ".rate_series.json"
FX_HISTORY_PATH = REPO_ROOT / ".fx_history.json"


# ── vnstock subprocess isolation ────────────────────────────────────────────
VNSTOCK_HELPER = REPO_ROOT / "_vnstock_worker.py"
SUBPROCESS_CONCURRENCY = 6


# ── Cache config (file-backed) ──────────────────────────────────────────────
CACHE_DIR = REPO_ROOT / ".cache"
# Per-function TTLs for vnstock responses (seconds). Missing keys use DEFAULT_TTL.
CACHE_TTL: dict[str, int] = {
    "company_overview":   3600,
    "company_news":       1800,
    "company_events":     3600,
    "quote_history":       300,
    "quote_history_full": 3600,
    "income_statement":  86400,
    "balance_sheet":     86400,
    "cash_flow":         86400,
    "price_board":         60,
}
DEFAULT_TTL = 3600

# World Bank annual data doesn't change more than weekly — cache 7d, stale-fallback 90d
WB_CACHE_TTL_SEC = 7 * 24 * 3600
WB_STALE_MAX_SEC = 90 * 24 * 3600


# ── Sector definitions (used by rotation, cycle, risk, DCF sanity) ──────────
# Broad VN sector map used by get_sector_rotation and get_market_cycle.
# Each sector lists representative liquid tickers for equal-weighted computation.
VN_SECTORS: dict[str, list[str]] = {
    "Banking":          ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "STB"],
    "Real Estate":      ["VIC", "VHM", "NLG", "KDH", "DXG", "DIG"],
    "Technology":       ["FPT", "CMG", "ELC"],
    "Telecom":          ["VGI", "CTR"],
    "Steel/Materials":  ["HPG", "HSG", "NKG", "DGC"],
    "Consumer Staples": ["VNM", "SAB", "MSN"],
    "Retail":           ["MWG", "FRT", "PNJ"],
    "Aviation":         ["VJC", "HVN"],
    "Industrial":       ["GVR", "PHR"],
    "Energy":           ["GAS", "PLX", "BSR", "PVS"],
}
CYCLICAL_SECTORS: set[str] = {
    "Banking", "Real Estate", "Steel/Materials", "Retail", "Aviation", "Industrial", "Energy",
}
DEFENSIVE_SECTORS: set[str] = {"Consumer Staples", "Telecom"}


# Sector-string → peer-ticker mapping used by compare_stocks & DCF sensitivity.
# Keys are lowercase substrings; vnstock's sector strings vary ("Banks" vs "Banking").
SECTOR_PEER_SET: list[tuple[str, list[str]]] = [
    ("technolog",          ["FPT", "CMG", "VGI", "ITD", "ELC"]),
    ("bank",               ["VCB", "BID", "CTG", "TCB", "MBB", "ACB", "STB", "VPB", "HDB"]),
    ("real estate",        ["VIC", "VHM", "NLG", "KDH", "DXG", "DIG"]),
    ("steel",              ["HPG", "HSG", "NKG"]),
    ("material",           ["HPG", "HSG", "DGC"]),
    ("staple",             ["VNM", "SAB", "MSN"]),
    ("discretionary",      ["MWG", "FRT", "PNJ"]),
    ("retail",             ["MWG", "FRT", "PNJ"]),
    ("aviation",           ["VJC", "HVN"]),
    ("airline",            ["VJC", "HVN"]),
    ("industrial",         ["GVR", "PHR"]),
    ("energy",             ["GAS", "PLX", "BSR"]),
    ("oil",                ["GAS", "PLX", "BSR", "PVD", "PVS"]),
    ("telecom",            ["VGI", "CTR"]),
]

# DCF vs relative valuation weightings by sector (banks → nearly pure relative;
# durable franchises → nearly pure DCF).
VALUATION_WEIGHTS: list[tuple[str, dict[str, float]]] = [
    ("bank",               {"dcf": 0.1, "relative": 0.9}),
    ("insurance",          {"dcf": 0.1, "relative": 0.9}),
    ("real estate",        {"dcf": 0.2, "relative": 0.8}),
    ("aviation",           {"dcf": 0.2, "relative": 0.8}),
    ("airline",            {"dcf": 0.2, "relative": 0.8}),
    ("steel",              {"dcf": 0.3, "relative": 0.7}),
    ("material",           {"dcf": 0.3, "relative": 0.7}),
    ("energy",             {"dcf": 0.3, "relative": 0.7}),
    ("oil",                {"dcf": 0.3, "relative": 0.7}),
    ("discretionary",      {"dcf": 0.4, "relative": 0.6}),
    ("retail",             {"dcf": 0.4, "relative": 0.6}),
    ("staple",             {"dcf": 0.6, "relative": 0.4}),
    ("technolog",          {"dcf": 0.5, "relative": 0.5}),
    ("telecom",            {"dcf": 0.5, "relative": 0.5}),
    ("industrial",         {"dcf": 0.5, "relative": 0.5}),
]
DEFAULT_WEIGHTS = {"dcf": 0.5, "relative": 0.5}
DCF_UNRELIABLE_KEYS = ["bank", "insurance", "real estate"]


# Sector beta proxies for VN market (vs VN-Index baseline = 1.0). Keys are
# lowercase substrings to survive vnstock's inconsistent sector strings.
SECTOR_BETAS: dict[str, float] = {
    "real estate": 1.4, "bất động sản": 1.4,
    "banking": 1.1, "ngân hàng": 1.1, "bank": 1.1,
    "technology": 1.2, "công nghệ": 1.2, "it services": 1.2,
    "steel": 1.5, "thép": 1.5, "materials": 1.4,
    "consumer staples": 0.7, "hàng tiêu dùng thiết yếu": 0.7,
    "consumer discretionary": 1.1, "retail": 1.1,
    "utilities": 0.5, "điện": 0.6, "tiện ích": 0.5,
    "aviation": 1.6, "hàng không": 1.6,
    "oil & gas": 1.3, "dầu khí": 1.3, "energy": 1.3,
    "telecommunications": 0.9, "viễn thông": 0.9,
}


# ── Watch universe ──────────────────────────────────────────────────────────
# Curated large-cap set surfaced in market overviews and stress tests.
MARKET_WATCH: list[str] = [
    "VCB", "BID", "TCB", "MBB", "VPB",     # Banking
    "VIC", "VHM",                            # Real Estate
    "FPT", "HPG", "VNM",                    # Tech / Steel / Consumer staples
    "MWG", "GAS", "PLX", "MSN", "SAB",     # Retail / Energy / Consumer
]


# ── Top-5 banks used as fresh credit-growth proxy for M2 signal ─────────────
M2_BANKS: list[str] = ["VCB", "BID", "CTG", "TCB", "MBB"]


# ── World Bank indicators used by get_vn_macro_indicators ───────────────────
WB_INDICATORS: dict[str, str] = {
    "GDP growth (%)":         "NY.GDP.MKTP.KD.ZG",
    "CPI inflation (%)":      "FP.CPI.TOTL.ZG",
    "Real interest rate (%)": "FR.INR.RINR",
    "Unemployment (%)":       "SL.UEM.TOTL.ZS",
    "Current account (% GDP)":"BN.CAB.XOKA.GD.ZS",
}
