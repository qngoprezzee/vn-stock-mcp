"""Markdown table builders.

The bulk of the current handler code inlines pipe-separated markdown rows.
Prefer these helpers for NEW handlers; the existing 76+ inline patterns
migrate opportunistically.
"""
from __future__ import annotations


def render_table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    """Render a GitHub-flavoured markdown table.

    Args:
        headers: column header labels.
        rows: pre-formatted cell strings (one row = one list, same length as headers).
        aligns: per-column alignment, one of 'left'/'right'/'center'. Defaults to left.

    Returns a multi-line string. Callers append to their own line list.
    """
    if not headers:
        return ""
    n = len(headers)
    if aligns is None:
        aligns = ["left"] * n
    align_row = []
    for a in aligns:
        if a == "right":
            align_row.append("---:")
        elif a == "center":
            align_row.append(":---:")
        else:
            align_row.append("---")

    out = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(align_row) + "|",
    ]
    for row in rows:
        cells = [str(c) for c in row]
        if len(cells) < n:
            cells += ["—"] * (n - len(cells))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)
