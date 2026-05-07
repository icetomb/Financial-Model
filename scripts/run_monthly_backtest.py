"""
Monthly backtest runner.

Cron entry point that, once per month:

1. Pulls the top 50 recommendations using the same defaults as the
   `/api/recommendations` page (no sector / industry / sort overrides,
   profitable_only off, $1 B+ market cap floor matching the UI).
2. Runs every registered prediction model (`models.MODEL_BUILDERS`)
   against each of those tickers with a 30-day horizon.
3. Saves each prediction to the existing `predictions` table tagged with
   ``batch_id = recommendations_YYYY_MM`` so the existing evaluation flow
   picks them up after 30 days.
4. Skips any (batch_id, ticker, model_name) triple that already exists,
   making re-runs in the same month idempotent.
5. Prints a JSON summary on stdout for easy log scraping.

Run manually with::

    cd /var/www/Financial-Model
    venv/bin/python scripts/run_monthly_backtest.py

See ``Documents/DEPLOYMENT.md`` for the cron entry that wires this up.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import date
from typing import Any

# Allow `python scripts/run_monthly_backtest.py` from any cwd (cron wraps
# the call in `cd /var/www/Financial-Model && ...` so this is just a
# safety net for direct invocations).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database as db  # noqa: E402
from models import (  # noqa: E402
    PredictionError,
    get_available_models,
    return_direction,
    run_model,
)
from services.backtests import BACKTEST_SOURCE, make_batch_id  # noqa: E402
from services.recommendations import get_recommendations  # noqa: E402


# Defaults match the `/recommendations` page's HTML:
#   sector=All, industry=All, min_market_cap=$1B, sort=score,
#   profitable_only=off, results=50 (overridden vs the UI's 20).
DEFAULT_TOP_N = 50
DEFAULT_MIN_MARKET_CAP = 1_000_000_000.0
DEFAULT_SORT_BY = "score"
DEFAULT_FORECAST_HORIZON_DAYS = 30


def _build_logger(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("monthly_backtest")


def fetch_top_recommendations(top_n: int = DEFAULT_TOP_N) -> list[dict[str, Any]]:
    """Wrapper around ``get_recommendations`` using the page's defaults."""
    return get_recommendations(
        sector=None,
        industry=None,
        limit=top_n,
        min_market_cap=DEFAULT_MIN_MARKET_CAP,
        profitable_only=False,
        sort_by=DEFAULT_SORT_BY,
    )


def _save_one(
    log: logging.Logger,
    *,
    rec: dict[str, Any],
    rank: int,
    model_name: str,
    batch_id: str,
    batch_date: str,
    forecast_horizon: int,
) -> str:
    """Run one (ticker, model) pair, persist the prediction, return outcome.

    Outcome is one of: ``"saved"``, ``"skipped"``, ``"error"`` so the
    caller can keep tallies.  All errors are caught here; this function
    never raises.
    """
    ticker = rec["ticker"]

    if db.prediction_exists_in_batch(batch_id, ticker, model_name):
        log.info("[skip] %s / %s already in batch %s", ticker, model_name, batch_id)
        return "skipped"

    try:
        result = run_model(model_name, ticker)
    except PredictionError as exc:
        log.warning("[fail] %s / %s: %s", ticker, model_name, exc)
        return "error"
    except Exception as exc:  # noqa: BLE001 - last-resort guard for cron stability
        log.error("[fail] %s / %s: unexpected error: %s", ticker, model_name, exc)
        log.debug(traceback.format_exc())
        return "error"

    db.save_prediction(
        model_name=model_name,
        ticker=result["ticker"],
        prediction_date=result["latest_data_date"],
        latest_close=result["latest_close"],
        predicted_return=result["predicted_return"],
        predicted_price=result["estimated_price_30d"],
        predicted_direction=return_direction(result["predicted_return"]),
        forecast_horizon_days=result.get("forecast_horizon_days", forecast_horizon),
        batch_id=batch_id,
        batch_date=batch_date,
        prediction_source=BACKTEST_SOURCE,
        recommendation_rank=rank,
        recommendation_score=rec.get("recommendation_score"),
    )
    log.info(
        "[ok]   %s / %s  predicted_return=%.4f  rank=%d",
        ticker,
        model_name,
        result["predicted_return"],
        rank,
    )
    return "saved"


def run(top_n: int = DEFAULT_TOP_N, *, verbose: bool = False) -> dict[str, Any]:
    """Run the full monthly backtest pipeline.  Returns a summary dict."""
    log = _build_logger(verbose)
    today = date.today()
    batch_id = make_batch_id(today)
    batch_date = today.isoformat()
    models = get_available_models()

    log.info("=" * 60)
    log.info("Monthly backtest starting")
    log.info("batch_id=%s  models=%s  top_n=%d", batch_id, models, top_n)

    # Make sure the database (and any column migrations) is ready before
    # the script tries to write.  Safe to call repeatedly.
    db.init_db()

    log.info("Fetching top %d recommendations (defaults)…", top_n)
    try:
        recs = fetch_top_recommendations(top_n)
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to fetch recommendations: %s", exc)
        log.debug(traceback.format_exc())
        return {
            "batch_id": batch_id,
            "batch_date": batch_date,
            "recommendation_count": 0,
            "models": models,
            "attempted": 0,
            "saved": 0,
            "skipped": 0,
            "errors": [f"recommendations: {exc}"],
        }

    log.info("Got %d recommendations", len(recs))

    attempted = saved = skipped = errors = 0
    error_messages: list[str] = []

    for rank, rec in enumerate(recs, start=1):
        for model_name in models:
            attempted += 1
            try:
                outcome = _save_one(
                    log,
                    rec=rec,
                    rank=rank,
                    model_name=model_name,
                    batch_id=batch_id,
                    batch_date=batch_date,
                    forecast_horizon=DEFAULT_FORECAST_HORIZON_DAYS,
                )
            except Exception as exc:  # noqa: BLE001 - keep cron going on any DB error
                outcome = "error"
                error_messages.append(f"{rec.get('ticker', '?')}/{model_name}: {exc}")
                log.error("[fail] DB write failed: %s", exc)

            if outcome == "saved":
                saved += 1
            elif outcome == "skipped":
                skipped += 1
            else:
                errors += 1
                if rec.get("ticker"):
                    error_messages.append(f"{rec['ticker']}/{model_name}")

    summary = {
        "batch_id": batch_id,
        "batch_date": batch_date,
        "recommendation_count": len(recs),
        "models": models,
        "attempted": attempted,
        "saved": saved,
        "skipped": skipped,
        "error_count": errors,
        "errors": error_messages,
    }

    log.info("-" * 60)
    log.info("Summary:")
    log.info("  batch_id              = %s", summary["batch_id"])
    log.info("  recommendation_count  = %d", summary["recommendation_count"])
    log.info("  models                = %s", summary["models"])
    log.info("  attempted             = %d", summary["attempted"])
    log.info("  saved                 = %d", summary["saved"])
    log.info("  skipped               = %d", summary["skipped"])
    log.info("  errors                = %d", summary["error_count"])
    log.info("=" * 60)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the monthly backtest.")
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Number of recommendations to back-test (default: {DEFAULT_TOP_N}).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args(argv)

    summary = run(top_n=args.top_n, verbose=args.verbose)

    # Final JSON line — easy to grep for in the cron log file.
    print(json.dumps(summary, indent=2))

    # Non-zero exit if nothing got saved AND there were errors so a
    # broken cron is visible in the system mail.
    return 1 if (summary["saved"] == 0 and summary["error_count"] > 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
