"""K7 — Extract every passage where an author discusses a concept.

Two-stage pattern:
  Stage 1 (this script): grep + context window → writes a `_pending` markdown
  Stage 2 (Claude Code): the `vn-concept-extractor` skill reads the pending
                          file and synthesizes a wiki page

Usage:
    .venv/bin/python -m knowledge.pipelines.extract_concept \\
        --author "Warren Buffett" \\
        --concept "intrinsic value" \\
        --synonyms "intrinsic worth,intrinsic business value"

Then in Claude Code:
    /extract-concept intrinsic-value
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from knowledge.lib.corpus import REPO_ROOT, find_passages, iter_sources


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower())
    return s.strip("-")


def _slugify_author(author: str) -> str:
    """Buffett → 'buffett', 'Warren Buffett' → 'buffett'"""
    parts = author.lower().split()
    return parts[-1] if parts else "author"


def _format_passage_block(p: dict, idx: int) -> str:
    pub_year = ""
    if p.get("pub_date"):
        m = re.search(r"\b(20\d{2}|19\d{2})\b", p["pub_date"])
        if m:
            pub_year = m.group(1)

    return f"""### Passage {idx} — {p['title']}{f" ({pub_year})" if pub_year else ""}

*Source: `{p['source_id']}` | Matched keyword: **{p['keyword']}***

> {p['passage'].replace(chr(10), chr(10) + '> ')}

---
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--author", required=True,
                        help="Author to search within (substring match, case-insensitive)")
    parser.add_argument("--concept", required=True,
                        help='Concept name, e.g. "intrinsic value"')
    parser.add_argument("--synonyms", default="",
                        help="Comma-separated synonyms / variants")
    parser.add_argument("--category", default="",
                        help="Optional category filter (books, blogs, papers)")
    parser.add_argument("--context", type=int, default=2,
                        help="Paragraphs of surrounding context (default 2)")
    parser.add_argument("--max-per-source", type=int, default=5,
                        help="Max matches per source file (default 5)")
    parser.add_argument("--out-dir", default="knowledge/wiki",
                        help="Output base directory (default knowledge/wiki)")
    args = parser.parse_args()

    keywords = [args.concept] + [s.strip() for s in args.synonyms.split(",") if s.strip()]

    sources = list(iter_sources(
        author=args.author,
        category=args.category if args.category else None,
    ))

    if not sources:
        print(f"No sources found for author='{args.author}'.", file=sys.stderr)
        print("Hint: check `knowledge/manifest.json` for available authors.", file=sys.stderr)
        return 1

    print(f"Searching {len(sources)} source(s) by {args.author}...")
    print(f"  Keywords: {keywords}")

    passages = find_passages(
        sources, keywords,
        context_paragraphs=args.context,
        max_matches_per_source=args.max_per_source,
    )

    if not passages:
        print(f"No passages found for concept '{args.concept}'.")
        return 0

    # Sort by year (newest first) using pub_date
    def _year(p):
        m = re.search(r"\b(20\d{2}|19\d{2})\b", p.get("pub_date", "") or "")
        return int(m.group(1)) if m else 0
    passages.sort(key=_year, reverse=True)

    print(f"  Found {len(passages)} passage(s)")

    # Write the pending file
    author_slug = _slugify_author(args.author)
    concept_slug = _slug(args.concept)
    out_dir = REPO_ROOT / args.out_dir / f"{author_slug}-concepts"
    out_dir.mkdir(parents=True, exist_ok=True)

    pending_path = out_dir / f"_pending_{concept_slug}.md"

    header = f"""---
concept: {args.concept}
author: {args.author}
keywords: [{', '.join(repr(k) for k in keywords)}]
source_ids: [{', '.join(repr(p['source_id']) for p in passages)}]
passage_count: {len(passages)}
status: pending_synthesis
---

# Concept Extraction — {args.concept.title()} ({args.author})

**Status:** awaiting synthesis. Run `/extract-concept {concept_slug}` in Claude Code,
or invoke the `vn-concept-extractor` skill, to turn the raw passages below into the
final wiki page at `{out_dir.relative_to(REPO_ROOT)}/{concept_slug}.md`.

**Inputs gathered:**
- Author filter: `{args.author}`
- Concept: `{args.concept}`
- Synonyms/keywords: `{keywords}`
- Sources searched: {len(sources)}
- Passages found: {len(passages)}

---

## Raw Passages

"""
    body_parts = [_format_passage_block(p, i + 1) for i, p in enumerate(passages)]
    pending_path.write_text(header + "\n".join(body_parts), encoding="utf-8")

    print(f"\nPending file written: {pending_path.relative_to(REPO_ROOT)}")
    print(f"\nNext: invoke the vn-concept-extractor skill (or /extract-concept {concept_slug}) in Claude Code.")
    print(f"It will synthesize the final wiki page at:")
    print(f"  {(out_dir / f'{concept_slug}.md').relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
