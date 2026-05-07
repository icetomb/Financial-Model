"""
SQLite database layer for the stock prediction tracker.

Manages three tables:
  - watchlist          : tickers the user wants to track over time
  - predictions        : saved Model 1 (and future model) predictions with evaluation data
  - fundamentals_cache : cached per-ticker financial snapshots for recommendations

The database file (financial_model.db) is created automatically on first run.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

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


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the set of column names that currently exist on *table*.

    Wraps ``PRAGMA table_info(<table>)`` so callers can ask "does this
    column exist?" without parsing PRAGMA output everywhere.
    """
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


# Columns added by the monthly-backtest feature.  Listed once so the fresh
# CREATE TABLE and the legacy ALTER TABLE migration agree on the schema.
#
# NOTE: SQLite cannot ALTER TABLE ADD COLUMN with a NOT NULL clause unless
# a constant default is supplied (which we do for prediction_source).
_PREDICTION_BATCH_COLUMNS: list[tuple[str, str]] = [
    ("batch_id",             "TEXT"),
    ("batch_date",           "TEXT"),
    ("prediction_source",    "TEXT NOT NULL DEFAULT 'manual'"),
    ("recommendation_rank",  "INTEGER"),
    ("recommendation_score", "REAL"),
]


def _migrate_predictions_table(conn: sqlite3.Connection) -> list[str]:
    """Add any monthly-backtest batch columns that are missing.

    Compares the live ``predictions`` schema (via ``PRAGMA table_info``)
    against ``_PREDICTION_BATCH_COLUMNS`` and issues an ``ALTER TABLE
    ADD COLUMN`` for every column that does not already exist.  Existing
    rows are left untouched; new columns are NULL (or ``'manual'`` for
    ``prediction_source``).

    Returns the list of column names that were actually added so the
    caller / tests can log or assert on the migration.
    """
    existing = _table_columns(conn, "predictions")
    added: list[str] = []
    for col_name, col_def in _PREDICTION_BATCH_COLUMNS:
        if col_name in existing:
            continue
        conn.execute(f"ALTER TABLE predictions ADD COLUMN {col_name} {col_def}")
        added.append(col_name)
    return added


def init_db() -> None:
    """Create tables if they don't already exist, then run any pending
    column migrations, then create supporting indexes.

    Order matters:

    1. ``CREATE TABLE IF NOT EXISTS`` is a no-op when the table already
       exists, so it cannot retro-fit columns onto a legacy database.
    2. Therefore we explicitly call :func:`_migrate_predictions_table`
       *before* anything that references the new ``batch_id`` column —
       otherwise creating an index on ``batch_id`` would fail on legacy
       databases with ``no such column: batch_id``.
    3. Indexes are created last, once the schema is known to be current.
    """
    conn = get_connection()

    # ---- Stage 1: create base tables on a fresh database -------------
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker       TEXT    UNIQUE NOT NULL,
            company_name TEXT    DEFAULT '',
            is_owned     INTEGER DEFAULT 0,
            is_active    INTEGER DEFAULT 1,
            date_added   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS fundamentals_cache (
            ticker             TEXT PRIMARY KEY,
            company_name       TEXT DEFAULT '',
            sector             TEXT DEFAULT '',
            industry           TEXT DEFAULT '',
            current_price      REAL,
            week52_high        REAL,
            week52_low         REAL,
            ma200              REAL,
            month_return       REAL,
            market_cap         REAL,
            net_income         REAL,
            operating_cashflow REAL,
            free_cashflow      REAL,
            revenue_growth     REAL,
            debt_to_equity     REAL,
            roe                REAL,
            last_updated       TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS news_analysis_cache (
            ticker              TEXT PRIMARY KEY,
            sentiment_label     TEXT    NOT NULL DEFAULT 'neutral',
            sentiment_icon_color TEXT   NOT NULL DEFAULT 'yellow',
            news_adjustment     REAL    NOT NULL DEFAULT 0,
            summary             TEXT    NOT NULL DEFAULT '',
            headline_count      INTEGER NOT NULL DEFAULT 0,
            positive_count      INTEGER NOT NULL DEFAULT 0,
            negative_count       INTEGER NOT NULL DEFAULT 0,
            neutral_count       INTEGER NOT NULL DEFAULT 0,
            sentiment_score     REAL    NOT NULL DEFAULT 0,
            risk_flags          TEXT    DEFAULT '[]',
            positive_catalysts  TEXT    DEFAULT '[]',
            analyzed_at         TEXT    NOT NULL,
            expires_at          TEXT    NOT NULL
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
            created_at            TEXT    NOT NULL,
            batch_id              TEXT,
            batch_date            TEXT,
            prediction_source     TEXT    NOT NULL DEFAULT 'manual',
            recommendation_rank   INTEGER,
            recommendation_score  REAL
        );
    """)

    # ---- Stage 2: migrate legacy databases ---------------------------
    # Must run *before* any index / query that touches the new columns,
    # otherwise an upgraded DB without batch_id will fail with
    # "no such column: batch_id".
    _migrate_predictions_table(conn)

    # ---- Stage 3: indexes (safe now that columns are guaranteed) -----
    # Unique partial index doubles as duplicate protection at the DB
    # level for monthly backtest rows.  The WHERE clause keeps legacy
    # rows (batch_id IS NULL) out of the uniqueness check entirely.
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_batch_unique
            ON predictions(batch_id, ticker, model_name)
            WHERE batch_id IS NOT NULL
        """
    )

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

