"""Read-only queries over the ingested knowledge corpus.

Used by:
  - K6 daily_brief pipeline
  - K7 extract_concept pipeline
  - K8 compare_authors_on MCP tool
  - K9 thesis_context MCP tool

All functions are pure — they read manifest + source files and return data,
nothing is mutated.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator

VN_TZ = timezone(timedelta(hours=7))
REPO_ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = REPO_ROOT / "knowledge"
MANIFEST_PATH = KNOWLEDGE_DIR / "manifest.json"

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Source:
    id: str
    path: Path
    metadata: dict
    body: str

    def __getitem__(self, key: str):
        return self.metadata.get(key)

    @property
    def title(self) -> str:        return self.metadata.get("title", "")
    @property
    def source_name(self) -> str:  return self.metadata.get("source", "")
    @property
    def authors(self) -> list[str]: return self.metadata.get("authors", []) or []
    @property
    def category(self) -> str:     return self.metadata.get("category", "articles")
    @property
    def language(self) -> str:     return self.metadata.get("language", "")
    @property
    def url(self) -> str:          return self.metadata.get("url", "")
    @property
    def tickers(self) -> list[str]: return self.metadata.get("tickers_mentioned", []) or []
    @property
    def pub_date(self) -> str:     return self.metadata.get("pub_date", "")
    @property
    def ingested_at(self) -> str:  return self.metadata.get("ingested_at", "")


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"version": 1, "ingested": {}, "stats": {}}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return (metadata_dict, body_without_frontmatter)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    block = m.group(1)
    body = text[m.end():]
    fields: dict = {}
    for line in block.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            items: list[str] = []
            for tok in re.findall(r'"([^"]*)"', inner):
                items.append(tok)
            if not items:
                items = [v.strip() for v in inner.split(",") if v.strip()]
            fields[key] = items
        elif value.startswith('"') and value.endswith('"'):
            fields[key] = value[1:-1]
        elif value in ("true", "false"):
            fields[key] = (value == "true")
        elif value == "null":
            fields[key] = None
        else:
            # Try numeric
            try:
                if "." in value:
                    fields[key] = float(value)
                else:
                    fields[key] = int(value)
            except ValueError:
                fields[key] = value
    return fields, body


def read_source(path: Path) -> Source | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    meta, body = parse_frontmatter(text)
    sid = meta.get("id") or path.stem
    return Source(id=str(sid), path=path, metadata=meta, body=body)


def iter_sources(
    *,
    category: str | None = None,
    author: str | None = None,
    source_name: str | None = None,
    source_contains: str | None = None,
    language: str | None = None,
    tickers: list[str] | None = None,
    since_days: int | None = None,
    limit: int | None = None,
) -> Iterator[Source]:
    """Yield Source objects matching the given filters.

    Filters:
        category        — exact match on `category` frontmatter field
        author          — string contained in any author entry (case-insensitive)
        source_name     — exact match on `source` frontmatter field
        source_contains — substring match on `source` field (case-insensitive)
        language        — exact match on language code
        tickers         — at least one of these tickers must appear in `tickers_mentioned`
        since_days      — only sources whose pub_date OR ingested_at is within N days
        limit           — cap on number of sources yielded
    """
    manifest = load_manifest()
    cutoff = None
    if since_days is not None:
        cutoff = datetime.now(VN_TZ) - timedelta(days=since_days)

    count = 0
    for sid, entry in manifest.get("ingested", {}).items():
        if category and entry.get("category") != category:
            continue
        if source_name and entry.get("source") != source_name:
            continue
        if source_contains and source_contains.lower() not in (entry.get("source", "") or "").lower():
            continue

        path = REPO_ROOT / entry["path"]
        if not path.exists():
            continue

        # Lazy-load file to apply richer filters
        source = read_source(path)
        if source is None:
            continue

        if language and source.language != language:
            continue
        if author:
            au_lower = author.lower()
            if not any(au_lower in (a or "").lower() for a in source.authors):
                continue
        if tickers:
            if not (set(t.upper() for t in tickers) & set(source.tickers)):
                continue
        if cutoff is not None:
            # Try pub_date first; if unparseable, fall back to ingested_at
            ref_dt = _parse_loose_date(source.pub_date) or _parse_loose_date(source.ingested_at)
            if ref_dt is None or ref_dt < cutoff:
                continue

        yield source
        count += 1
        if limit and count >= limit:
            return


def _parse_loose_date(s: str) -> datetime | None:
    """Best-effort date parse — handles ISO 8601, RFC 822, and a few other shapes."""
    if not s:
        return None
    s = s.strip()
    # ISO formats
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s[:25 if "T" in s else 10], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=VN_TZ)
            return dt
        except ValueError:
            pass
    # RFC 822 (RSS pubDate) — varieties with/without seconds, +0700 or GMT
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M %Z",   "%a, %d %b %Y %H:%M %z",
        "%a, %d %b %y %H:%M:%S %z", "%a, %d %b %y %H:%M:%S %Z",
    ):
        try:
            dt = datetime.strptime(s[:35], fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=VN_TZ)
            return dt
        except ValueError:
            pass
    return None


def find_passages(
    sources: Iterable[Source],
    keywords: list[str],
    *,
    context_paragraphs: int = 2,
    max_matches_per_source: int = 5,
) -> list[dict]:
    """Search source bodies for keyword matches with surrounding context.

    Returns list of dicts:
        {source_id, source_name, title, year, passage, keyword, location}

    The passage includes `context_paragraphs` of surrounding paragraphs above and below the match.
    """
    if not keywords:
        return []

    # Compile case-insensitive whole-word patterns
    patterns = [
        (kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))
        for kw in keywords
    ]

    results: list[dict] = []
    for source in sources:
        paragraphs = re.split(r"\n\s*\n", source.body)
        matches_this_source = 0
        for i, para in enumerate(paragraphs):
            if matches_this_source >= max_matches_per_source:
                break
            for kw, pat in patterns:
                if pat.search(para):
                    lo = max(0, i - context_paragraphs)
                    hi = min(len(paragraphs), i + context_paragraphs + 1)
                    passage = "\n\n".join(paragraphs[lo:hi]).strip()

                    results.append({
                        "source_id":   source.id,
                        "source_name": source.source_name,
                        "title":       source.title,
                        "url":         source.url,
                        "pub_date":    source.pub_date,
                        "authors":     source.authors,
                        "keyword":     kw,
                        "passage":     passage,
                        "para_idx":    i,
                    })
                    matches_this_source += 1
                    break  # one keyword hit per paragraph is enough

    return results


def list_theses_for_ticker(ticker: str) -> list[dict]:
    """Look at theses/INDEX.md and return entries mentioning this ticker."""
    index_path = REPO_ROOT / "theses" / "INDEX.md"
    if not index_path.exists():
        return []
    ticker = ticker.upper()
    rows = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"- [{ticker}"):
            m = re.match(r"- \[([A-Z]+) — (\d{4}-\d{2}-\d{2})\]\(([^)]+)\)\s*—\s*(.*)", line)
            if m:
                rows.append({
                    "ticker":    m.group(1),
                    "date":      m.group(2),
                    "filename":  m.group(3),
                    "summary":   m.group(4),
                    "path":      str(REPO_ROOT / "theses" / m.group(3)),
                })
    return rows


def list_analyses_for_ticker(ticker: str) -> list[dict]:
    """Look at analyses/INDEX.md and return entries mentioning this ticker."""
    index_path = REPO_ROOT / "analyses" / "INDEX.md"
    if not index_path.exists():
        return []
    ticker = ticker.upper()
    rows = []
    for line in index_path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"- \[([A-Z]+) — ([^\]]+)\]\(([^)]+)\)\s*—\s*(.*)", line)
        if m and m.group(1) == ticker:
            rows.append({
                "ticker":   m.group(1),
                "label":    m.group(2),
                "filename": m.group(3),
                "summary":  m.group(4),
                "path":     str(REPO_ROOT / "analyses" / m.group(3)),
            })
    return rows
