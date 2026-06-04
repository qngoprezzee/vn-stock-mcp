"""Ingest images via OCR (Tesseract) — for screenshots of articles, scanned book pages, etc.

Requires Tesseract installed locally:
    brew install tesseract tesseract-lang

For Vietnamese content, the 'vie' language pack is bundled with tesseract-lang.

Usage:
    .venv/bin/python -m knowledge.pipelines.ingest_image ~/Downloads/screenshot.png \\
        --category articles --source "Bloomberg Screenshot" --lang eng+vie

    .venv/bin/python -m knowledge.pipelines.ingest_image ~/Documents/scanned-pages/ \\
        --category books --source "Margin of Safety" --lang eng

Caveats:
    - Best for images that ARE text (screenshots, scans). For chart-only images
      OCR returns little signal.
    - Tesseract on Vietnamese is decent but lower quality than English.
    - For high-fidelity OCR, consider PaddleOCR or Apple Vision (mac-only).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from PIL import Image

from knowledge.pipelines._common import (
    REPO_ROOT,
    already_ingested,
    content_hash,
    load_manifest,
    manifest_entry,
    save_manifest,
    write_source,
)


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def _check_tesseract() -> bool:
    return shutil.which("tesseract") is not None


def _ocr_image(img_path: Path, lang: str = "eng") -> tuple[str, dict]:
    import pytesseract
    img = Image.open(img_path)
    text = pytesseract.image_to_string(img, lang=lang)
    meta = {
        "width":  img.size[0],
        "height": img.size[1],
        "lang":   lang,
    }
    return text.strip(), meta


def ingest_one_image(
    img_path: Path,
    *,
    category: str,
    source_name: str,
    authors: list[str] | None,
    language: str,
    lang_packs: str,
    manifest: dict,
) -> dict:
    result = {"path": str(img_path), "status": "pending", "title": None, "error": None}

    if not img_path.exists():
        result["status"] = "error"
        result["error"] = "file not found"
        return result

    try:
        body, ex_meta = _ocr_image(img_path, lang=lang_packs)
    except Exception as e:
        result["status"] = "error"
        result["error"] = f"OCR failed: {type(e).__name__}: {e}"
        return result

    if not body or len(body) < 100:
        result["status"] = "error"
        result["error"] = f"OCR produced too little text ({len(body)} chars) — image may not contain readable text"
        return result

    title = img_path.stem.replace("_", " ").replace("-", " ").strip().title()
    inferred_source = source_name or "Image Capture"
    chash = content_hash(str(img_path), title)

    if already_ingested(manifest, chash):
        result["status"] = "skipped"
        result["title"] = title
        return result

    sid, fp = write_source(
        category=category,
        source_name=inferred_source,
        title=title,
        body=body,
        url=f"file://{img_path}",
        source_url="",
        pub_date="",
        authors=authors or [],
        language=language or "en",
        doc_type="image-ocr",
        extra={
            "original_path": str(img_path),
            "image_width":   ex_meta["width"],
            "image_height":  ex_meta["height"],
            "ocr_lang":      ex_meta["lang"],
        },
    )
    manifest["ingested"][sid] = manifest_entry(
        source_name=inferred_source,
        source_url="",
        url=f"file://{img_path}",
        title=title,
        pub_date="",
        chash=chash,
        path=fp,
        category=category,
        doc_type="image-ocr",
    )

    result.update(status="ingested", title=title, words=len(body.split()))
    return result


def _collect_images(inputs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in inputs:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            for ext in _IMAGE_EXTS:
                paths.extend(sorted(p.rglob(f"*{ext}")))
        elif p.is_file() and p.suffix.lower() in _IMAGE_EXTS:
            paths.append(p)
    seen, unique = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("paths", nargs="+", help="Image file paths or directories")
    parser.add_argument("--category", default="articles",
                        choices=["articles", "books", "blogs", "filings", "transcripts", "papers", "regulatory"])
    parser.add_argument("--source",   default="")
    parser.add_argument("--authors",  default="")
    parser.add_argument("--language", default="", help="Source language tag (en, vi)")
    parser.add_argument("--lang",     default="eng",
                        help="Tesseract language pack(s): 'eng', 'vie', or 'eng+vie' for mixed (default: eng)")
    args = parser.parse_args()

    if not _check_tesseract():
        print("Tesseract not installed.", file=sys.stderr)
        print("Install: brew install tesseract tesseract-lang", file=sys.stderr)
        return 1

    images = _collect_images(args.paths)
    if not images:
        print("No images found.", file=sys.stderr)
        return 1

    print(f"OCR-ingesting {len(images)} image(s) with lang='{args.lang}'...")
    manifest = load_manifest()
    authors = [a.strip() for a in args.authors.split(",") if a.strip()] or None

    results = []
    for img in images:
        results.append(ingest_one_image(
            img, category=args.category, source_name=args.source,
            authors=authors, language=args.language, lang_packs=args.lang, manifest=manifest,
        ))

    save_manifest(manifest)

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        emoji = {"ingested": "✓", "skipped": "·", "error": "✗"}.get(r["status"], "?")
        name = (r.get("title") or Path(r["path"]).name)[:65]
        suffix = f" ({r['words']:,} words OCR'd)" if r.get("words") else ""
        err = f" — {r['error']}" if r.get("error") else ""
        print(f"  {emoji} {name}{suffix}{err}")
    print("\nSummary:")
    for status, n in sorted(counts.items()):
        print(f"  {status}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