def prediction_exists(model_name: str, ticker: str, prediction_date: str) -> bool:
    """Return True if a prediction already exists for this model+ticker+date."""
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM predictions WHERE model_name = ? AND ticker = ? AND prediction_date = ?",
        (model_name, ticker.upper(), prediction_date),
    ).fetchone()
    conn.close()
    return row is not None


def save_prediction(
    model_name: str,
    ticker: str,
    prediction_date: str,
    latest_close: float,
    predicted_return: float,
    predicted_price: float,
    predicted_direction: str,
    forecast_horizon_days: int = 30,
    *,
    batch_id: str | None = None,
    batch_date: str | None = None,
    prediction_source: str = "manual",
    recommendation_rank: int | None = None,
    recommendation_score: float | None = None,
) -> dict:
    """Insert a new prediction row and return it as a dict.

    ``batch_id`` / ``batch_date`` / ``prediction_source`` /
    ``recommendation_rank`` / ``recommendation_score`` are optional metadata
    populated by the monthly backtest automation; manual predictions made
    from the UI continue to leave them at their defaults.
    """
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO predictions
            (model_name, ticker, prediction_date, latest_close,
             predicted_return, predicted_price, predicted_direction,
             forecast_horizon_days, created_at,
             batch_id, batch_date, prediction_source,
             recommendation_rank, recommendation_score)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            batch_id,
            batch_date,
            prediction_source,
            recommendation_rank,
            recommendation_score,
        ),
    )
    conn.commit()
    pred_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (pred_id,)).fetchone()
    conn.close()
    return dict(row)


def prediction_exists_in_batch(batch_id: str, ticker: str, model_name: str) -> bool:
    """Return True if a prediction already exists for this monthly batch+ticker+model.

    Used by the monthly backtest to guarantee idempotency when the same
    cron job runs twice in the same calendar month.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT 1 FROM predictions
        WHERE batch_id = ? AND ticker = ? AND model_name = ?
        """,
        (batch_id, ticker.upper(), model_name),
    ).fetchone()
    conn.close()
    return row is not None


