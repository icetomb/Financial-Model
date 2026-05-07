"""
Tests for the monthly backtest automation, evaluation script, and
related database / API surface.

Covers:
- batch_id generation
- duplicate protection in the predictions table
- top-50 recommendation selection (defaults match the page)
- iteration over every registered model
- prediction rows store batch metadata correctly
- the evaluation script delegates to the existing pipeline
- /api/backtests endpoints
"""

from __future__ import annotations

import os
import sys
from datetime import date
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import sqlite3  # noqa: E402

import database as db  # noqa: E402
from models import get_available_models  # noqa: E402
from services.backtests import (  # noqa: E402
    BACKTEST_SOURCE,
    list_batch_summaries,
    make_batch_id,
    summarize_batch,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path, monkeypatch):
    """Point the database at a temporary file for every test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()


def _fake_recommendation(ticker: str, rank: int, score: float = 80.0) -> dict:
    return {
        "ticker": ticker,
        "company_name": f"{ticker} Corp",
        "sector": "Technology",
        "industry": "Software",
        "current_price": 100.0,
        "recommendation_score": score,
        "base_score": score,
    }


def _fake_model_result(ticker: str, predicted_return: float = 0.02) -> dict:
    return {
        "ticker": ticker,
        "latest_close": 100.0,
        "predicted_return": predicted_return,
        "estimated_price_30d": 100.0 * (1 + predicted_return),
        "forecast_horizon_days": 30,
        "outlook": "Bullish",
        "summary": "test",
        "latest_data_date": "2026-05-06",
        "metrics": {"mae": 0, "rmse": 0, "r2": 0, "direction_accuracy": 0},
        "samples": {"total": 100, "train": 80, "test": 20},
    }


# ---------------------------------------------------------------------------
# batch_id generation
# ---------------------------------------------------------------------------

class TestBatchId:
    def test_make_batch_id_from_date(self):
        assert make_batch_id(date(2026, 5, 6)) == "recommendations_2026_05"

    def test_make_batch_id_pads_single_digit_month(self):
        assert make_batch_id(date(2026, 1, 15)) == "recommendations_2026_01"

    def test_make_batch_id_default_uses_today(self):
        today = date.today()
        expected = f"recommendations_{today.year:04d}_{today.month:02d}"
        assert make_batch_id() == expected

    def test_custom_prefix(self):
        assert make_batch_id(date(2026, 7, 1), prefix="custom") == "custom_2026_07"


# ---------------------------------------------------------------------------
# Database batch metadata
# ---------------------------------------------------------------------------

class TestPredictionBatchMetadata:
    def _save(self, **kwargs):
        defaults = dict(
            model_name="Model 1",
            ticker="AAPL",
            prediction_date="2026-05-06",
            latest_close=100.0,
            predicted_return=0.05,
            predicted_price=105.0,
            predicted_direction="up",
        )
        defaults.update(kwargs)
        return db.save_prediction(**defaults)

    def test_save_with_batch_metadata(self):
        row = self._save(
            batch_id="recommendations_2026_05",
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            recommendation_rank=3,
            recommendation_score=75.5,
        )
        assert row["batch_id"] == "recommendations_2026_05"
        assert row["batch_date"] == "2026-05-06"
        assert row["prediction_source"] == BACKTEST_SOURCE
        assert row["recommendation_rank"] == 3
        assert row["recommendation_score"] == 75.5

    def test_save_without_batch_defaults_to_manual(self):
        row = self._save()
        assert row["batch_id"] is None
        assert row["prediction_source"] == "manual"
        assert row["recommendation_rank"] is None

    def test_prediction_exists_in_batch_true(self):
        self._save(
            batch_id="recommendations_2026_05",
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            ticker="AAPL",
            model_name="Model 1",
        )
        assert db.prediction_exists_in_batch(
            "recommendations_2026_05", "AAPL", "Model 1"
        ) is True

    def test_prediction_exists_in_batch_false_for_different_model(self):
        self._save(
            batch_id="recommendations_2026_05",
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            ticker="AAPL",
            model_name="Model 1",
        )
        assert db.prediction_exists_in_batch(
            "recommendations_2026_05", "AAPL", "Model 2"
        ) is False

    def test_prediction_exists_in_batch_false_for_different_batch(self):
        self._save(
            batch_id="recommendations_2026_05",
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            ticker="AAPL",
            model_name="Model 1",
        )
        assert db.prediction_exists_in_batch(
            "recommendations_2026_06", "AAPL", "Model 1"
        ) is False

    def test_get_predictions_by_batch_orders_by_rank(self):
        self._save(
            batch_id="b1",
            batch_date="2026-05-06",
            ticker="AAA",
            recommendation_rank=2,
            model_name="Model 1",
        )
        self._save(
            batch_id="b1",
            batch_date="2026-05-06",
            ticker="BBB",
            recommendation_rank=1,
            model_name="Model 1",
        )
        rows = db.get_predictions_by_batch("b1")
        assert len(rows) == 2
        assert rows[0]["ticker"] == "BBB"
        assert rows[1]["ticker"] == "AAA"

    def test_unique_partial_index_blocks_duplicate_batch_rows(self):
        """The unique partial index is a belt-and-suspenders guarantee:
        even if the application-level check is bypassed, the DB will
        still reject a duplicate (batch_id, ticker, model_name) row.
        """
        self._save(
            batch_id="recommendations_2026_05",
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            ticker="AAA",
            model_name="Model 1",
        )
        with pytest.raises(sqlite3.IntegrityError):
            self._save(
                batch_id="recommendations_2026_05",
                batch_date="2026-05-06",
                prediction_source=BACKTEST_SOURCE,
                ticker="AAA",
                model_name="Model 1",
            )

    def test_unique_partial_index_allows_legacy_null_batch_rows(self):
        """Manual / legacy rows have batch_id=NULL.  The unique index
        has a ``WHERE batch_id IS NOT NULL`` clause so two such rows
        with identical (ticker, model_name) must NOT collide — that's
        exactly what the partial index is for.
        """
        self._save(ticker="AAA", model_name="Model 1")
        self._save(ticker="AAA", model_name="Model 1")  # would collide without WHERE clause


# ---------------------------------------------------------------------------
# Legacy database migration  (regression test for the
# "no such column: batch_id" failure mode reported on 2026-05-06)
# ---------------------------------------------------------------------------

class TestLegacyDatabaseMigration:
    """Simulate a database created before the monthly-backtest feature
    existed: predictions table is present but lacks the five batch
    columns.  ``init_db()`` must upgrade it in-place without losing
    existing rows and without crashing on the index creation step.
    """

    LEGACY_PREDICTIONS_DDL = """
        CREATE TABLE predictions (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name            TEXT    NOT NULL DEFAULT 'Model 1',
            ticker                TEXT    NOT NULL,
            prediction_date       TEXT    NOT NULL,
            latest_close          REAL    NOT NULL,
            predicted_return      REAL    NOT NULL,
            predicted_price       REAL    NOT NULL,
            predicted_direction   TEXT    NOT NULL,
            forecast_horizon_days INTEGER NOT NULL DEFAULT 30,
            status                TEXT    NOT NULL DEFAULT 'pending',
            actual_price          REAL,
            actual_return         REAL,
            actual_direction      TEXT,
            direction_correct     INTEGER,
            magnitude_comparison  TEXT,
            prediction_error      REAL,
            evaluated_at          TEXT,
            created_at            TEXT    NOT NULL
        )
    """

    def _seed_legacy_db(self, db_path: str) -> int:
        """Create a pre-feature predictions table and insert one row.

        Returns the inserted row's id so the test can verify the row
        survives the migration.
        """
        conn = sqlite3.connect(db_path)
        conn.executescript(self.LEGACY_PREDICTIONS_DDL)
        cursor = conn.execute(
            """
            INSERT INTO predictions
                (model_name, ticker, prediction_date, latest_close,
                 predicted_return, predicted_price, predicted_direction,
                 created_at)
            VALUES ('Model 1', 'LEGACY', '2026-04-01', 100.0,
                    0.05, 105.0, 'up', '2026-04-01T00:00:00')
            """
        )
        legacy_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return legacy_id

    def test_init_db_migrates_legacy_predictions_table(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "legacy.db")
        monkeypatch.setattr(db, "DB_PATH", db_path)

        legacy_id = self._seed_legacy_db(db_path)

        # This used to crash with "no such column: batch_id" because the
        # CREATE INDEX ran before the ALTER TABLE migration.
        db.init_db()

        # All five new columns must exist after migration.
        conn = db.get_connection()
        try:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(predictions)").fetchall()}
            for required in (
                "batch_id",
                "batch_date",
                "prediction_source",
                "recommendation_rank",
                "recommendation_score",
            ):
                assert required in cols, f"Missing column after migration: {required}"

            # Legacy row must survive untouched, and the new columns
            # must default to NULL / 'manual' as documented.
            row = conn.execute(
                "SELECT * FROM predictions WHERE id = ?", (legacy_id,)
            ).fetchone()
            assert row is not None
            assert row["ticker"] == "LEGACY"
            assert row["batch_id"] is None
            assert row["prediction_source"] == "manual"
        finally:
            conn.close()

    def test_init_db_is_idempotent(self, tmp_path, monkeypatch):
        """Running init_db twice in a row must not raise."""
        db_path = str(tmp_path / "legacy.db")
        monkeypatch.setattr(db, "DB_PATH", db_path)
        self._seed_legacy_db(db_path)

        db.init_db()
        db.init_db()  # second call is a no-op for migrations + index

    def test_unique_index_is_created_on_legacy_db(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "legacy.db")
        monkeypatch.setattr(db, "DB_PATH", db_path)
        self._seed_legacy_db(db_path)

        db.init_db()

        conn = db.get_connection()
        try:
            indexes = {
                row["name"]
                for row in conn.execute("PRAGMA index_list(predictions)").fetchall()
            }
            assert "idx_predictions_batch_unique" in indexes
        finally:
            conn.close()

    def test_migrate_predictions_table_returns_added_columns(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "legacy.db")
        monkeypatch.setattr(db, "DB_PATH", db_path)
        self._seed_legacy_db(db_path)

        conn = db.get_connection()
        try:
            added = db._migrate_predictions_table(conn)
            assert set(added) == {
                "batch_id",
                "batch_date",
                "prediction_source",
                "recommendation_rank",
                "recommendation_score",
            }

            # Second call must add nothing.
            added_again = db._migrate_predictions_table(conn)
            assert added_again == []
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Monthly backtest runner
# ---------------------------------------------------------------------------

class TestMonthlyBacktestRunner:
    @patch("scripts.run_monthly_backtest.run_model")
    @patch("scripts.run_monthly_backtest.get_recommendations")
    def test_top_50_default_is_used(self, mock_get_recs, mock_run_model):
        mock_get_recs.return_value = [_fake_recommendation(f"TKR{i}", i) for i in range(1, 4)]
        mock_run_model.side_effect = lambda model, ticker: _fake_model_result(ticker)

        from scripts.run_monthly_backtest import (
            DEFAULT_MIN_MARKET_CAP,
            DEFAULT_SORT_BY,
            DEFAULT_TOP_N,
            run,
        )

        run()

        mock_get_recs.assert_called_once()
        kwargs = mock_get_recs.call_args.kwargs
        assert kwargs["limit"] == DEFAULT_TOP_N
        assert kwargs["sector"] is None
        assert kwargs["industry"] is None
        assert kwargs["min_market_cap"] == DEFAULT_MIN_MARKET_CAP
        assert kwargs["profitable_only"] is False
        assert kwargs["sort_by"] == DEFAULT_SORT_BY

    @patch("scripts.run_monthly_backtest.run_model")
    @patch("scripts.run_monthly_backtest.get_recommendations")
    def test_iterates_over_every_registered_model(self, mock_get_recs, mock_run_model):
        mock_get_recs.return_value = [_fake_recommendation("AAA", 1)]
        mock_run_model.side_effect = lambda model, ticker: _fake_model_result(ticker)

        from scripts.run_monthly_backtest import run

        summary = run()

        used_models = {call.args[0] for call in mock_run_model.call_args_list}
        assert used_models == set(get_available_models())
        assert summary["models"] == get_available_models()
        assert summary["saved"] == len(get_available_models())

    @patch("scripts.run_monthly_backtest.run_model")
    @patch("scripts.run_monthly_backtest.get_recommendations")
    def test_saves_batch_metadata_on_each_row(self, mock_get_recs, mock_run_model):
        mock_get_recs.return_value = [
            _fake_recommendation("AAA", 1, score=82.0),
            _fake_recommendation("BBB", 2, score=77.0),
        ]
        mock_run_model.side_effect = lambda model, ticker: _fake_model_result(ticker)

        from scripts.run_monthly_backtest import run

        summary = run()
        rows = db.get_predictions_by_batch(summary["batch_id"])
        models = get_available_models()

        assert len(rows) == 2 * len(models)
        for row in rows:
            assert row["batch_id"] == summary["batch_id"]
            assert row["prediction_source"] == BACKTEST_SOURCE
            assert row["recommendation_rank"] in (1, 2)
            assert row["recommendation_score"] in (82.0, 77.0)

    @patch("scripts.run_monthly_backtest.run_model")
    @patch("scripts.run_monthly_backtest.get_recommendations")
    def test_running_twice_is_idempotent(self, mock_get_recs, mock_run_model):
        mock_get_recs.return_value = [_fake_recommendation("AAA", 1)]
        mock_run_model.side_effect = lambda model, ticker: _fake_model_result(ticker)

        from scripts.run_monthly_backtest import run

        first = run()
        second = run()

        # Second run saves nothing and skips everything that was saved.
        assert second["saved"] == 0
        assert second["skipped"] == first["saved"]

        rows = db.get_predictions_by_batch(first["batch_id"])
        # Re-run did NOT create extra rows.
        assert len(rows) == first["saved"]

    @patch("scripts.run_monthly_backtest.run_model")
    @patch("scripts.run_monthly_backtest.get_recommendations")
    def test_prediction_error_is_recorded_not_raised(
        self, mock_get_recs, mock_run_model,
    ):
        from models import PredictionError

        mock_get_recs.return_value = [_fake_recommendation("BAD", 1)]
        mock_run_model.side_effect = PredictionError("not enough history")

        from scripts.run_monthly_backtest import run

        summary = run()
        assert summary["saved"] == 0
        assert summary["error_count"] == len(get_available_models())


# ---------------------------------------------------------------------------
# Backtest summary helpers
# ---------------------------------------------------------------------------

class TestBacktestSummary:
    def _seed_batch(self, batch_id: str = "recommendations_2026_05"):
        # Two predictions for AAA, one Model 1 (completed, correct), one Model 2 (pending).
        a1 = db.save_prediction(
            model_name="Model 1",
            ticker="AAA",
            prediction_date="2026-05-06",
            latest_close=100.0,
            predicted_return=0.05,
            predicted_price=105.0,
            predicted_direction="up",
            batch_id=batch_id,
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            recommendation_rank=1,
            recommendation_score=80.0,
        )
        db.evaluate_prediction(
            a1["id"],
            actual_price=110.0,
            actual_return=0.10,
            actual_direction="up",
            direction_correct=True,
            magnitude_comparison="bigger",
            prediction_error=0.05,
        )
        db.save_prediction(
            model_name="Model 2",
            ticker="AAA",
            prediction_date="2026-05-06",
            latest_close=100.0,
            predicted_return=-0.02,
            predicted_price=98.0,
            predicted_direction="down",
            batch_id=batch_id,
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            recommendation_rank=1,
            recommendation_score=80.0,
        )

    def test_summarize_batch_returns_aggregates(self):
        self._seed_batch()
        summary = summarize_batch("recommendations_2026_05")
        assert summary is not None
        assert summary["batch_id"] == "recommendations_2026_05"
        assert summary["total_predictions"] == 2
        assert summary["completed_predictions"] == 1
        assert summary["pending_predictions"] == 1
        assert summary["tickers"] == ["AAA"]

        models = {m["model_name"]: m for m in summary["models"]}
        assert models["Model 1"]["completed_predictions"] == 1
        assert models["Model 1"]["direction_accuracy"] == 100.0
        assert models["Model 2"]["pending_predictions"] == 1

    def test_summarize_batch_unknown_returns_none(self):
        assert summarize_batch("nope") is None

    def test_list_batch_summaries(self):
        self._seed_batch("recommendations_2026_05")
        self._seed_batch("recommendations_2026_06")
        summaries = list_batch_summaries()
        ids = {s["batch_id"] for s in summaries}
        assert ids == {"recommendations_2026_05", "recommendations_2026_06"}


# ---------------------------------------------------------------------------
# Evaluation script
# ---------------------------------------------------------------------------

class TestEvaluationScript:
    @patch("scripts.run_evaluation.evaluate_pending_predictions")
    def test_run_delegates_to_existing_evaluator(self, mock_eval):
        mock_eval.return_value = {
            "evaluated_count": 3,
            "evaluated_ids": [1, 2, 3],
            "errors": [],
        }
        from scripts.run_evaluation import run

        summary = run()
        assert mock_eval.called
        assert summary == {
            "evaluated_count": 3,
            "evaluated_ids": [1, 2, 3],
            "errors": [],
        }


# ---------------------------------------------------------------------------
# /api/backtests endpoints
# ---------------------------------------------------------------------------

class TestBacktestApi:
    @pytest.fixture()
    def client(self):
        from app import create_app
        app = create_app()
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def _seed(self, batch_id="recommendations_2026_05"):
        db.save_prediction(
            model_name="Model 1",
            ticker="AAA",
            prediction_date="2026-05-06",
            latest_close=100.0,
            predicted_return=0.05,
            predicted_price=105.0,
            predicted_direction="up",
            batch_id=batch_id,
            batch_date="2026-05-06",
            prediction_source=BACKTEST_SOURCE,
            recommendation_rank=1,
            recommendation_score=80.0,
        )

    def test_list_endpoint_returns_summaries(self, client):
        self._seed()
        resp = client.get("/api/backtests")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)
        assert any(b["batch_id"] == "recommendations_2026_05" for b in data)

    def test_detail_endpoint_returns_summary(self, client):
        self._seed()
        resp = client.get("/api/backtests/recommendations_2026_05")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["batch_id"] == "recommendations_2026_05"
        assert data["total_predictions"] == 1

    def test_detail_endpoint_404_for_unknown(self, client):
        resp = client.get("/api/backtests/does_not_exist")
        assert resp.status_code == 404
