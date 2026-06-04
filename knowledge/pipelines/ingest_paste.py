"""Manual-paste ingest for paywalled sources (Bloomberg, FT, WSJ, paywalled blogs).

Paste flow:
    .venv/bin/python -m knowledge.pipelines.ingest_paste \\
        --title "Money Stuff: Crypto Is Now JPMorgan's Problem" \\
        --source "Bloomberg — Matt Levine" \\
        --url "https://www.bloomberg.com/..." \\
        --authors "Matt Levine" \\
        --category articles \\
        --file path/to/article.txt

Or pipe stdin:
    pbpaste | .venv/bin/python -m knowledge.pipelines.ingest_paste --title "..." --source "..." --url "..."
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from knowledge.pipelines._common import (
    REPO_ROOT,
    already_ingested,
    content_hash,
    load_manifest,
    manifest_entry,
    save_manifest,
    write_source,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--title",    required=True, help="Article / chapter title")
    parser.add_argument("--source",   required=True, help="Publication or author group, e.g. 'Bloomberg — Matt Levine'")
    parser.add_argument("--url",      default="",    help="Original URL (for citation; optional)")
    parser.add_argument("--authors",  default="",    help="Comma-separated authors")
    parser.add_argument("--language", default="en")
    parser.add_argument("--category", default="articles",
                        choices=["articles", "books", "blogs", "filings", "transcripts", "papers", "regulatory"])
    parser.add_argument("--pub-date", default="",    help="Original publication date, e.g. 2026-05-15")
    parser.add_argument("--file",     default="",    help="Path to text file (defaults to stdin)")
    args = parser.parse_args()

    if args.file:
        body = Path(args.file).read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            print("Paste article text, then press Ctrl-D when done:", file=sys.stderr)
        body = sys.stdin.read()

    body = body.strip()
    if len(body) < 100:
        print(f"Body too short ({len(body)} chars). Aborting.", file=sys.stderr)
        return 1

    manifest = load_manifest()
    chash = content_hash(args.url or args.title, args.title)
    if already_ingested(manifest, chash):
        print(f"Already ingested (content_hash={chash}). Skipping.")
        return 0

    authors = [a.strip() for a in args.authors.split(",") if a.strip()] or None

    sid, fp = write_source(
        category=args.category,
        source_name=args.source,
        title=args.title,
        body=body,
        url=args.url,
        source_url=args.url.split("/")[0] + "//" + args.url.split("/")[2] if args.url else "",
        pub_date=args.pub_date,
        authors=authors,
        language=args.language,
        doc_type=args.category.rstrip("s") if args.category != "articles" else "article",
    )

    manifest["ingested"][sid] = manifest_entry(
        source_name=args.source,
        source_url="",
        url=args.url,
        title=args.title,
        pub_date=args.pub_date,
        chash=chash,
        path=fp,
        category=args.category,
        doc_type=args.category.rstrip("s") if args.category != "articles" else "article",
    )
    save_manifest(manifest)

    print(f"✓ Saved: {fp.relative_to(REPO_ROOT)}")
    print(f"  ID:    {sid}")
    print(f"  Hash:  {chash}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
