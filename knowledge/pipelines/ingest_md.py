"""Ingest a local markdown file (or directory of them) into the knowledge base.

If the file already has YAML frontmatter (Obsidian, Jekyll, etc.) we preserve
known fields. Otherwise we infer title from the first heading or filename.

Usage:
    .venv/bin/python -m knowledge.pipelines.ingest_md ~/Notes/fpt-analysis.md \\
        --category articles --source "Personal Notes"

    .venv/bin/python -m knowledge.pipelines.ingest_md ~/Obsidian/Investing/ --category articles
"""
from __future__ import annotations

import argparse
import re
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


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_existing_frontmatter(text: str) -> tuple[dict, str]:
    """If the markdown file starts with YAML frontmatter, return (fields, body_without_fm)."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text

    fm_block = m.group(1)
    body = text[m.end():]
    fields: dict = {}

    for line in fm_block.split("\n"):
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if value.startswith("[") and value.endswith("]"):
            # crude list parse
            inner = value[1:-1].strip()
            fields[key] = [v.strip().strip('"').strip("'") for v in inner.split(",") if v.strip()]
        else:
            fields[key] = value
    return fields, body


def _infer_title(body: str, filename: str) -> str:
    for line in body.split("\n")[:30]:
        m = re.match(r"^#\s+(.+)", line.strip())
        if m:
            return m.group(1).strip()
    return filename.replace("_", " ").replace("-", " ").strip().title()


def ingest_one_md(
    md_path: Path,
    *,
    category: str,
    source_name: str,
    authors: list[str] | None,
    language: str,
    manifest: dict,
) -> dict:
    result = {"path": str(md_path), "status": "pending", "title": None, "error": None}

    if not md_path.exists():
        result["status"] = "error"
        result["error"] = "file not found"
        return result

    try:
        text = md_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"read failed: {e}"
        return result

    if len(text.strip()) < 50:
        result["status"] = "error"
        result["error"] = "file too short"
        return result

    existing_fm, body = _parse_existing_frontmatter(text)
    title = (existing_fm.get("title") or _infer_title(body, md_path.stem))

    inferred_source = source_name or existing_fm.get("source") or md_path.parent.name or "Personal Markdown"
    chash = content_hash(str(md_path), title)

    if already_ingested(manifest, chash):
        result["status"] = "skipped"
        result["title"] = title
        return result

    final_authors = authors or (
        existing_fm.get("authors") if isinstance(existing_fm.get("authors"), list) else None
    ) or []

    sid, fp = write_source(
        category=category,
        source_name=inferred_source,
        title=title,
        body=body.strip(),
        url=f"file://{md_path}",
        source_url="",
        pub_date=existing_fm.get("date") or existing_fm.get("pub_date") or "",
        authors=final_authors,
        language=language or existing_fm.get("language") or "en",
        doc_type="markdown",
        extra={"original_path": str(md_path)},
    )
    manifest["ingested"][sid] = manifest_entry(
        source_name=inferred_source,
        source_url="",
        url=f"file://{md_path}",
        title=title,
        pub_date=existing_fm.get("date") or "",
        chash=chash,
        path=fp,
        category=category,
        doc_type="markdown",
    )

    result.update(status="ingested", title=title, words=len(body.split()))
    return result


def _collect_md(inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in inputs:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            paths.extend(sorted(p.rglob("*.md")))
            paths.extend(sorted(p.rglob("*.markdown")))
        elif p.is_file() and p.suffix.lower() in (".md", ".markdown"):
            paths.append(p)
    seen, unique = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("paths", nargs="+", help="Markdown file paths or directories")
    parser.add_argument("--category", default="articles",
                        choices=["articles", "books", "blogs", "filings", "transcripts", "papers", "regulatory"])
    parser.add_argument("--source",   default="")
    parser.add_argument("--authors",  default="")
    parser.add_argument("--language", default="")
    args = parser.parse_args()

    md_paths = _collect_md(args.paths)
    if not md_paths:
        print("No markdown files found.", file=sys.stderr)
        return 1

    print(f"Ingesting {len(md_paths)} markdown file(s)...")
    manifest = load_manifest()
    authors = [a.strip() for a in args.authors.split(",") if a.strip()] or None

    results = []
    for p in md_paths:
        results.append(ingest_one_md(p, category=args.category, source_name=args.source,
                                     authors=authors, language=args.language, manifest=manifest))

    save_manifest(manifest)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        emoji = {"ingested": "✓", "skipped": "·", "error": "✗"}.get(r["status"], "?")
        name = (r.get("title") or Path(r["path"]).name)[:70]
        suffix = f" ({r['words']:,} words)" if r.get("words") else ""
        err = f" — {r['error']}" if r.get("error") else ""
        print(f"  {emoji} {name}{suffix}{err}")
    print("\nSummary:")
    for status, n in sorted(counts.items()):
        print(f"  {status}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
