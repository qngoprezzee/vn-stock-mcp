"""Unified folder ingester: walks a directory, classifies files by extension,
and dispatches each to the right pipeline (PDF, EPUB, markdown, image).

This is the one-command bootstrap for adding a mixed folder of materials
(books in PDF + EPUB, your own markdown notes, scanned page screenshots).

Usage:
    # Default: process PDF, EPUB, MD. Skip images.
    .venv/bin/python -m knowledge.pipelines.ingest_folder ~/Documents/investing-library/ \\
        --category books

    # Include images via OCR (requires Tesseract)
    .venv/bin/python -m knowledge.pipelines.ingest_folder ~/Documents/investing-library/ \\
        --category books --ocr --ocr-lang "eng+vie"

    # Dry-run: classify files without ingesting
    .venv/bin/python -m knowledge.pipelines.ingest_folder ~/Documents/library/ --dry-run

    # Filter to specific extensions
    .venv/bin/python -m knowledge.pipelines.ingest_folder ~/Documents/library/ --only pdf,epub
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from knowledge.pipelines._common import load_manifest, save_manifest


_PDF_EXTS   = {".pdf"}
_EPUB_EXTS  = {".epub"}
_MD_EXTS    = {".md", ".markdown"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def _classify(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in _PDF_EXTS:   return "pdf"
    if ext in _EPUB_EXTS:  return "epub"
    if ext in _MD_EXTS:    return "md"
    if ext in _IMAGE_EXTS: return "image"
    return None


def _walk(folder: Path) -> dict[str, list[Path]]:
    by_type: dict[str, list[Path]] = {"pdf": [], "epub": [], "md": [], "image": [], "skip": []}
    for p in folder.rglob("*"):
        if not p.is_file():
            continue
        # skip hidden files (.DS_Store etc.)
        if any(part.startswith(".") for part in p.relative_to(folder).parts):
            continue
        ftype = _classify(p)
        if ftype:
            by_type[ftype].append(p)
        else:
            by_type["skip"].append(p)
    return by_type


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("folder", help="Folder to ingest")
    parser.add_argument("--category", default="books",
                        choices=["articles", "books", "blogs", "filings", "transcripts", "papers", "regulatory"])
    parser.add_argument("--source",   default="")
    parser.add_argument("--authors",  default="")
    parser.add_argument("--language", default="")
    parser.add_argument("--ocr",      action="store_true", help="OCR images with Tesseract (off by default)")
    parser.add_argument("--ocr-lang", default="eng",       help="Tesseract lang code (e.g. eng, eng+vie)")
    parser.add_argument("--only",     default="",          help="Comma-separated subset: pdf,epub,md,image")
    parser.add_argument("--dry-run",  action="store_true", help="Classify files without ingesting")
    args = parser.parse_args()

    folder = Path(args.folder).expanduser().resolve()
    if not folder.is_dir():
        print(f"Not a directory: {folder}", file=sys.stderr)
        return 1

    only = set(t.strip() for t in args.only.split(",") if t.strip()) if args.only else None

    print(f"Scanning {folder}...")
    by_type = _walk(folder)

    print(f"\nFound:")
    print(f"  PDFs:   {len(by_type['pdf'])}")
    print(f"  EPUBs:  {len(by_type['epub'])}")
    print(f"  MDs:    {len(by_type['md'])}")
    print(f"  Images: {len(by_type['image'])}{' (OCR will run)' if args.ocr else ' (skipping — pass --ocr to include)'}")
    print(f"  Skipped (unsupported): {len(by_type['skip'])}")

    if args.dry_run:
        print("\n--dry-run: no ingestion performed.")
        for kind in ("pdf", "epub", "md", "image"):
            if by_type[kind]:
                print(f"\n  Would ingest as {kind}:")
                for p in by_type[kind][:10]:
                    print(f"    {p.relative_to(folder)}")
                if len(by_type[kind]) > 10:
                    print(f"    ... and {len(by_type[kind]) - 10} more")
        if by_type["skip"]:
            unsupported_exts = {p.suffix.lower() for p in by_type["skip"]}
            print(f"\n  Unsupported extensions encountered: {sorted(unsupported_exts)}")
        return 0

    # Build dispatch list
    targets: list[tuple[str, Path]] = []
    for kind in ("pdf", "epub", "md", "image"):
        if only and kind not in only:
            continue
        if kind == "image" and not args.ocr:
            continue
        for p in by_type[kind]:
            targets.append((kind, p))

    if not targets:
        print("\nNothing to ingest with current flags.")
        return 0

    print(f"\nIngesting {len(targets)} file(s)...\n")

    # Lazy-import each pipeline so missing optional deps don't crash unrelated paths
    manifest = load_manifest()
    counters = {"ingested": 0, "skipped": 0, "error": 0}

    authors_list = [a.strip() for a in args.authors.split(",") if a.strip()] or None

    for kind, path in targets:
        rel = path.relative_to(folder)
        print(f"  [{kind:5}] {rel}")

        try:
            if kind == "pdf":
                from knowledge.pipelines.ingest_pdf import ingest_one_pdf
                r = ingest_one_pdf(
                    path, category=args.category, source_name=args.source,
                    authors=authors_list, language=args.language, pub_date="",
                    manifest=manifest,
                )
            elif kind == "epub":
                # ingest_epub doesn't expose a single-file helper, so we inline the same logic
                from knowledge.pipelines.ingest_epub import _extract_epub
                from knowledge.pipelines._common import (
                    content_hash, already_ingested, write_source, manifest_entry,
                )
                body, ex_meta = _extract_epub(path)
                if not body or len(body) < 200:
                    r = {"status": "error", "error": "body too short"}
                else:
                    title = ex_meta.get("title") or path.stem
                    inferred = args.source or title
                    chash = content_hash(str(path), title)
                    if already_ingested(manifest, chash):
                        r = {"status": "skipped", "title": title}
                    else:
                        authors = authors_list or ([ex_meta["author"]] if ex_meta.get("author") else [])
                        sid, fp = write_source(
                            category=args.category, source_name=inferred, title=title, body=body,
                            url=f"file://{path}", source_url="", pub_date=ex_meta.get("date", ""),
                            authors=authors, language=args.language or ex_meta.get("language") or "en",
                            doc_type="epub",
                            extra={"original_path": str(path), "chapters": ex_meta["chapters"]},
                        )
                        manifest["ingested"][sid] = manifest_entry(
                            source_name=inferred, source_url="", url=f"file://{path}",
                            title=title, pub_date=ex_meta.get("date", ""), chash=chash,
                            path=fp, category=args.category, doc_type="epub",
                        )
                        r = {"status": "ingested", "title": title}
            elif kind == "md":
                from knowledge.pipelines.ingest_md import ingest_one_md
                r = ingest_one_md(path, category=args.category, source_name=args.source,
                                  authors=authors_list, language=args.language, manifest=manifest)
            elif kind == "image":
                if not shutil.which("tesseract"):
                    print(f"      ✗ Tesseract not installed — skipping images")
                    counters["error"] += 1
                    continue
                from knowledge.pipelines.ingest_image import ingest_one_image
                r = ingest_one_image(
                    path, category=args.category, source_name=args.source,
                    authors=authors_list, language=args.language,
                    lang_packs=args.ocr_lang, manifest=manifest,
                )
            else:
                r = {"status": "error", "error": "unknown kind"}
        except Exception as e:
            r = {"status": "error", "error": f"{type(e).__name__}: {e}"}

        status = r.get("status", "error")
        counters[status] = counters.get(status, 0) + 1
        emoji = {"ingested": "✓", "skipped": "·", "error": "✗"}.get(status, "?")
        note  = r.get("title") or r.get("error") or ""
        print(f"      {emoji} {note}")

    save_manifest(manifest)

    print("\nSummary:")
    for status, n in counters.items():
        print(f"  {status}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
