"""Keyword-based sentiment scoring for VN + EN financial headlines.

MVP approach: count positive vs. negative keywords; return a normalized score
in [-1, +1]. Crude, but catches the obvious cases at zero cost. Replace with
LLM-scored sentiment via Claude Code at ingest time when ready.

Score interpretation:
    > +0.3  →  strongly positive
    +0.05 to +0.3  →  mildly positive
    -0.05 to +0.05 →  neutral
    -0.3 to -0.05  →  mildly negative
    < -0.3  →  strongly negative
"""
from __future__ import annotations

import re
import unicodedata


# Vietnamese-financial positive keywords. Match against lowercased + ASCII-folded text.
_POSITIVE_KEYWORDS_VI: set[str] = {
    "tang truong",       # growth
    "tang manh",         # strong rise
    "tang",              # rise / increase (common; ambiguous with "khong tang")
    "loi nhuan",         # profit
    "loi nhuan ky luc",  # record profit
    "ky luc",            # record
    "vuot",              # exceed
    "vuot ky vong",      # beat expectations
    "tich cuc",          # positive
    "kha quan",          # favorable
    "khoi sac",          # bright outlook
    "dat",               # achieve
    "hap dan",           # attractive
    "but pha",           # breakthrough
    "mua rong",          # net buy (foreign)
    "mua manh",          # strong buy
    "ki vong",           # expectation
    "thanh cong",        # success
    "cai thien",         # improve
    "tang cuong",        # strengthen
    "bat tang",          # gap up
    "hoi phuc",          # recovery
    "hoi sinh",          # revival
    "san cao",           # high (price)
    "lap dinh",          # hit peak
    "ron rang",          # bustling
    "song phai sinh",    # derivative wave (when net long)
}

_NEGATIVE_KEYWORDS_VI: set[str] = {
    "giam manh",         # strong decline
    "giam sau",          # deep decline
    "giam",              # decrease
    "lo",                # loss
    "thua lo",           # net loss
    "sut giam",          # drop
    "sut",               # drop
    "tieu cuc",          # negative
    "rui ro",            # risk
    "kho khan",          # difficulty
    "suy giam",          # decline
    "suy thoai",         # recession
    "ban rong",          # net sell (foreign)
    "ban thao",          # panic sell
    "lao doc",           # plunge
    "do dao",            # collapse
    "vo no",             # default
    "thua le",           # loss
    "than trong",        # caution
    "yeu",               # weak
    "dieu chinh",        # correction
    "ap luc ban",        # selling pressure
    "thanh khoan thap",  # low liquidity (often bearish)
    "khoi ngoai ban",    # foreigners selling
    "san thap",          # low (price)
    "do",                # red (markets falling — slight risk of false positive)
    "khung hoang",       # crisis
    "tham hoa",          # disaster
    "ban gia san",       # sell at the bottom
}

_POSITIVE_KEYWORDS_EN: set[str] = {
    "rise", "rises", "rising", "rose",
    "gain", "gains", "gained",
    "beat", "beats", "beating",
    "strong", "stronger", "strongest",
    "growth", "growing", "grew",
    "positive", "favorable", "bullish",
    "profit", "profitable",
    "record", "high", "highs", "all-time high",
    "rally", "surge", "soar", "soared", "jumped",
    "outperform", "outperforming",
    "buy", "buying", "buyers",
    "upgrade", "upgraded",
    "expand", "expansion",
    "breakthrough", "milestone",
    "recover", "recovered", "recovery",
}

_NEGATIVE_KEYWORDS_EN: set[str] = {
    "fall", "falls", "falling", "fell",
    "loss", "losses",
    "decline", "declined", "declining",
    "drop", "drops", "dropped",
    "weak", "weaker", "weakest",
    "negative", "bearish",
    "miss", "missed", "missing",
    "plunge", "plunged", "plunging",
    "slump", "slumped",
    "crash", "crashed",
    "sell", "selling", "sell-off", "selloff", "sellers",
    "downgrade", "downgraded",
    "risk", "risks", "risky",
    "crisis", "concerns", "worry", "worries",
    "default", "defaults",
    "recession", "downturn",
}


_VN_LETTER_MAP = {
    "đ": "d", "Đ": "D",
}


def _ascii_fold(text: str) -> str:
    """Strip Vietnamese diacritics: 'tăng' → 'tang', 'điều' → 'dieu'.

    NFKD handles combining marks but NOT the Vietnamese letter `đ`/`Đ`
    (which is its own character, not d + combining stroke). Pre-fold it.
    """
    for k, v in _VN_LETTER_MAP.items():
        text = text.replace(k, v)
    normalized = unicodedata.normalize("NFKD", text or "")
    return normalized.encode("ascii", "ignore").decode("ascii")


def score_sentiment(text: str) -> dict:
    """Score a piece of text. Returns dict with score, pos_count, neg_count, n_total.

    Score is in [-1, +1]: (pos - neg) / (pos + neg), or 0 if no signals.
    """
    if not text:
        return {"score": 0.0, "pos_count": 0, "neg_count": 0}

    ascii_text = _ascii_fold(text).lower()
    word_only = re.sub(r"[^a-z0-9\s-]", " ", ascii_text)

    pos = 0
    neg = 0

    # Vietnamese keywords — substring match on ASCII-folded text
    for kw in _POSITIVE_KEYWORDS_VI:
        if kw in word_only:
            pos += word_only.count(kw)
    for kw in _NEGATIVE_KEYWORDS_VI:
        if kw in word_only:
            neg += word_only.count(kw)

    # English keywords — word-boundary match to avoid false positives
    # (e.g. "buy" should not match "buyer" already counted, but "rises" should match)
    for kw in _POSITIVE_KEYWORDS_EN:
        pos += len(re.findall(rf"\b{re.escape(kw)}\b", word_only))
    for kw in _NEGATIVE_KEYWORDS_EN:
        neg += len(re.findall(rf"\b{re.escape(kw)}\b", word_only))

    total = pos + neg
    score = ((pos - neg) / total) if total else 0.0

    return {
        "score":     round(score, 3),
        "pos_count": pos,
        "neg_count": neg,
    }


def label_sentiment(score: float) -> str:
    if score >= 0.3:    return "strong-positive"
    if score >= 0.05:   return "positive"
    if score <= -0.3:   return "strong-negative"
    if score <= -0.05:  return "negative"
    return "neutral"


def score_article(source, text: str) -> dict:
    """Prefer cached LLM score from frontmatter; fall back to keyword scoring.

    ``source`` is a ``knowledge.lib.corpus.Source`` instance (typed as Any to
    avoid a circular import — only ``sentiment_llm`` property is accessed).
    """
    llm = getattr(source, "sentiment_llm", None)
    if llm and isinstance(llm.get("score"), (int, float)):
        return {
            "score":  float(llm["score"]),
            "source": "llm",
            "reason": llm.get("reason"),
        }
    result = score_sentiment(text)
    result["source"] = "keyword"
    return result
