"""K19: LLM sentiment scoring pipeline.

Usage:
    python -m knowledge.pipelines.score_sentiment_llm [--dry-run] [--limit N] [--batch-size N]

Reads all article .md files in knowledge/sources/articles/ that are missing a
`sentiment_llm` frontmatter field, batches them, calls GPT-4o-mini for scores,
and writes `sentiment_llm: {...}` back into frontmatter. Idempotent.

Requires: OPENAI_API_KEY env var.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTICLES_DIR = REPO_ROOT / "knowledge" / "sources" / "articles"

_FRONTMATTER_END_RE = re.compile(r"\n---\n", re.MULTILINE)

_SYSTEM_PROMPT = """\
You are a Vietnamese equity research sentiment analyst.
Score each article's likely MARKET IMPACT for VN stock investors.
Return ONLY a valid JSON array — no prose, no markdown fences.

Score range: -1.0 (extremely bearish) to +1.0 (extremely bullish).
Keep reason to one short English sentence (≤15 words).

Examples:
  "Record profit, revenue up 40%" → {"score": 0.85, "reason": "Strong earnings beat signals sustained growth."}
  "Executive arrested for fraud"  → {"score": -0.90, "reason": "Fraud arrest destroys management credibility."}
  "Company holds routine AGM"     → {"score": 0.0, "reason": "Routine event with no price catalyst."}
  "Inflation report disappoints despite 'positive' CPI" → {"score": -0.5, "reason": "Misleading headline; underlying data is negative."}

Output schema:
[{"id": <int>, "score": <float>, "reason": "<str>"}]
"""


def _unscored_paths(limit: int | None) -> list[Path]:
    paths = sorted(ARTICLES_DIR.glob("*.md"), reverse=True)
    unscored = []
    for p in paths:
        text = p.read_text(encoding="utf-8", errors="replace")
        if "sentiment_llm:" not in text:
            unscored.append(p)
        if limit and len(unscored) >= limit:
            break
    return unscored


def _extract_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_END_RE.search(text)
    body = text[m.end():] if m else text
    title_m = re.search(r'^title:\s*"([^"]+)"', text, re.MULTILINE)
    title = title_m.group(1) if title_m else path.stem
    snippet = (body or "").strip()[:300].replace("\n", " ")
    return f"{title}. {snippet}"


def _patch_frontmatter(path: Path, llm_meta: dict) -> None:
    content = path.read_text(encoding="utf-8", errors="replace")
    m = _FRONTMATTER_END_RE.search(content)
    if not m:
        return  # malformed file; skip
    insert_pos = m.start()
    line = f'\nsentiment_llm: {json.dumps(llm_meta, ensure_ascii=False)}'
    content = content[:insert_pos] + line + content[insert_pos:]
    path.write_text(content, encoding="utf-8")


def _call_gpt(client, batch: list[dict]) -> list[dict]:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        max_tokens=512,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(batch, ensure_ascii=False)},
        ],
    )
    raw = response.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw).rstrip("`").strip()
    return json.loads(raw)


def run(dry_run: bool = False, limit: int | None = None, batch_size: int = 10) -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set. Export it and re-run.")

    try:
        import openai
    except ImportError:
        raise SystemExit("openai package not installed. Run: pip install openai>=1.40.0")

    client = openai.OpenAI(api_key=api_key)

    unscored = _unscored_paths(limit)
    if not unscored:
        print("All articles already scored. Nothing to do.")
        return

    print(f"Found {len(unscored)} unscored articles. Batch size: {batch_size}. Dry run: {dry_run}")

    today = __import__("datetime").date.today().isoformat()
    total_scored = 0
    total_errors = 0

    for batch_start in range(0, len(unscored), batch_size):
        batch_paths = unscored[batch_start: batch_start + batch_size]
        batch_input = [
            {"id": i, "text": _extract_text(p)}
            for i, p in enumerate(batch_paths)
        ]

        try:
            results = _call_gpt(client, batch_input)
            result_map = {r["id"]: r for r in results}
        except Exception as exc:
            print(f"  [batch {batch_start // batch_size + 1}] API error: {exc} — skipping batch")
            total_errors += len(batch_paths)
            time.sleep(2)
            continue

        for i, path in enumerate(batch_paths):
            r = result_map.get(i)
            if not r or not isinstance(r.get("score"), (int, float)):
                print(f"  SKIP {path.name} — bad result: {r}")
                total_errors += 1
                continue

            score = round(float(r["score"]), 3)
            reason = str(r.get("reason", ""))[:120]
            llm_meta = {
                "score":     score,
                "reason":    reason,
                "model":     "gpt-4o-mini",
                "scored_at": today,
            }

            if dry_run:
                print(f"  DRY {path.name}: score={score:+.2f} — {reason}")
            else:
                _patch_frontmatter(path, llm_meta)
                print(f"  ✓ {path.name}: score={score:+.2f} — {reason}")
            total_scored += 1

        if batch_start + batch_size < len(unscored):
            time.sleep(0.5)

    print(f"\nDone. Scored: {total_scored}  Errors/skipped: {total_errors}")
    if not dry_run and total_scored:
        print("Re-run `correlate_news_to_price` to see improved sentiment accuracy.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LLM sentiment backfill (K19)")
    parser.add_argument("--dry-run", action="store_true", help="Print scores without writing files")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of articles to process")
    parser.add_argument("--batch-size", type=int, default=10, help="Articles per API call (default 10)")
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit, batch_size=args.batch_size)
