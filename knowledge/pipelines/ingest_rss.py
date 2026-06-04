"""Ingest RSS feeds into knowledge/sources/articles/ as immutable markdown files.

Each article becomes a markdown file with YAML frontmatter capturing:
  - Source attribution (which feed, original URL)
  - Publication date + ingestion date
  - Content hash (for dedup across re-runs)
  - Auto-detected VN ticker mentions
  - Language tag

Run:
    .venv/bin/python -m knowledge.pipelines.ingest_rss
    .venv/bin/python -m knowledge.pipelines.ingest_rss --stats
    .venv/bin/python -m knowledge.pipelines.ingest_rss --feeds news        # news RSS only
    .venv/bin/python -m knowledge.pipelines.ingest_rss --feeds economy     # economy RSS only
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable

import httpx


# Re-use the feed lists from server.py so we have a single source of truth
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server import _RSS_FEEDS, _ECONOMY_FEEDS, _TICKER_ALIASES, _RSS_HEADERS  # noqa: E402


REPO_ROOT       = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR   = REPO_ROOT / "knowledge"
ARTICLES_DIR    = KNOWLEDGE_DIR / "sources" / "articles"
MANIFEST_PATH   = KNOWLEDGE_DIR / "manifest.json"

# VN tz: UTC+7
VN_TZ = timezone(timedelta(hours=7))

# Detect 3-4 char uppercase tokens that look like VN tickers
_TICKER_REGEX = re.compile(r"\b[A-Z]{3,4}\b")
# Auxiliary set of well-known VN tickers + alias lookups
_KNOWN_TICKERS = set(_TICKER_ALIASES.keys()) | {
    "FPT", "CMG", "VGI", "CTR", "VCB", "BID", "CTG", "TCB", "MBB", "VPB", "ACB",
    "VIC", "VHM", "VRE", "VNM", "SAB", "MSN", "MWG", "FRT", "PNJ", "HPG", "HSG",
    "NKG", "VJC", "HVN", "GVR", "PHR", "GAS", "PLX", "POW", "DCM", "DPM", "BSR",
    "BCM", "DIG", "KDH", "NLG", "DXG", "VIX", "SSI", "VND", "HCM", "VIB", "STB",
    "EIB", "MSB", "OCB", "TPB", "LPB", "SHB", "ANV", "VHC", "DGC", "PVD", "PVS",
    "VCG", "CTD", "REE", "NT2", "PC1", "CII", "GMD", "VTP", "ITD", "ELC", "IMP",
    "DHG", "TRA", "DBD",
    # Indices
    "VNI", "VN30", "HNX", "HOSE",
}


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"version": 1, "last_run": None, "stats": {"total_sources": 0, "by_type": {}}, "ingested": {}}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")


def _content_hash(title: str, link: str) -> str:
    """Stable hash for dedup. URL + title together because some sources rewrite URLs."""
    payload = f"{link.strip()}|{title.strip()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _slugify_source(source: str) -> str:
    """Compact ASCII slug from a feed name. Strips Vietnamese diacritics."""
    # Decompose unicode and drop combining marks (handles VN diacritics)
    normalized = unicodedata.normalize("NFKD", source)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    s = ascii_only.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:30] or "source"


def _clean_description(raw: str) -> str:
    """Strip HTML tags AND decode entities. Some VN feeds double-encode characters."""
    text = re.sub(r"<[^>]+>", " ", raw)
    # Decode HTML entities like &#244; → ô and &amp; → & (loops handle double-encoding)
    for _ in range(2):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    # The CafeF feeds use # without leading & for entities (e.g. "#244;" instead of "&#244;")
    text = re.sub(r"#(\d{2,5});", lambda m: chr(int(m.group(1))), text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_tickers(text: str) -> list[str]:
    """Pull out uppercase tokens that match known VN tickers."""
    candidates = set(_TICKER_REGEX.findall(text))
    found = sorted(candidates & _KNOWN_TICKERS)
    # Also scan for known company names (VN aliases like Vingroup → VIC)
    upper = text.upper()
    for ticker, aliases in _TICKER_ALIASES.items():
        if any(alias.upper() in upper for alias in aliases):
            if ticker not in found:
                found.append(ticker)
    return sorted(set(found))


def _yaml_frontmatter(meta: dict) -> str:
    """Hand-roll YAML frontmatter (no pyyaml dep). Strings are always quoted."""
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
            # Quote all strings to be safe with Vietnamese diacritics + special chars
            lines.append(f"{key}: {json.dumps(str(value), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


async def _fetch_feed(client: httpx.AsyncClient, source: str, url: str) -> list[dict]:
    """Pull and parse one RSS feed. Returns list of items with normalized fields."""
    try:
        resp = await client.get(url, timeout=15, headers=_RSS_HEADERS)
        resp.raise_for_status()
        raw = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", resp.text).encode("utf-8", errors="replace")
        root = ET.fromstring(raw)
    except Exception as e:
        print(f"  ⚠️  {source}: feed unreachable ({e})")
        return []

    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title", "") or "").strip()
        link_el = item.find("link")
        link = (link_el.text or "").strip() if link_el is not None else ""
        if not link:
            link = (item.findtext("guid", "") or "").strip()
        pub_date = (item.findtext("pubDate", "") or "").strip()
        description_raw = item.findtext("description", "") or ""
        description = _clean_description(description_raw)

        if not title or not link:
            continue

        items.append({
            "source": source,
            "title": title,
            "link": link,
            "pub_date": pub_date,
            "description": description,
        })
    return items


def _write_article(item: dict, source_url: str) -> tuple[str, Path]:
    """Persist one article as markdown with frontmatter. Returns (id, path)."""
    content_hash = _content_hash(item["title"], item["link"])
    source_slug = _slugify_source(item["source"])
    today = datetime.now(VN_TZ).date().isoformat()

    article_id = f"{source_slug}_{today}_{content_hash[:8]}"
    filename = f"{today}_{source_slug}_{content_hash[:8]}.md"
    filepath = ARTICLES_DIR / filename

    text_for_ticker_scan = f"{item['title']} {item['description']}"
    tickers = _extract_tickers(text_for_ticker_scan)

    meta = {
        "id":                  article_id,
        "source":              item["source"],
        "source_url":          source_url,
        "url":                 item["link"],
        "title":               item["title"],
        "pub_date":            item["pub_date"],
        "ingested_at":         datetime.now(VN_TZ).isoformat(timespec="seconds"),
        "content_hash":        content_hash,
        "language":            "vi",
        "type":                "article",
        "tickers_mentioned":   tickers,
        "authors":             [],
        "full_text_fetched":   False,
    }

    body = item["description"] or "*No description in RSS feed — fetch full text via follow-up pipeline.*"
    filepath.write_text(_yaml_frontmatter(meta) + "\n\n" + body + "\n", encoding="utf-8")
    return article_id, filepath


def _source_url_from_feed(feed_url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(feed_url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _ingest(feed_set: str) -> dict:
    """Ingest selected feeds. Returns counters."""
    ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest()

    feeds: list[tuple[str, str]] = []
    if feed_set in ("news", "all"):
        feeds += list(_RSS_FEEDS)
    if feed_set in ("economy", "all"):
        feeds += list(_ECONOMY_FEEDS)

    # Dedupe feeds by URL (overlap between _RSS_FEEDS and _ECONOMY_FEEDS)
    seen_urls = set()
    unique_feeds = []
    for source, url in feeds:
        if url not in seen_urls:
            seen_urls.add(url)
            unique_feeds.append((source, url))

    print(f"Ingesting {len(unique_feeds)} unique feeds...")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        batches = await asyncio.gather(
            *[_fetch_feed(client, source, url) for source, url in unique_feeds],
            return_exceptions=True,
        )

    new_count = 0
    skip_count = 0
    error_count = 0

    for (source, url), batch in zip(unique_feeds, batches):
        if isinstance(batch, Exception):
            error_count += 1
            print(f"  ⚠️  {source}: {batch}")
            continue
        if not batch:
            continue

        source_url = _source_url_from_feed(url)
        for item in batch:
            content_hash = _content_hash(item["title"], item["link"])
            today = datetime.now(VN_TZ).date().isoformat()
            article_id = f"{_slugify_source(source)}_{today}_{content_hash[:8]}"

            # Dedup against manifest (note: same content_hash → same article_id today)
            if article_id in manifest["ingested"]:
                skip_count += 1
                continue
            # Also check by content_hash alone for cross-day dedup
            if any(v.get("content_hash") == content_hash for v in manifest["ingested"].values()):
                skip_count += 1
                continue

            try:
                aid, fp = _write_article(item, source_url)
                manifest["ingested"][aid] = {
                    "source": item["source"],
                    "source_url": source_url,
                    "url": item["link"],
                    "title": item["title"],
                    "pub_date": item["pub_date"],
                    "content_hash": content_hash,
                    "type": "article",
                    "path": str(fp.relative_to(REPO_ROOT)),
                    "ingested_at": datetime.now(VN_TZ).isoformat(timespec="seconds"),
                }
                new_count += 1
            except Exception as e:
                print(f"  ⚠️  Failed to write {item['title'][:60]}: {e}")
                error_count += 1

    # Update manifest stats
    by_type: dict[str, int] = {}
    for entry in manifest["ingested"].values():
        t = entry.get("type", "article")
        by_type[t] = by_type.get(t, 0) + 1

    manifest["last_run"] = datetime.now(VN_TZ).isoformat(timespec="seconds")
    manifest["stats"] = {
        "total_sources": len(manifest["ingested"]),
        "by_type": by_type,
    }
    _save_manifest(manifest)

    return {"new": new_count, "skipped": skip_count, "errors": error_count, "total_after": len(manifest["ingested"])}


def _print_stats() -> None:
    manifest = _load_manifest()
    stats = manifest.get("stats", {})

    print(f"# Knowledge Manifest")
    print(f"Last run: {manifest.get('last_run', 'never')}")
    print(f"Total sources: {stats.get('total_sources', 0)}")
    print(f"\nBy type:")
    for t, n in (stats.get("by_type") or {}).items():
        print(f"  {t}: {n}")

    # Source distribution
    by_source: dict[str, int] = {}
    by_date:   dict[str, int] = {}
    ticker_counts: dict[str, int] = {}
    for entry in manifest["ingested"].values():
        s = entry.get("source", "?")
        by_source[s] = by_source.get(s, 0) + 1
        d = (entry.get("ingested_at") or "")[:10]
        by_date[d] = by_date.get(d, 0) + 1

    # Scan files for ticker counts
    for fp in ARTICLES_DIR.glob("*.md"):
        text = fp.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"tickers_mentioned: \[(.*?)\]", text)
        if m:
            for t in re.findall(r'"([A-Z]+)"', m.group(1)):
                ticker_counts[t] = ticker_counts.get(t, 0) + 1

    if by_source:
        print(f"\nTop sources:")
        for src, n in sorted(by_source.items(), key=lambda x: -x[1])[:10]:
            print(f"  {n:4d}  {src}")

    if ticker_counts:
        print(f"\nTop tickers mentioned:")
        for t, n in sorted(ticker_counts.items(), key=lambda x: -x[1])[:15]:
            print(f"  {n:4d}  {t}")

    if by_date:
        print(f"\nIngest dates:")
        for d, n in sorted(by_date.items(), reverse=True)[:7]:
            print(f"  {d}: {n}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--stats", action="store_true", help="Print manifest stats and exit")
    parser.add_argument(
        "--feeds",
        choices=["news", "economy", "all"],
        default="all",
        help="Which feed set to ingest (default: all = news + economy)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.stats:
        _print_stats()
        return 0

    print(f"Ingesting feeds: {args.feeds}")
    result = asyncio.run(_ingest(args.feeds))
    print(f"\nDone.")
    print(f"  New articles:  {result['new']}")
    print(f"  Skipped (dup): {result['skipped']}")
    print(f"  Errors:        {result['errors']}")
    print(f"  Manifest total: {result['total_after']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
