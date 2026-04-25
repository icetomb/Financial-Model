"""
News analysis layer for the recommendation pipeline (Layer 2).

3-Layer Architecture
--------------------
  Layer 1: Quantitative base score (0-100) — price weakness + financial health
  Layer 2: News analysis (this module) — sentiment label + bounded adjustment
  Layer 3: Final score = base_score + news_adjustment, clamped to [0, 100]

News acts as a refinement layer, NOT a replacement for quantitative analysis.
The adjustment is additive and bounded (default ±10 pts) so it cannot
overwhelm the base score.

Tuning points (all constants in this file):
  - NEWS_ADJUSTMENT_BOUNDS: max positive / negative adjustment values
  - SENTIMENT_THRESHOLDS: aggregate score cutoffs for classification
  - POSITIVE_KEYWORDS / NEGATIVE_KEYWORDS: headline scoring terms
  - RECENCY_DECAY_DAYS: how quickly older headlines lose weight
  - DUPLICATE_SIMILARITY: threshold for near-duplicate detection
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all tuning knobs live here
# ---------------------------------------------------------------------------

# Max additive adjustment applied to the 0-100 base score.
NEWS_ADJUSTMENT_BOUNDS: dict[str, float] = {
    "max_positive": 10.0,
    "max_negative": -10.0,
}

# Aggregate sentiment score thresholds (score ranges from -1 to +1).
SENTIMENT_THRESHOLDS: dict[str, float] = {
    "positive": 0.15,
    "negative": -0.15,
}

RECENCY_DECAY_DAYS = 14
DUPLICATE_SIMILARITY = 0.80

# ---------------------------------------------------------------------------
# Keyword lists — financial-context terms for headline scoring
# ---------------------------------------------------------------------------

POSITIVE_KEYWORDS: list[str] = [
    "beat expectations", "beats expectations", "exceeded expectations",
    "surpassed expectations", "record earnings", "record revenue",
    "strong earnings", "earnings beat", "revenue beat", "profit beat",
    "better than expected", "above expectations", "strong quarter",
    "blowout quarter", "top-line beat", "bottom-line beat",
    "upgrade", "upgraded", "overweight", "buy rating", "outperform",
    "price target raised", "price target increase", "bullish",
    "top pick", "strong buy",
    "growth", "expanding", "expansion", "momentum", "accelerating",
    "surging demand", "market share gain",
    "partnership", "acquisition", "new deal", "contract win",
    "product launch", "innovation", "breakthrough",
    "fda approval", "approval granted",
    "dividend increase", "dividend raise", "buyback", "share repurchase",
    "raised dividend", "special dividend",
    "surge", "soar", "rally", "all-time high",
]

NEGATIVE_KEYWORDS: list[str] = [
    "missed expectations", "miss expectations", "below expectations",
    "disappointing earnings", "earnings miss", "revenue miss",
    "worse than expected", "weak earnings", "profit decline",
    "revenue decline", "sales decline", "weaker than expected",
    "guidance cut", "lowered guidance", "slashed guidance",
    "lowered outlook", "warns", "warning", "profit warning",
    "downgrade", "downgraded", "underweight", "sell rating",
    "underperform", "price target cut", "price target lowered",
    "bearish", "price target reduced",
    "lawsuit", "investigation", "regulatory risk", "probe",
    "fine", "fined", "penalty", "fraud", "sec inquiry",
    "antitrust", "class action", "settlement",
    "bankruptcy", "default", "debt concern", "liquidity concern",
    "credit downgrade", "junk status",
    "layoff", "layoffs", "restructuring", "plant closure",
    "recall", "safety concern", "supply chain issue",
    "plunge", "crash", "tumble", "slump", "selloff", "sell-off",
]

# Theme categories for structured risk/support flag detection.
RISK_THEMES: dict[str, list[str]] = {
    "Legal/regulatory risk": [
        "lawsuit", "investigation", "probe", "regulatory",
        "fine", "penalty", "sec", "antitrust", "class action",
    ],
    "Lowered guidance": [
        "guidance cut", "lowered guidance", "lowered outlook",
        "slashed guidance", "profit warning", "warns",
    ],
    "Restructuring": ["layoff", "layoffs", "restructuring", "plant closure"],
    "Financial stress": [
        "bankruptcy", "default", "debt concern", "liquidity",
        "credit downgrade",
    ],
}

CATALYST_THEMES: dict[str, list[str]] = {
    "Earnings beat": [
        "earnings beat", "beat expectations", "better than expected",
        "strong quarter", "record earnings",
    ],
    "Analyst upgrade": [
        "upgrade", "upgraded", "overweight", "buy rating",
        "top pick", "strong buy",
    ],
    "Business development": [
        "partnership", "acquisition", "deal", "contract win",
        "product launch",
    ],
    "Dividend/buyback": [
        "dividend increase", "dividend raise", "buyback",
        "share repurchase",
    ],
    "Growth momentum": [
        "growth", "expanding", "momentum", "accelerating",
        "market share gain",
    ],
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _is_near_duplicate(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b).ratio() >= DUPLICATE_SIMILARITY


def _recency_weight(published: str | None) -> float:
    """Return a weight in [0.3, 1.0] based on headline age."""
    if not published:
        return 0.5
    try:
        dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        age_days = max((now - dt).total_seconds() / 86400, 0.0)
        if age_days <= 1:
            return 1.0
        if age_days >= RECENCY_DECAY_DAYS:
            return 0.3
        return 1.0 - 0.7 * (age_days / RECENCY_DECAY_DAYS)
    except (ValueError, TypeError):
        return 0.5


# ---------------------------------------------------------------------------
# Headline-level scoring
# ---------------------------------------------------------------------------


def score_headline(title: str) -> float:
    """Score a single headline from -1.0 to +1.0 using keyword matching."""
    text = _normalize_text(title)
    if not text:
        return 0.0

    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in text)
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text)
    total = pos_hits + neg_hits
    if total == 0:
        return 0.0
    return (pos_hits - neg_hits) / total


# ---------------------------------------------------------------------------
# Theme / flag detection
# ---------------------------------------------------------------------------


def _detect_flags(headlines: list[dict]) -> tuple[list[str], list[str]]:
    """Return ``(risk_flags, positive_catalysts)`` detected in *headlines*."""
    all_text = " ".join(_normalize_text(h.get("title", "")) for h in headlines)

    risk_flags: list[str] = []
    for label, keywords in RISK_THEMES.items():
        if any(kw in all_text for kw in keywords):
            risk_flags.append(label)

    catalysts: list[str] = []
    for label, keywords in CATALYST_THEMES.items():
        if any(kw in all_text for kw in keywords):
            catalysts.append(label)

    return risk_flags, catalysts


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------


def _generate_summary(
    sentiment_label: str,
    positive_count: int,
    negative_count: int,
    neutral_count: int,
    risk_flags: list[str],
    catalysts: list[str],
    total: int,
) -> str:
    """Produce a concise, investment-relevant summary string."""
    if total == 0:
        return "No recent news available for analysis."

    if sentiment_label == "positive":
        base = "Recent coverage is mostly positive"
        if catalysts:
            base += f", driven by {', '.join(c.lower() for c in catalysts[:2])}"
        base += "."
        if risk_flags:
            base += f" Minor concern: {risk_flags[0].lower()}."
        return base

    if sentiment_label == "negative":
        base = "Recent coverage is negative"
        if risk_flags:
            base += f", highlighting {', '.join(f.lower() for f in risk_flags[:2])}"
        base += "."
        if catalysts:
            base += f" However, {catalysts[0].lower()} noted."
        return base

    # neutral
    if positive_count > 0 and negative_count > 0:
        pos_part = catalysts[0].lower() if catalysts else "some positive signals"
        neg_part = risk_flags[0].lower() if risk_flags else "offsetting concerns"
        return f"Recent headlines are mixed, with {pos_part} offset by {neg_part}."
    if total <= 2:
        return "Limited recent news with no strong directional signal."
    return "Recent news is neutral with no clear positive or negative trend."


# ---------------------------------------------------------------------------
# Main analysis entry point
# ---------------------------------------------------------------------------


def analyze_headlines(headlines: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze a list of news headlines and return a full sentiment analysis.

    Parameters
    ----------
    headlines : list[dict]
        Each dict needs at minimum a ``title`` key.
        Optional: ``published`` (ISO date string) for recency weighting.

    Returns
    -------
    dict with sentiment_label, sentiment_icon_color, news_adjustment,
    summary, headline_count, positive/negative/neutral counts,
    risk_flags, positive_catalysts, and raw sentiment_score.
    """
    neutral_result: dict[str, Any] = {
        "sentiment_label": "neutral",
        "sentiment_icon_color": "yellow",
        "news_adjustment": 0.0,
        "summary": "No recent news available for analysis.",
        "headline_count": 0,
        "positive_count": 0,
        "negative_count": 0,
        "neutral_count": 0,
        "risk_flags": [],
        "positive_catalysts": [],
        "sentiment_score": 0.0,
    }

    if not headlines:
        return neutral_result

    # Deduplicate near-identical headlines
    unique: list[dict] = []
    seen_texts: list[str] = []
    for h in headlines:
        text = _normalize_text(h.get("title", ""))
        if not text:
            continue
        if any(_is_near_duplicate(text, s) for s in seen_texts):
            continue
        seen_texts.append(text)
        unique.append(h)

    if not unique:
        return neutral_result

    # Score each headline with recency weighting
    weighted_scores: list[float] = []
    weights: list[float] = []
    positive_count = 0
    negative_count = 0
    neutral_count = 0

    for h in unique:
        score = score_headline(h.get("title", ""))
        weight = _recency_weight(h.get("published"))
        weighted_scores.append(score * weight)
        weights.append(weight)

        if score > 0.05:
            positive_count += 1
        elif score < -0.05:
            negative_count += 1
        else:
            neutral_count += 1

    total_weight = sum(weights)
    sentiment_score = (
        sum(weighted_scores) / total_weight if total_weight else 0.0
    )
    sentiment_score = max(-1.0, min(1.0, sentiment_score))

    # Classify
    if sentiment_score >= SENTIMENT_THRESHOLDS["positive"]:
        sentiment_label = "positive"
        icon_color = "green"
    elif sentiment_score <= SENTIMENT_THRESHOLDS["negative"]:
        sentiment_label = "negative"
        icon_color = "red"
    else:
        sentiment_label = "neutral"
        icon_color = "yellow"

    adjustment = compute_news_adjustment(sentiment_score)
    risk_flags, catalysts = _detect_flags(unique)
    summary = _generate_summary(
        sentiment_label,
        positive_count,
        negative_count,
        neutral_count,
        risk_flags,
        catalysts,
        len(unique),
    )

    return {
        "sentiment_label": sentiment_label,
        "sentiment_icon_color": icon_color,
        "news_adjustment": round(adjustment, 1),
        "summary": summary,
        "headline_count": len(unique),
        "positive_count": positive_count,
        "negative_count": negative_count,
        "neutral_count": neutral_count,
        "risk_flags": risk_flags,
        "positive_catalysts": catalysts,
        "sentiment_score": round(sentiment_score, 3),
    }


