"""Ingest a local EPUB file into the knowledge base.

EPUBs are essentially a ZIP of XHTML files + metadata. We extract each chapter
as a `## Section heading` in one markdown file so chapter structure is preserved
for the chunker downstream.

Usage:
    .venv/bin/python -m knowledge.pipelines.ingest_epub /path/to/book.epub \\
        --category books \\
        --source "The Outsiders" \\
        --authors "William Thorndike"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bs4 import BeautifulSoup
from ebooklib import epub, ITEM_DOCUMENT

from knowledge.pipelines._common import (
    REPO_ROOT,
    already_ingested,
    content_hash,
    load_manifest,
    manifest_entry,
    save_manifest,
    write_source,
)


def _html_to_markdown(html: str) -> str:
    """Strip HTML/CSS, preserve heading + paragraph structure, return plain markdown."""
    soup = BeautifulSoup(html, "html.parser")

    # Headings
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            prefix = "#" * level
            h.replace_with(f"\n\n{prefix} {h.get_text(strip=True)}\n\n")

    # Paragraphs
    for p in soup.find_all("p"):
        p.replace_with(f"{p.get_text(' ', strip=True)}\n\n")

    # Lists
    for li in soup.find_all("li"):
        li.replace_with(f"- {li.get_text(' ', strip=True)}\n")

    text = soup.get_text()
    # Collapse excessive blank lines
    lines = [ln.rstrip() for ln in text.split("\n")]
    cleaned: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            cleaned.append(ln)
            blank = 0
        else:
            blank += 1
            if blank < 2:
                cleaned.append("")
    return "\n".join(cleaned).strip()


def _extract_epub(epub_path: Path) -> tuple[str, dict]:
    """Return (markdown_body, metadata) for the whole book."""
    book = epub.read_epub(str(epub_path), options={"ignore_ncx": True})

    # Metadata via Dublin Core
    def _meta(field: str) -> str:
        values = book.get_metadata("DC", field)
        return values[0][0].strip() if values else ""

    title  = _meta("title") or epub_path.stem
    author = _meta("creator")
    lang   = _meta("language") or "en"
    pub    = _meta("date")

    chapters: list[str] = []
    for item in book.get_items_of_type(ITEM_DOCUMENT):
        try:
            body = item.get_content().decode("utf-8", errors="replace")
        except Exception:
            continue
        md = _html_to_markdown(body)
        if md and len(md) > 50:
            chapters.append(md)

    full_body = "\n\n---\n\n".join(chapters)

    meta = {
        "title":   title,
        "author":  author,
        "language": lang,
        "date":    pub,
        "chapters": len(chapters),
    }
    return full_body, meta


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("path", help="Local EPUB file path")
    parser.add_argument("--category", default="books",
                        choices=["articles", "books", "blogs", "filings", "transcripts", "papers", "regulatory"])
    parser.add_argument("--source",   default="")
    parser.add_argument("--authors",  default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--pub-date", default="")
    args = parser.parse_args()

    epub_path = Path(args.path).expanduser().resolve()
    if not epub_path.exists():
        print(f"File not found: {epub_path}", file=sys.stderr)
        return 1

    try:
        body, ex_meta = _extract_epub(epub_path)
    except Exception as e:
        print(f"EPUB extraction failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if not body or len(body) < 200:
        print(f"Extracted body too short ({len(body) if body else 0} chars).", file=sys.stderr)
        return 1

    manifest = load_manifest()
    title = ex_meta.get("title") or epub_path.stem
    inferred_source = args.source or title
    chash = content_hash(str(epub_path), title)

    if already_ingested(manifest, chash):
        print(f"Already ingested (content_hash={chash}). Skipping.")
        return 0

    authors = [a.strip() for a in args.authors.split(",") if a.strip()] or (
        [ex_meta["author"]] if ex_meta.get("author") else []
    )

    sid, fp = write_source(
        category=args.category,
        source_name=inferred_source,
        title=title,
        body=body,
        url=f"file://{epub_path}",
        source_url="",
        pub_date=args.pub_date or ex_meta.get("date", ""),
        authors=authors,
        language=args.language or ex_meta.get("language") or "en",
        doc_type="epub",
        extra={"original_path": str(epub_path), "chapters": ex_meta["chapters"]},
    )
    manifest["ingested"][sid] = manifest_entry(
        source_name=inferred_source,
        source_url="",
        url=f"file://{epub_path}",
        title=title,
        pub_date=args.pub_date or ex_meta.get("date", ""),
        chash=chash,
        path=fp,
        category=args.category,
        doc_type="epub",
    )
    save_manifest(manifest)

    print(f"✓ {title}")
    print(f"  Chapters: {ex_meta['chapters']}  |  Words: {len(body.split()):,}")
    print(f"  Saved:    {fp.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
