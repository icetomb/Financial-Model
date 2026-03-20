"""
SQLite database layer for the stock prediction tracker.

Manages two tables:
  - watchlist  : tickers the user wants to track over time
  - predictions: saved Model 1 (and future model) predictions with evaluation data

The database file (financial_model.db) is created automatically on first run.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

DB_PATH = "financial_model.db"


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    """Return a new connection with row-factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    """Create tables if they don't already exist."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT    UNIQUE NOT NULL,
            company_name TEXT    DEFAULT '',
            is_owned     INTEGER DEFAULT 0,
            is_active    INTEGER DEFAULT 1,
            date_added   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS predictions (
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
        );
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Watchlist CRUD
# ---------------------------------------------------------------------------

def add_to_watchlist(ticker: str, company_name: str = "") -> dict | None:
    """Insert a ticker into the watchlist. Returns None if it already exists."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO watchlist (ticker, company_name, date_added) VALUES (?, ?, ?)",
            (ticker.upper(), company_name, date.today().isoformat()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM watchlist WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
        return dict(row)
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def get_watchlist() -> list[dict]:
    """Return all active watchlist items, newest first."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM watchlist WHERE is_active = 1 ORDER BY date_added DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_watchlist_item_by_ticker(ticker: str) -> dict | None:
    """Look up a single watchlist row by ticker symbol."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM watchlist WHERE ticker = ? AND is_active = 1",
        (ticker.upper(),),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_watchlist_item(item_id: int, **kwargs) -> bool:
    """Update allowed fields on a watchlist row."""
    conn = get_connection()
    allowed = {"company_name", "is_owned", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        conn.close()
        return False
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [item_id]
    conn.execute(f"UPDATE watchlist SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return True


def remove_from_watchlist(item_id: int) -> None:
    """Delete a watchlist row by ID."""
    conn = get_connection()
    conn.execute("DELETE FROM watchlist WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Prediction CRUD
# ---------------------------------------------------------------------------

def save_prediction(
    model_name: str,
    ticker: str,
    prediction_date: str,
    latest_close: float,
    predicted_return: float,
    predicted_price: float,
    predicted_direction: str,
    forecast_horizon_days: int = 30,
) -> dict:
    """Insert a new prediction row and return it as a dict."""
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO predictions
            (model_name, ticker, prediction_date, latest_close,
             predicted_return, predicted_price, predicted_direction,
             forecast_horizon_days, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            model_name,
            ticker.upper(),
            prediction_date,
            latest_close,
            predicted_return,
            predicted_price,
            predicted_direction,
            forecast_horizon_days,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()
    pred_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (pred_id,)).fetchone()
    conn.close()
    return dict(row)


def get_predictions(
    model_name: str | None = None,
    ticker: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Query predictions with optional filters."""
    conn = get_connection()
    query = "SELECT * FROM predictions WHERE 1=1"
    params: list = []

    if model_name:
        query += " AND model_name = ?"
        params.append(model_name)
    if ticker:
        query += " AND ticker = ?"
        params.append(ticker.upper())
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY prediction_date DESC, created_at DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_pending_predictions() -> list[dict]:
    """Shortcut: all predictions still awaiting evaluation."""
    return get_predictions(status="pending")


def delete_prediction(pred_id: int) -> None:
    """Delete a single prediction row by ID."""
    conn = get_connection()
    conn.execute("DELETE FROM predictions WHERE id = ?", (pred_id,))
    conn.commit()
    conn.close()


def evaluate_prediction(
    pred_id: int,
    actual_price: float,
    actual_return: float,
    actual_direction: str,
    direction_correct: bool,
    magnitude_comparison: str,
    prediction_error: float,
) -> None:
    """Mark a prediction as completed with evaluation data."""
    conn = get_connection()
    conn.execute(
        """
        UPDATE predictions SET
            status               = 'completed',
            actual_price         = ?,
            actual_return        = ?,
            actual_direction     = ?,
            direction_correct    = ?,
            magnitude_comparison = ?,
            prediction_error     = ?,
            evaluated_at         = ?
        WHERE id = ?
        """,
        (
            actual_price,
            actual_return,
            actual_direction,
            1 if direction_correct else 0,
            magnitude_comparison,
            prediction_error,
            datetime.now().isoformat(),
            pred_id,
        ),
    )
    conn.commit()
    conn.close()


def get_model_performance(model_name: str = "Model 1") -> dict:
    """Aggregate performance metrics for a model."""
    conn = get_connection()
    all_rows = conn.execute(
        "SELECT * FROM predictions WHERE model_name = ?", (model_name,)
    ).fetchall()
    conn.close()

    completed = [dict(r) for r in all_rows if r["status"] == "completed"]
    pending = [dict(r) for r in all_rows if r["status"] == "pending"]

    total = len(all_rows)
    num_completed = len(completed)
    num_pending = len(pending)
    num_correct = sum(1 for p in completed if p["direction_correct"])
    num_incorrect = num_completed - num_correct

    direction_accuracy = (num_correct / num_completed * 100) if num_completed else 0.0

    avg_prediction_error = 0.0
    avg_predicted_return = 0.0
    avg_actual_return = 0.0

    if completed:
        avg_prediction_error = (
            sum(abs(p["prediction_error"]) for p in completed) / num_completed
        )
        avg_predicted_return = (
            sum(p["predicted_return"] for p in completed) / num_completed
        )
        avg_actual_return = (
            sum(p["actual_return"] for p in completed) / num_completed
        )

    return {
        "model_name": model_name,
        "total_predictions": total,
        "completed_predictions": num_completed,
        "pending_predictions": num_pending,
        "correct_predictions": num_correct,
        "incorrect_predictions": num_incorrect,
        "direction_accuracy": round(direction_accuracy, 1),
        "avg_prediction_error": round(avg_prediction_error * 100, 2),
        "avg_predicted_return": round(avg_predicted_return * 100, 2),
        "avg_actual_return": round(avg_actual_return * 100, 2),
    }