# ---------------------------------------------------------------------------
# Adjustment & final score helpers
# ---------------------------------------------------------------------------


def compute_news_adjustment(sentiment_score: float) -> float:
    """Convert an aggregate sentiment score (-1 to +1) into a bounded adjustment.

    The mapping is linear:
      +1.0 sentiment → +max_positive pts
      -1.0 sentiment → max_negative pts
       0.0 sentiment →  0 pts

    Bounds are set in ``NEWS_ADJUSTMENT_BOUNDS`` (default ±10).
    """
    if sentiment_score >= 0:
        return sentiment_score * NEWS_ADJUSTMENT_BOUNDS["max_positive"]
    return sentiment_score * abs(NEWS_ADJUSTMENT_BOUNDS["max_negative"])


def compute_final_score(base_score: float, news_adjustment: float) -> float:
    """Compute the final recommendation score.

    ``final_score = clamp(base_score + news_adjustment, 0, 100)``

    Uses additive adjustment (not a multiplier) so the quantitative base
    score remains the primary determinant.
    """
    return round(max(0.0, min(100.0, base_score + news_adjustment)), 1)


def get_final_stance(final_score: float, sentiment_label: str) -> str:
    """Produce an interpretable overall stance label."""
    if final_score >= 55:
        if sentiment_label == "negative":
            return "Candidate with caution"
        return "Strong candidate"
    if final_score >= 35:
        if sentiment_label == "positive":
            return "Candidate"
        if sentiment_label == "negative":
            return "Hold off"
        return "Mixed / insufficient news confidence"
    return "Hold off"