def get_predictions_by_batch(batch_id: str) -> list[dict]:
    """Return every prediction row associated with *batch_id*."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT * FROM predictions
        WHERE batch_id = ?
        ORDER BY recommendation_rank ASC, model_name ASC
        """,
        (batch_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_batch_ids() -> list[dict]:
    """List distinct batches in the predictions table, newest first.

    Returns a list of dicts with ``batch_id``, ``batch_date``,
    ``prediction_source``, and ``total_predictions`` so the API can
    render a summary list without a per-row scan.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            batch_id,
            MIN(batch_date)         AS batch_date,
            MIN(prediction_source)  AS prediction_source,
            COUNT(*)                AS total_predictions
        FROM predictions
        WHERE batch_id IS NOT NULL
        GROUP BY batch_id
        ORDER BY batch_date DESC, batch_id DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


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


# ---------------------------------------------------------------------------
# Fundamentals cache
# ---------------------------------------------------------------------------

_CACHE_FIELDS = (
    "ticker", "company_name", "sector", "industry", "current_price",
    "week52_high", "week52_low", "ma200", "month_return", "market_cap",
    "net_income", "operating_cashflow", "free_cashflow", "revenue_growth",
    "debt_to_equity", "roe",
)


def get_fundamentals_cache(ticker: str) -> dict | None:
    """Return cached fundamentals for *ticker*, or None if not cached."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM fundamentals_cache WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def upsert_fundamentals_cache(data: dict) -> None:
    """Insert or update a fundamentals cache row."""
    conn = get_connection()
    values = tuple(data.get(f) for f in _CACHE_FIELDS)
    placeholders = ", ".join("?" for _ in _CACHE_FIELDS)
    columns = ", ".join(_CACHE_FIELDS)
    set_clause = ", ".join(f"{f} = excluded.{f}" for f in _CACHE_FIELDS if f != "ticker")

    conn.execute(
        f"""
        INSERT INTO fundamentals_cache ({columns}, last_updated)
        VALUES ({placeholders}, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            {set_clause},
            last_updated = excluded.last_updated
        """,
        values + (datetime.now().isoformat(),),
    )
    conn.commit()
    conn.close()


def clear_stale_cache(max_age_hours: int = 24) -> int:
    """Delete cache rows older than *max_age_hours*. Returns rows deleted."""
    conn = get_connection()
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
    cursor = conn.execute(
        "DELETE FROM fundamentals_cache WHERE last_updated < ?", (cutoff,)
    )
    conn.commit()
    deleted = cursor.rowcount
    conn.close()
    return deleted


# ---------------------------------------------------------------------------
# News analysis cache
# ---------------------------------------------------------------------------

_NEWS_CACHE_FIELDS = (
    "sentiment_label", "sentiment_icon_color", "news_adjustment", "summary",
    "headline_count", "positive_count", "negative_count", "neutral_count",
    "sentiment_score",
)


def get_news_analysis_cache(ticker: str) -> dict | None:
    """Return cached news analysis for *ticker*, or None."""
    import json as _json

    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM news_analysis_cache WHERE ticker = ?", (ticker.upper(),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    result = dict(row)
    result["risk_flags"] = _json.loads(result.get("risk_flags") or "[]")
    result["positive_catalysts"] = _json.loads(result.get("positive_catalysts") or "[]")
    return result


def upsert_news_analysis_cache(ticker: str, analysis: dict) -> None:
    """Insert or update a news analysis cache row."""
    import json as _json

    conn = get_connection()
    conn.execute(
        """
        INSERT INTO news_analysis_cache
            (ticker, sentiment_label, sentiment_icon_color, news_adjustment,
             summary, headline_count, positive_count, negative_count,
             neutral_count, sentiment_score, risk_flags, positive_catalysts,
             analyzed_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            sentiment_label      = excluded.sentiment_label,
            sentiment_icon_color = excluded.sentiment_icon_color,
            news_adjustment      = excluded.news_adjustment,
            summary              = excluded.summary,
            headline_count       = excluded.headline_count,
            positive_count       = excluded.positive_count,
            negative_count       = excluded.negative_count,
            neutral_count        = excluded.neutral_count,
            sentiment_score      = excluded.sentiment_score,
            risk_flags           = excluded.risk_flags,
            positive_catalysts   = excluded.positive_catalysts,
            analyzed_at          = excluded.analyzed_at,
            expires_at           = excluded.expires_at
        """,
        (
            ticker.upper(),
            analysis.get("sentiment_label", "neutral"),
            analysis.get("sentiment_icon_color", "yellow"),
            analysis.get("news_adjustment", 0.0),
            analysis.get("summary", ""),
            analysis.get("headline_count", 0),
            analysis.get("positive_count", 0),
            analysis.get("negative_count", 0),
            analysis.get("neutral_count", 0),
            analysis.get("sentiment_score", 0.0),
            _json.dumps(analysis.get("risk_flags", [])),
            _json.dumps(analysis.get("positive_catalysts", [])),
            analysis.get("analyzed_at", datetime.now().isoformat()),
            analysis.get("expires_at", datetime.now().isoformat()),
        ),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Model performance
# ---------------------------------------------------------------------------

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
