"""
Pending-prediction evaluation.

Single source of truth for the "evaluate predictions whose 30-day horizon
has elapsed" pipeline.  Used by:

- ``POST /api/predictions/evaluate``  – the existing UI button
- ``scripts/run_evaluation.py``       – the cron job

Both call :func:`evaluate_pending_predictions` so the behaviour stays
exactly the same regardless of trigger source.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import yfinance as yf

import database as db
from models import return_direction

logger = logging.getLogger(__name__)


def _magnitude_label(predicted_return: float, actual_return: float) -> str:
    """Compare absolute predicted vs actual return."""
    abs_predicted = abs(predicted_return)
    abs_actual = abs(actual_return)
    if abs(abs_actual - abs_predicted) < 0.0001:
        return "equal"
    if abs_actual > abs_predicted:
        return "bigger"
    return "smaller"


def _fetch_target_close(ticker: str, target_date: date) -> float | None:
    """Return the first close on/after *target_date*, or None if unavailable."""
    df = yf.download(
        ticker,
        start=target_date.isoformat(),
        end=(target_date + timedelta(days=7)).isoformat(),
        progress=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    if df.empty:
        return None
    return float(df["Close"].iloc[0])


def evaluate_pending_predictions(today: date | None = None) -> dict[str, Any]:
    """Evaluate every pending prediction whose forecast horizon has elapsed.

    Returns a dict with::

        {
            "evaluated_count": int,
            "evaluated_ids":   [pred_id, ...],
            "errors":          [str, ...],
        }

    Mirrors the structure the existing ``/api/predictions/evaluate`` route
    has always returned so the frontend keeps working unchanged.
    """
    today = today or date.today()
    pending = db.get_pending_predictions()
    evaluated: list[int] = []
    errors: list[str] = []

    for pred in pending:
        try:
            pred_date = datetime.strptime(pred["prediction_date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            errors.append(
                f"Skipping prediction {pred['id']}: invalid date {pred.get('prediction_date')}"
            )
            continue

        target_date = pred_date + timedelta(days=pred["forecast_horizon_days"])
        if today < target_date:
            continue

        try:
            actual_price = _fetch_target_close(pred["ticker"], target_date)
            if actual_price is None:
                errors.append(
                    f"No price data for {pred['ticker']} around {target_date}"
                )
                continue

            actual_return = (actual_price - pred["latest_close"]) / pred["latest_close"]
            actual_direction = return_direction(actual_return)
            direction_correct = pred["predicted_direction"] == actual_direction
            magnitude = _magnitude_label(pred["predicted_return"], actual_return)
            prediction_error = actual_return - pred["predicted_return"]

            db.evaluate_prediction(
                pred["id"],
                actual_price,
                actual_return,
                actual_direction,
                direction_correct,
                magnitude,
                prediction_error,
            )
            evaluated.append(pred["id"])

        except Exception as exc:
            errors.append(f"Error evaluating {pred['ticker']}: {exc}")
            logger.exception("Evaluation failed for prediction %s", pred.get("id"))

    return {
        "evaluated_count": len(evaluated),
        "evaluated_ids": evaluated,
        "errors": errors,
    }
