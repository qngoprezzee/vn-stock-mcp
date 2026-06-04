"""Shared utilities for knowledge ingest pipelines.

Every pipeline (RSS, URL, PDF, manual paste, etc.) reuses these helpers so
manifest schema, frontmatter, slugification, dedup, and ticker extraction
stay consistent across source types.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import sys
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT     = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
SOURCES_DIR   = KNOWLEDGE_DIR / "sources"
MANIFEST_PATH = KNOWLEDGE_DIR / "manifest.json"

VN_TZ = timezone(timedelta(hours=7))


# Re-use ticker aliases from the main server module
sys.path.insert(0, str(REPO_ROOT))
from server import _TICKER_ALIASES  # noqa: E402

_TICKER_REGEX = re.compile(r"\b[A-Z]{3,4}\b")

# Hand-curated whitelist of common VN tickers + indices
_KNOWN_VN_TICKERS: set[str] = set(_TICKER_ALIASES.keys()) | {
    "FPT", "CMG", "VGI", "CTR", "VCB", "BID", "CTG", "TCB", "MBB", "VPB", "ACB",
    "VIC", "VHM", "VRE", "VNM", "SAB", "MSN", "MWG", "FRT", "PNJ", "HPG", "HSG",
    "NKG", "VJC", "HVN", "GVR", "PHR", "GAS", "PLX", "POW", "DCM", "DPM", "BSR",
    "BCM", "DIG", "KDH", "NLG", "DXG", "VIX", "SSI", "VND", "HCM", "VIB", "STB",
    "EIB", "MSB", "OCB", "TPB", "LPB", "SHB", "ANV", "VHC", "DGC", "PVD", "PVS",
    "VCG", "CTD", "REE", "NT2", "PC1", "CII", "GMD", "VTP", "ITD", "ELC", "IMP",
    "DHG", "TRA", "DBD", "VNI", "VN30", "HNX", "HOSE",
}


def now_vn_iso() -> str:
    return datetime.now(VN_TZ).isoformat(timespec="seconds")


def today_vn() -> str:
    return datetime.now(VN_TZ).date().isoformat()


def content_hash(*parts: str) -> str:
    """Stable hash for dedup. Pass any number of strings that together identify the content."""
    payload = "|".join(p.strip() for p in parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def slugify(text: str, max_len: int = 30) -> str:
    """ASCII slug from arbitrary text (strips diacritics, including Vietnamese)."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", ascii_only)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "untitled"


def clean_html_text(raw: str) -> str:
    """Strip HTML tags, decode entities (including the non-standard #NNN; pattern from CafeF)."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    for _ in range(2):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    text = re.sub(r"#(\d{2,5});", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_tickers(text: str) -> list[str]:
    """Pull recognised VN tickers from arbitrary text. Returns sorted unique list."""
    candidates = set(_TICKER_REGEX.findall(text or ""))
    found = set(candidates & _KNOWN_VN_TICKERS)

    upper = (text or "").upper()
    for ticker, aliases in _TICKER_ALIASES.items():
        if any(alias.upper() in upper for alias in aliases):
            found.add(ticker)

    return sorted(found)


def yaml_frontmatter(meta: dict[str, Any]) -> str:
    """Hand-rolled YAML frontmatter. All strings JSON-quoted for diacritic safety."""
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, list):
            if not value:
                lines.append(f"{key}: []")
            else:
                items = ", ".join(json.dumps(v, ensure_ascii=False) for v in value)
                lines.append(f"{key}: [{items}]")
        elif isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        else:
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.exists():
        return {"version": 1, "last_run": None, "stats": {"total_sources": 0, "by_type": {}}, "ingested": {}}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(manifest: dict[str, Any]) -> None:
    # Refresh stats before write so callers don't have to remember
    by_type: dict[str, int] = {}
    for entry in manifest["ingested"].values():
        t = entry.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1
    manifest["stats"] = {
        "total_sources": len(manifest["ingested"]),
        "by_type": by_type,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def already_ingested(manifest: dict, chash: str) -> bool:
    return any(entry.get("content_hash") == chash for entry in manifest["ingested"].values())


def category_dir(category: str) -> Path:
    """Routes a category name to the right sources/ subfolder."""
    valid = {"articles", "books", "blogs", "filings", "transcripts", "papers", "regulatory"}
    if category not in valid:
        category = "articles"  # safe default
    return SOURCES_DIR / category


def write_source(
    *,
    category: str,
    source_name: str,
    title: str,
    body: str,
    url: str = "",
    source_url: str = "",
    pub_date: str = "",
    authors: list[str] | None = None,
    language: str = "en",
    extra: dict[str, Any] | None = None,
    doc_type: str = "article",
) -> tuple[str, Path]:
    """Write a single source file with frontmatter into the right category folder.

    Returns (id, path). Caller is responsible for updating the manifest.
    """
    category_dir(category).mkdir(parents=True, exist_ok=True)

    chash = content_hash(url or title, title)
    source_slug = slugify(source_name)
    today = today_vn()
    sid = f"{source_slug}_{today}_{chash[:8]}"

    filename = f"{today}_{source_slug}_{chash[:8]}.md"
    filepath = category_dir(category) / filename

    meta: dict[str, Any] = {
        "id":                sid,
        "source":            source_name,
        "source_url":        source_url,
        "url":               url,
        "title":             title,
        "pub_date":          pub_date,
        "ingested_at":       now_vn_iso(),
        "content_hash":      chash,
        "language":          language,
        "type":              doc_type,
        "category":          category,
        "tickers_mentioned": extract_tickers(title + "\n" + body),
        "authors":           authors or [],
        "full_text_fetched": bool(body and len(body) > 200),
    }
    if extra:
        meta.update(extra)

    filepath.write_text(yaml_frontmatter(meta) + "\n\n" + (body or "") + "\n", encoding="utf-8")
    return sid, filepath


def manifest_entry(*, source_name: str, source_url: str, url: str, title: str, pub_date: str, chash: str, path: Path, category: str, doc_type: str) -> dict[str, Any]:
    return {
        "source":       source_name,
        "source_url":   source_url,
        "url":          url,
        "title":        title,
        "pub_date":     pub_date,
        "content_hash": chash,
        "type":         doc_type,
        "category":     category,
        "path":         str(path.relative_to(REPO_ROOT)),
        "ingested_at":  now_vn_iso(),
    }
