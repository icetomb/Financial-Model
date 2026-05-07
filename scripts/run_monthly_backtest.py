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

Resilience:

- Every ``run_model(...)`` call is wrapped in :func:`with_retries` so a
  transient Yahoo Finance failure (401 Unauthorized, 429 Too Many
  Requests, 5xx, connection reset, timeout, ...) is retried up to 3
  times with exponential backoff (1.5 s, 3 s) before being recorded as
  a *data-fetch failure* rather than a model failure.
- A short sleep between tickers reduces rate-limit pressure on Yahoo's
  endpoints during a 50-ticker run.
- A failure for one (ticker, model) pair never aborts the rest of the
  batch; affected tickers are recorded in ``failed_tickers``.
- In-process caches in :mod:`models.model_1` and :mod:`models.model_2`
  ensure the same ticker's price history (and SPY/VIX) are not
  re-downloaded for every model.

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
import time
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
from services.yf_resilience import is_transient_error, with_retries  # noqa: E402


# Defaults match the `/recommendations` page's HTML:
#   sector=All, industry=All, min_market_cap=$1B, sort=score,
#   profitable_only=off, results=50 (overridden vs the UI's 20).
DEFAULT_TOP_N = 50
DEFAULT_MIN_MARKET_CAP = 1_000_000_000.0
DEFAULT_SORT_BY = "score"
DEFAULT_FORECAST_HORIZON_DAYS = 30

# Resilience tuning.
RETRY_ATTEMPTS = 3                # initial + 2 retries
RETRY_BASE_DELAY_SECONDS = 1.5    # backoff: 1.5 s, 3 s
SLEEP_BETWEEN_TICKERS_SECONDS = 1.0


# Per-call outcome labels used by ``_save_one`` and the run loop.
OUTCOME_SAVED = "saved"
OUTCOME_SKIPPED = "skipped"
OUTCOME_DATA_FAILURE = "data_failure"
OUTCOME_MODEL_FAILURE = "model_failure"


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


def _classify_failure(exc: BaseException) -> str:
    """Return ``OUTCOME_DATA_FAILURE`` for transient yfinance errors,
    ``OUTCOME_MODEL_FAILURE`` for everything else.

    A ``PredictionError`` raised by ``download_data`` chains the original
    HTTP error via ``__cause__``, so :func:`is_transient_error` walks the
    cause chain and detects the underlying 401/429/5xx/timeout/etc.
    """
    return OUTCOME_DATA_FAILURE if is_transient_error(exc) else OUTCOME_MODEL_FAILURE


