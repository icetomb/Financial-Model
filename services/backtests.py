"""
Monthly backtest helpers.

Tiny module that the cron script (`scripts/run_monthly_backtest.py`) and
the read-only `/api/backtests` endpoints share.  Keeps the core logic
small and testable:

- ``make_batch_id(...)``  – deterministic ``recommendations_YYYY_MM`` ID
- ``summarize_batch(...)`` – aggregate per-batch / per-model stats from
  predictions stored in SQLite
- ``list_batch_summaries()`` – every batch's summary, newest first
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

import database as db

# Predictions created by the monthly cron script use this source label
# so we can distinguish them from ad-hoc manual predictions.
BACKTEST_SOURCE = "monthly_backtest"


def make_batch_id(when: date | None = None, prefix: str = "recommendations") -> str:
    """Build the canonical ``recommendations_YYYY_MM`` batch identifier.

    The ID is derived from the calendar month so two runs in the same
    month produce the same ID — that is what powers idempotency.
    """
    when = when or date.today()
    return f"{prefix}_{when.year:04d}_{when.month:02d}"


def _aggregate(predictions: Iterable[dict]) -> dict[str, Any]:
    """Reduce a flat list of prediction rows into a summary dict."""
    predictions = list(predictions)
    total = len(predictions)
    completed = [p for p in predictions if p.get("status") == "completed"]
    pending = [p for p in predictions if p.get("status") == "pending"]

    by_model: dict[str, dict[str, Any]] = {}
    for p in predictions:
        bucket = by_model.setdefault(
            p["model_name"],
            {
                "model_name": p["model_name"],
                "total": 0,
                "completed": 0,
                "pending": 0,
                "correct": 0,
                "abs_error_sum": 0.0,
            },
        )
        bucket["total"] += 1
        if p.get("status") == "completed":
            bucket["completed"] += 1
            if p.get("direction_correct"):
                bucket["correct"] += 1
            err = p.get("prediction_error")
            if err is not None:
                bucket["abs_error_sum"] += abs(err)
        else:
            bucket["pending"] += 1

    model_stats = []
    for stats in by_model.values():
        completed_n = stats["completed"]
        accuracy = (stats["correct"] / completed_n * 100) if completed_n else 0.0
        avg_err = (stats["abs_error_sum"] / completed_n) if completed_n else 0.0
        model_stats.append(
            {
                "model_name": stats["model_name"],
                "total_predictions": stats["total"],
                "completed_predictions": stats["completed"],
                "pending_predictions": stats["pending"],
                "direction_accuracy": round(accuracy, 1),
                "avg_prediction_error": round(avg_err * 100, 2),
            }
        )
    model_stats.sort(key=lambda r: r["model_name"])

    return {
        "total_predictions": total,
        "completed_predictions": len(completed),
        "pending_predictions": len(pending),
        "models": model_stats,
    }


def summarize_batch(batch_id: str) -> dict[str, Any] | None:
    """Aggregate stats for a single batch.  Returns ``None`` if unknown."""
    rows = db.get_predictions_by_batch(batch_id)
    if not rows:
        return None

    first = rows[0]
    summary = {
        "batch_id": batch_id,
        "batch_date": first.get("batch_date"),
        "source": first.get("prediction_source"),
        "tickers": sorted({r["ticker"] for r in rows}),
    }
    summary.update(_aggregate(rows))
    return summary


def list_batch_summaries() -> list[dict[str, Any]]:
    """Return summaries for every known batch, newest first."""
    summaries: list[dict[str, Any]] = []
    for entry in db.get_batch_ids():
        summary = summarize_batch(entry["batch_id"])
        if summary is not None:
            summaries.append(summary)
    return summaries
