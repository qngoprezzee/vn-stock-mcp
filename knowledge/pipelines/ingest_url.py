"""Generic URL → knowledge ingest. Handles HTML (via trafilatura) and PDF (via pymupdf).

Usage:
    .venv/bin/python -m knowledge.pipelines.ingest_url <url> [--category books] [--source "Howard Marks"] [--authors "Howard Marks"]
    .venv/bin/python -m knowledge.pipelines.ingest_url --registry knowledge/registry.json
    .venv/bin/python -m knowledge.pipelines.ingest_url --stats
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx
import trafilatura

from knowledge.pipelines._common import (
    REPO_ROOT,
    KNOWLEDGE_DIR,
    SOURCES_DIR,
    already_ingested,
    category_dir,
    content_hash,
    load_manifest,
    manifest_entry,
    save_manifest,
    write_source,
)


DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

# Some sites (Investopedia, Morgan Stanley) require browser-like Accept headers
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="123", "Not(A:Brand";v="8"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def _is_pdf(resp: httpx.Response, url: str) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    if "application/pdf" in ctype:
        return True
    if url.lower().endswith(".pdf"):
        return True
    # Some servers return text/html for PDFs; sniff first bytes
    return resp.content[:4] == b"%PDF"


def _extract_html(html_text: str, url: str) -> tuple[str, dict]:
    """Run trafilatura. Returns (markdown_body, metadata_dict)."""
    body = trafilatura.extract(
        html_text,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    ) or ""

    meta_obj = trafilatura.extract_metadata(html_text, default_url=url)
    meta: dict = {}
    if meta_obj:
        meta = {
            "title":    (meta_obj.title or "").strip(),
            "author":   (meta_obj.author or "").strip(),
            "date":     (meta_obj.date or "").strip() if hasattr(meta_obj, "date") else "",
            "sitename": (meta_obj.sitename or "").strip() if hasattr(meta_obj, "sitename") else "",
            "language": (meta_obj.language or "").strip() if hasattr(meta_obj, "language") else "",
        }
    return body, meta


_GENERIC_PDF_TITLES = {"untitled", "printmgr file", "document", "doc1", "scanned document", ""}


def _extract_pdf(pdf_bytes: bytes) -> tuple[str, dict]:
    """Extract text from a PDF using pymupdf. Falls back to first heading-like line for title."""
    import fitz  # pymupdf
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    chunks: list[str] = []
    for page in doc:
        chunks.append(page.get_text("text"))
    body = "\n\n".join(c.strip() for c in chunks if c.strip())

    pdf_meta = doc.metadata or {}
    raw_title = (pdf_meta.get("title") or "").strip()

    # Fallback: if PDF metadata title is generic, pull first reasonable line of body
    if raw_title.lower() in _GENERIC_PDF_TITLES:
        for line in body.split("\n")[:20]:
            line = line.strip()
            if 8 < len(line) < 150 and any(c.isalpha() for c in line):
                raw_title = line
                break

    meta = {
        "title":  raw_title,
        "author": (pdf_meta.get("author") or "").strip(),
        "date":   (pdf_meta.get("creationDate") or "").strip(),
        "pages":  doc.page_count,
    }
    doc.close()
    return body, meta


async def fetch_url(client: httpx.AsyncClient, url: str) -> httpx.Response:
    resp = await client.get(url, timeout=30, headers=DEFAULT_HEADERS, follow_redirects=True)
    resp.raise_for_status()
    return resp


async def ingest_one(
    client: httpx.AsyncClient,
    *,
    url: str,
    category: str = "articles",
    source_name: str = "",
    authors: list[str] | None = None,
    language: str = "",
    manifest: dict | None = None,
) -> dict:
    """Fetch + extract + write. Returns a result dict for the caller."""
    result = {"url": url, "status": "pending", "id": None, "path": None, "title": None, "error": None}

    if manifest is None:
        manifest = load_manifest()

    try:
        resp = await fetch_url(client, url)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"fetch failed: {e}"
        return result

    parsed = urlparse(url)
    source_url = f"{parsed.scheme}://{parsed.netloc}"
    inferred_source = source_name or parsed.netloc.replace("www.", "")

    try:
        if _is_pdf(resp, url):
            body, ex_meta = _extract_pdf(resp.content)
            doc_type = "pdf"
        else:
            body, ex_meta = _extract_html(resp.text, url)
            doc_type = "article" if category == "articles" else category.rstrip("s")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"extraction failed: {type(e).__name__}: {e}"
        return result

    if not body or len(body) < 100:
        result["status"] = "error"
        result["error"] = f"extracted body too short ({len(body) if body else 0} chars)"
        return result

    title = ex_meta.get("title") or parsed.path.rstrip("/").split("/")[-1] or inferred_source
    chash = content_hash(url, title)

    if already_ingested(manifest, chash):
        result["status"] = "skipped"
        result["title"] = title
        return result

    final_authors = authors or ([ex_meta["author"]] if ex_meta.get("author") else [])
    final_language = language or ex_meta.get("language") or "en"

    sid, fp = write_source(
        category=category,
        source_name=inferred_source,
        title=title,
        body=body,
        url=url,
        source_url=source_url,
        pub_date=ex_meta.get("date", ""),
        authors=final_authors,
        language=final_language,
        doc_type=doc_type,
        extra={"pages": ex_meta["pages"]} if "pages" in ex_meta else None,
    )

    manifest["ingested"][sid] = manifest_entry(
        source_name=inferred_source,
        source_url=source_url,
        url=url,
        title=title,
        pub_date=ex_meta.get("date", ""),
        chash=chash,
        path=fp,
        category=category,
        doc_type=doc_type,
    )

    result.update(status="ingested", id=sid, path=str(fp.relative_to(REPO_ROOT)), title=title)
    return result


async def ingest_urls(items: list[dict], concurrency: int = 4) -> list[dict]:
    """Ingest a list of {url, category, source, authors, language} items in parallel."""
    manifest = load_manifest()
    semaphore = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient() as client:
        async def run_one(item: dict) -> dict:
            async with semaphore:
                return await ingest_one(
                    client,
                    url=item["url"],
                    category=item.get("category", "articles"),
                    source_name=item.get("source", ""),
                    authors=item.get("authors"),
                    language=item.get("language", ""),
                    manifest=manifest,
                )

        results = await asyncio.gather(*[run_one(item) for item in items])

    save_manifest(manifest)
    return results


def _print_results(results: list[dict]) -> None:
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        emoji = {"ingested": "✓", "skipped": "·", "error": "✗"}.get(r["status"], "?")
        title_short = (r.get("title") or "")[:70]
        if r["status"] == "error":
            print(f"  {emoji} {r['url']} — {r['error']}")
        else:
            print(f"  {emoji} {title_short}  ({r['url']})")
    print("\nSummary:")
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("url", nargs="?", help="Single URL to ingest")
    parser.add_argument("--category", default="articles", help="Source category folder")
    parser.add_argument("--source", default="", help="Override source name")
    parser.add_argument("--authors", default="", help="Comma-separated author list")
    parser.add_argument("--language", default="", help="Language code (en, vi, ...)")
    parser.add_argument("--registry", help="Path to JSON registry of URLs to ingest")
    parser.add_argument("--stats", action="store_true", help="Print manifest stats and exit")
    args = parser.parse_args()

    if args.stats:
        m = load_manifest()
        s = m.get("stats", {})
        print(f"Last run: {m.get('last_run')}")
        print(f"Total sources: {s.get('total_sources', 0)}")
        for t, n in (s.get("by_type") or {}).items():
            print(f"  {t}: {n}")
        return 0

    items: list[dict]
    if args.registry:
        reg_path = Path(args.registry)
        if not reg_path.is_absolute():
            reg_path = REPO_ROOT / reg_path
        if not reg_path.exists():
            print(f"Registry not found: {reg_path}", file=sys.stderr)
            return 1
        registry = json.loads(reg_path.read_text(encoding="utf-8"))
        items = []
        for entry in registry.get("sources", []):
            common = {
                "category": entry.get("category", "articles"),
                "source": entry.get("source_name") or entry.get("source", ""),
                "authors": entry.get("authors"),
                "language": entry.get("language", ""),
            }
            for url in entry.get("urls", []):
                items.append({**common, "url": url})
        print(f"Ingesting {len(items)} URLs from registry...")
    elif args.url:
        items = [{
            "url": args.url,
            "category": args.category,
            "source": args.source,
            "authors": [a.strip() for a in args.authors.split(",") if a.strip()] or None,
            "language": args.language,
        }]
    else:
        parser.print_help()
        return 1

    results = asyncio.run(ingest_urls(items))
    _print_results(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
