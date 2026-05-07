"""
Pending prediction evaluator.

Cron entry point that runs the same evaluation logic as the
``Evaluate`` button on the predictions page (``POST /api/predictions/evaluate``)
but without going through HTTP.  Both the route and this script share
:func:`services.evaluation.evaluate_pending_predictions`, so behaviour
stays consistent regardless of trigger source.

Run manually with::

    cd /var/www/Financial-Model
    venv/bin/python scripts/run_evaluation.py

See ``Documents/DEPLOYMENT.md`` for the cron entry that runs this daily.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# Allow running with `python scripts/run_evaluation.py` from any cwd.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database as db  # noqa: E402
from services.evaluation import evaluate_pending_predictions  # noqa: E402


def _build_logger(verbose: bool) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("evaluation")


def run(*, verbose: bool = False) -> dict:
    """Run the evaluation pipeline and return the result dict."""
    log = _build_logger(verbose)

    log.info("=" * 60)
    log.info("Prediction evaluation starting")
    db.init_db()

    summary = evaluate_pending_predictions()

    log.info("evaluated_count = %d", summary["evaluated_count"])
    if summary["evaluated_ids"]:
        log.info("evaluated_ids   = %s", summary["evaluated_ids"])
    if summary["errors"]:
        log.warning("errors:")
        for err in summary["errors"]:
            log.warning("  - %s", err)
    log.info("=" * 60)

    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate pending predictions whose horizon has elapsed.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    args = parser.parse_args(argv)

    summary = run(verbose=args.verbose)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