def _save_one(
    log: logging.Logger,
    *,
    rec: dict[str, Any],
    rank: int,
    model_name: str,
    batch_id: str,
    batch_date: str,
    forecast_horizon: int,
) -> dict[str, Any]:
    """Run one (ticker, model) pair, persist the prediction, return outcome.

    Returns a dict with::

        {
            "outcome":    one of OUTCOME_*,
            "ticker":     str,
            "model_name": str,
            "error":      str | None,
        }

    Never raises; the caller is in the cron loop and must keep going
    even if a single pair has a database write error.
    """
    ticker = rec["ticker"]
    base = {"ticker": ticker, "model_name": model_name}

    if db.prediction_exists_in_batch(batch_id, ticker, model_name):
        log.info("[skip] %s / %s already in batch %s", ticker, model_name, batch_id)
        return {**base, "outcome": OUTCOME_SKIPPED, "error": None}

    label = f"{ticker}/{model_name}"
    try:
        result = with_retries(
            run_model,
            model_name,
            ticker,
            attempts=RETRY_ATTEMPTS,
            base_delay=RETRY_BASE_DELAY_SECONDS,
            logger=log,
            label=label,
        )
    except PredictionError as exc:
        outcome = _classify_failure(exc)
        tag = "[data-fail]" if outcome == OUTCOME_DATA_FAILURE else "[model-fail]"
        log.warning("%s %s: %s", tag, label, exc)
        return {**base, "outcome": outcome, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - last-resort guard for cron stability
        outcome = _classify_failure(exc)
        tag = "[data-fail]" if outcome == OUTCOME_DATA_FAILURE else "[model-fail]"
        log.error("%s %s: %s", tag, label, exc)
        log.debug(traceback.format_exc())
        return {**base, "outcome": outcome, "error": str(exc)}

    try:
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
    except Exception as exc:  # noqa: BLE001 - DB write failure is its own bucket
        log.error("[db-fail] %s: %s", label, exc)
        return {**base, "outcome": OUTCOME_MODEL_FAILURE, "error": f"db: {exc}"}

    log.info(
        "[ok]   %s  predicted_return=%.4f  rank=%d",
        label,
        result["predicted_return"],
        rank,
    )
    return {**base, "outcome": OUTCOME_SAVED, "error": None}


def _empty_summary(
    *,
    batch_id: str,
    batch_date: str,
    models: list[str],
    extra_error: str | None = None,
) -> dict[str, Any]:
    """Skeleton summary used when the run aborts before any work runs."""
    summary: dict[str, Any] = {
        "batch_id": batch_id,
        "batch_date": batch_date,
        "recommendation_count": 0,
        "models": models,
        "attempted": 0,
        "saved": 0,
        "skipped": 0,
        "data_failure_count": 0,
        "model_failure_count": 0,
        "error_count": 0,
        "failed_tickers": [],
        "data_failures": [],
        "model_failures": [],
        "errors": [],
    }
    if extra_error:
        summary["errors"].append(extra_error)
        summary["error_count"] = 1
    return summary


def run(
    top_n: int = DEFAULT_TOP_N,
    *,
    verbose: bool = False,
    sleep_between_tickers: float = SLEEP_BETWEEN_TICKERS_SECONDS,
) -> dict[str, Any]:
    """Run the full monthly backtest pipeline.  Returns a summary dict.

    ``sleep_between_tickers`` exists primarily for tests, which patch it
    to ``0`` to avoid stretching the test suite.
    """
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
        return _empty_summary(
            batch_id=batch_id,
            batch_date=batch_date,
            models=models,
            extra_error=f"recommendations: {exc}",
        )

    log.info("Got %d recommendations", len(recs))

    attempted = saved = skipped = 0
    data_failures: list[dict[str, Any]] = []
    model_failures: list[dict[str, Any]] = []
    failed_tickers: set[str] = set()

    for rank, rec in enumerate(recs, start=1):
        # Brief breather between tickers to ease pressure on Yahoo's
        # endpoints (skip the very first iteration so we don't add a
        # pointless delay before any work has happened).
        if rank > 1 and sleep_between_tickers > 0:
            time.sleep(sleep_between_tickers)

        for model_name in models:
            attempted += 1
            outcome = _save_one(
                log,
                rec=rec,
                rank=rank,
                model_name=model_name,
                batch_id=batch_id,
                batch_date=batch_date,
                forecast_horizon=DEFAULT_FORECAST_HORIZON_DAYS,
            )

            if outcome["outcome"] == OUTCOME_SAVED:
                saved += 1
            elif outcome["outcome"] == OUTCOME_SKIPPED:
                skipped += 1
            elif outcome["outcome"] == OUTCOME_DATA_FAILURE:
                data_failures.append(outcome)
                failed_tickers.add(outcome["ticker"])
            else:  # OUTCOME_MODEL_FAILURE
                model_failures.append(outcome)

    error_messages = [
        f"{f['ticker']}/{f['model_name']} (data): {f['error']}" for f in data_failures
    ] + [
        f"{f['ticker']}/{f['model_name']} (model): {f['error']}" for f in model_failures
    ]

    summary: dict[str, Any] = {
        "batch_id": batch_id,
        "batch_date": batch_date,
        "recommendation_count": len(recs),
        "models": models,
        "attempted": attempted,
        "saved": saved,
        "skipped": skipped,
        "data_failure_count": len(data_failures),
        "model_failure_count": len(model_failures),
        "error_count": len(data_failures) + len(model_failures),
        "failed_tickers": sorted(failed_tickers),
        "data_failures": data_failures,
        "model_failures": model_failures,
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
    log.info("  data_failures         = %d", summary["data_failure_count"])
    log.info("  model_failures        = %d", summary["model_failure_count"])
    if summary["failed_tickers"]:
        log.info("  failed_tickers        = %s", summary["failed_tickers"])
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
