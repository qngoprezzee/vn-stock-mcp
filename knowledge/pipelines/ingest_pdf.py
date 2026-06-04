"""Ingest local PDF files into the knowledge base.

Use this for PDFs you have on disk — books you've bought, broker research,
annual reports, papers downloaded from SSRN, etc. The original PDF stays
wherever you put it; only the extracted text goes into knowledge/sources/.

Usage:
    # Single book
    .venv/bin/python -m knowledge.pipelines.ingest_pdf /path/to/intelligent_investor.pdf \\
        --category books \\
        --source "The Intelligent Investor" \\
        --authors "Benjamin Graham" \\
        --pub-date "1949"

    # Annual report
    .venv/bin/python -m knowledge.pipelines.ingest_pdf ~/Downloads/fpt_annual_2024.pdf \\
        --category filings --source "FPT Corp 2024 Annual Report" --language vi

    # Batch (multiple files via shell glob)
    .venv/bin/python -m knowledge.pipelines.ingest_pdf ~/Downloads/*.pdf --category filings

    # Whole directory recursively
    .venv/bin/python -m knowledge.pipelines.ingest_pdf ~/Documents/investing-books/ --category books

    # Inspect existing ingest stats
    .venv/bin/python -m knowledge.pipelines.ingest_pdf --stats

Categories: articles, books, blogs, filings, transcripts, papers, regulatory
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fitz  # pymupdf

from knowledge.pipelines._common import (
    REPO_ROOT,
    already_ingested,
    content_hash,
    load_manifest,
    manifest_entry,
    save_manifest,
    write_source,
)


_GENERIC_PDF_TITLES = {"untitled", "printmgr file", "document", "doc1", "scanned document", ""}


def _extract_pdf(pdf_path: Path) -> tuple[str, dict]:
    """Extract text from a PDF. Returns (body, metadata).

    For scanned PDFs (no text layer), returns whatever little text exists +
    a warning flag in metadata so the caller can decide how to handle it.
    """
    doc = fitz.open(pdf_path)
    chunks: list[str] = []
    for page in doc:
        txt = page.get_text("text")
        if txt.strip():
            chunks.append(txt)
    body = "\n\n".join(c.strip() for c in chunks if c.strip())

    pdf_meta = doc.metadata or {}
    raw_title = (pdf_meta.get("title") or "").strip()

    # Fallback chain for title: PDF metadata → first heading-like line of body → filename
    if raw_title.lower() in _GENERIC_PDF_TITLES:
        for line in body.split("\n")[:30]:
            line = line.strip()
            if 8 < len(line) < 150 and any(c.isalpha() for c in line) and not line.endswith("."):
                raw_title = line
                break
    if raw_title.lower() in _GENERIC_PDF_TITLES:
        raw_title = pdf_path.stem.replace("_", " ").replace("-", " ").title()

    page_count = doc.page_count
    chars_per_page = (len(body) / page_count) if page_count > 0 else 0
    is_likely_scanned = page_count > 3 and chars_per_page < 50

    meta = {
        "title":            raw_title,
        "author":           (pdf_meta.get("author") or "").strip(),
        "date":             (pdf_meta.get("creationDate") or "").strip(),
        "pages":            page_count,
        "is_likely_scanned": is_likely_scanned,
        "chars_per_page":   round(chars_per_page),
    }
    doc.close()
    return body, meta


def _collect_pdf_paths(inputs: list[str]) -> list[Path]:
    """Expand inputs (files, directories, globs already-expanded by shell) into a flat PDF list."""
    paths: list[Path] = []
    for raw in inputs:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.pdf")))
        elif p.is_file() and p.suffix.lower() == ".pdf":
            paths.append(p)
        else:
            print(f"  ⚠️  Skipping (not a PDF or directory): {raw}", file=sys.stderr)
    # Dedupe (in case glob + dir overlap) while preserving order
    seen, unique = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def ingest_one_pdf(
    pdf_path: Path,
    *,
    category: str,
    source_name: str,
    authors: list[str] | None,
    language: str,
    pub_date: str,
    manifest: dict,
) -> dict:
    """Extract + write + register one PDF. Returns a result row."""
    result: dict = {"path": str(pdf_path), "status": "pending", "id": None, "title": None, "error": None}

    if not pdf_path.exists():
        result["status"] = "error"
        result["error"] = "file not found"
        return result

    try:
        body, ex_meta = _extract_pdf(pdf_path)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"extraction failed: {type(e).__name__}: {e}"
        return result

    if not body or len(body) < 100:
        result["status"] = "error"
        result["error"] = f"extracted body too short ({len(body) if body else 0} chars)"
        if ex_meta.get("is_likely_scanned"):
            result["error"] += " — looks like a scanned PDF; OCR not yet supported (see PLAN Task 5.1 future)"
        return result

    title = ex_meta.get("title") or pdf_path.stem
    inferred_source = source_name or title

    # Use file path as the dedup key so re-running the same file doesn't double-ingest
    chash = content_hash(str(pdf_path), title)

    if already_ingested(manifest, chash):
        result["status"] = "skipped"
        result["title"] = title
        return result

    final_authors = authors or ([ex_meta["author"]] if ex_meta.get("author") else [])

    sid, fp = write_source(
        category=category,
        source_name=inferred_source,
        title=title,
        body=body,
        url=f"file://{pdf_path}",
        source_url="",
        pub_date=pub_date or ex_meta.get("date", ""),
        authors=final_authors,
        language=language or "en",
        doc_type="pdf",
        extra={
            "pages":          ex_meta["pages"],
            "original_path":  str(pdf_path),
            "chars_per_page": ex_meta["chars_per_page"],
        },
    )

    manifest["ingested"][sid] = manifest_entry(
        source_name=inferred_source,
        source_url="",
        url=f"file://{pdf_path}",
        title=title,
        pub_date=pub_date or ex_meta.get("date", ""),
        chash=chash,
        path=fp,
        category=category,
        doc_type="pdf",
    )

    if ex_meta.get("is_likely_scanned"):
        result["warning"] = "PDF looks scanned — text extraction may be incomplete; consider OCR (future)"

    result.update(
        status="ingested",
        id=sid,
        title=title,
        pages=ex_meta["pages"],
        words=len(body.split()),
    )
    return result


def _print_results(results: list[dict]) -> None:
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
        emoji = {"ingested": "✓", "skipped": "·", "error": "✗"}.get(r["status"], "?")
        title_short = (r.get("title") or Path(r["path"]).name)[:65]
        if r["status"] == "error":
            print(f"  {emoji} {Path(r['path']).name} — {r['error']}")
        elif r["status"] == "ingested":
            warning = f"  ⚠ {r['warning']}" if r.get("warning") else ""
            print(f"  {emoji} {title_short}  ({r['pages']}pp, {r['words']:,} words){warning}")
        else:
            print(f"  {emoji} {title_short}  (already ingested)")
    print("\nSummary:")
    for status, count in sorted(by_status.items()):
        print(f"  {status}: {count}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("paths", nargs="*", help="PDF file paths, glob results, or directories")
    parser.add_argument("--category", default="books",
                        choices=["articles", "books", "blogs", "filings", "transcripts", "papers", "regulatory"],
                        help="Where to file the extracted markdown (default: books)")
    parser.add_argument("--source", default="", help="Source name. Defaults to PDF title or filename")
    parser.add_argument("--authors", default="", help="Comma-separated authors")
    parser.add_argument("--language", default="", help="Language code (en, vi). Defaults to en")
    parser.add_argument("--pub-date", default="", help="Original publication date, e.g. 1949 or 2024-Q4")
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

    if not args.paths:
        parser.print_help()
        return 1

    pdfs = _collect_pdf_paths(args.paths)
    if not pdfs:
        print("No PDF files found.", file=sys.stderr)
        return 1

    print(f"Ingesting {len(pdfs)} PDF(s)...")

    manifest = load_manifest()
    authors = [a.strip() for a in args.authors.split(",") if a.strip()] or None

    results = []
    for pdf in pdfs:
        result = ingest_one_pdf(
            pdf,
            category=args.category,
            source_name=args.source,
            authors=authors,
            language=args.language,
            pub_date=args.pub_date,
            manifest=manifest,
        )
        results.append(result)

    save_manifest(manifest)
    _print_results(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
