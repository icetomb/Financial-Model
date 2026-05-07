# Financial Model — Technical Reference

## Table of Contents

1. [Project Architecture](#1-project-architecture)
2. [Libraries & Frameworks](#2-libraries--frameworks)
3. [Module Breakdown](#3-module-breakdown)
   - [app.py — Flask Application & Routes](#apppy--flask-application--routes)
   - [database.py — SQLite Layer](#databasepy--sqlite-layer)
   - [models/__init__.py — Model Registry](#models__init__py--model-registry)
   - [models/model_1.py — Technical Predictor](#modelsmodel_1py--technical-predictor)
   - [models/model_2.py — Market-Aware Predictor](#modelsmodel_2py--market-aware-predictor)
   - [services/recommendations.py — Screener Engine](#servicesrecommendationspy--screener-engine)
   - [services/downside_risk.py — Downside Risk Scanner](#servicesdownside_riskpy--downside-risk-scanner)
   - [services/evaluation.py — Pending-Prediction Evaluator](#servicesevaluationpy--pending-prediction-evaluator)
   - [services/backtests.py — Monthly Batch Helpers](#servicesbacktestspy--monthly-batch-helpers)
   - [services/yf_resilience.py — Yahoo Finance Retry Helper](#servicesyf_resiliencepy--yahoo-finance-retry-helper)
   - [services/news_analysis.py — Sentiment Engine](#servicesnews_analysispy--sentiment-engine)
   - [services/stock_universe.py — Ticker Universe](#servicesstock_universepy--ticker-universe)
   - [scripts/run_monthly_backtest.py — Monthly Cron Runner](#scriptsrun_monthly_backtestpy--monthly-cron-runner)
   - [scripts/run_evaluation.py — Daily Cron Runner](#scriptsrun_evaluationpy--daily-cron-runner)
   - [tests/test_recommendations.py — Test Suite](#teststest_recommendationspy--test-suite)
   - [tests/test_backtests.py — Backtest Test Suite](#teststest_backtestspy--backtest-test-suite)
4. [Frontend Layer](#4-frontend-layer)
5. [Database Schema](#5-database-schema)
6. [Automated Monthly Backtesting](#6-automated-monthly-backtesting)

> **Note:** Performance optimisations, bug history, and migration post-mortems live in [`Documents/Bugs-Issues.md`](./Bugs-Issues.md). New entries should be added there as they are encountered.

---

## 1. Project Architecture

```
Financial-Model/
├── app.py                          # Flask app factory + all HTTP routes
├── database.py                     # SQLite schema + CRUD helpers
├── requirements.txt
├── models/
│   ├── __init__.py                 # Model registry (MODEL_BUILDERS, run_model)
│   ├── model_1.py                  # XGBoost predictor (18 price features)
│   └── model_2.py                  # XGBoost predictor (29 features + SPY/VIX)
├── services/
│   ├── recommendations.py          # "Likely Gainers" screener + news enrichment
│   ├── downside_risk.py            # "Likely Decliners" downside-risk scanner
│   ├── evaluation.py               # Pending-prediction evaluator (shared route + cron)
│   ├── backtests.py                # Monthly batch helpers + summary aggregation
│   ├── yf_resilience.py            # Retry helper for transient yfinance HTTP errors
│   ├── news_analysis.py            # Keyword sentiment + recency scoring
│   └── stock_universe.py           # Static ~200+ ticker universe
├── scripts/
│   ├── run_monthly_backtest.py     # Monthly cron entry point (top 50 × all models)
│   └── run_evaluation.py           # Daily cron entry point (evaluate pending)
├── tests/
│   ├── test_recommendations.py     # Full pytest suite for screener + sentiment
│   └── test_backtests.py           # Pytest suite for monthly backtest automation
├── templates/                      # Jinja2 HTML templates
└── static/                         # CSS + vanilla JavaScript
```

The application follows a **flat service-oriented layout**: routes in `app.py` delegate to model and service modules; all state is persisted in a single SQLite file (`financial_model.db`) managed by `database.py`.

---

## 2. Libraries & Frameworks

### Backend

| Library | Version Constraint | Usage |
|---|---|---|
| **Flask** | `>=3.1` | HTTP routing, Jinja2 templating, JSON responses |
| **yfinance** | `>=0.2` | Yahoo Finance market data, company info, news |
| **XGBoost** | `>=2.1` | `XGBRegressor` for 30-day return prediction |
| **pandas** | `>=2.2` | DataFrame manipulation, time-series indexing, rolling windows |
| **numpy** | `>=2` | Numerical operations, array math |
| **scikit-learn** | `>=1.5` | `mean_absolute_error`, `mean_squared_error`, `r2_score` for model evaluation |
| **sqlite3** | stdlib | Embedded database, `row_factory=sqlite3.Row` for dict-like row access |
| **concurrent.futures** | stdlib | `ThreadPoolExecutor` for parallel API fetches |
| **difflib** | stdlib | `SequenceMatcher` for near-duplicate headline detection |
| **re** | stdlib | Ticker validation (`TICKER_PATTERN = r'^[A-Z]{1,5}$'`) |
| **pytest** | (not pinned) | Unit and integration test runner |

### Frontend

- **Vanilla JavaScript** (no framework): `fetch()` API for all AJAX calls, DOM manipulation for dynamic UI
- **Jinja2** (bundled with Flask): server-side HTML templating
- No build tool, no bundler, no npm

---

## 3. Module Breakdown

---

### `app.py` — Flask Application & Routes

**Purpose:** Application factory and HTTP layer. All routing logic, request parsing, error handling, and model dispatch live here.

#### Imports and Helpers

`app.py` no longer owns the model registry. It imports `run_model`, `return_direction`, `get_available_models`, and `PredictionError` from `models/__init__.py`, and `evaluate_pending_predictions` from `services/evaluation.py`. This keeps the route handlers thin: they just parse the request, delegate to the shared functions, and return JSON.

#### Key Functions

**`create_app() -> Flask`**

App factory. Calls `db.init_db()` on startup (creates tables if they don't exist; runs the small migration in `_ensure_prediction_batch_columns` to add monthly-backtest columns to existing databases), then registers all routes. Returns the configured Flask instance.

#### API Routes

| Method | Endpoint | Handler Description |
|---|---|---|
| `GET` | `/` | Renders `index.html` |
| `POST` | `/predict` | Validates JSON body (`ticker`, `model_name`), calls `_run_model`, optionally saves to DB if ticker is on watchlist and no duplicate exists. Returns prediction JSON. |
| `GET` | `/watchlist` | Renders `watchlist.html` |
| `GET` | `/api/watchlist` | Returns full watchlist from DB |
| `POST` | `/api/watchlist` | Adds ticker, fetches company name via `yf.Ticker.info` |
| `PUT` | `/api/watchlist` | Updates `is_owned`/`is_active` for a ticker |
| `DELETE` | `/api/watchlist` | Removes ticker |
| `GET` | `/predictions` | Renders `predictions.html` |
| `GET` | `/api/predictions` | Returns saved predictions, optional `?model=` filter |
| `POST` | `/api/predictions/run` | Runs + saves prediction; returns 409 on duplicate |
| `DELETE` | `/api/predictions/<id>` | Deletes a prediction row |
| `POST` | `/api/predictions/evaluate` | Evaluates all pending predictions past their 30-day horizon |
| `GET` | `/api/models` | Returns `get_available_models()` (currently `["Model 1", "Model 2"]`) |
| `GET` | `/api/performance` | Returns `get_model_performance` aggregates |
| `GET` | `/api/backtests` | Returns one summary row per monthly batch (totals + per-model accuracy) |
| `GET` | `/api/backtests/<batch_id>` | Returns the per-model breakdown for a single batch (404 if unknown) |
| `GET` | `/recommendations` | Renders `recommendations.html` (tabbed: Likely Gainers + Likely Decliners), injects `sectors` list |
| `GET` | `/api/recommendations` | Calls `get_recommendations(...)`, returns ranked Likely Gainers as JSON |
| `GET` | `/api/industries` | Returns industries, optional `?sector=` filter |
| `GET` | `/api/news/<ticker>` | Fetches and analyzes news for a single ticker |
| `GET` | `/api/downside-risk` | Calls `get_downside_risk_stocks(...)`, returns ranked Likely Decliners. Query params: `sector`, `industry`, `limit`, `min_market_cap`, `use_model` (default `1`). |
| `GET` | `/api/downside-risk/news/<ticker>` | Recent headlines for a decliner with relative-time strings (e.g. "3h ago") attached. |

**Evaluation Logic** lives in `services/evaluation.py` (`evaluate_pending_predictions`) so the cron runner can call it directly. The route is a thin wrapper that returns the same dict the function produces. For each pending prediction where `today >= prediction_date + 30 days`:

1. Calls `yf.download(ticker, start=target_date, end=target_date + 7 days)`
2. Flattens MultiIndex columns if present
3. Uses the **first available close price** in the window (handles market holidays)
4. Computes actual return vs. predicted return, directional match, magnitude error
5. Calls `db.evaluate_prediction(...)` to persist the result

---

### `database.py` — SQLite Layer

**Purpose:** All persistence. Creates tables on first run, exposes typed CRUD functions, implements TTL-based cache expiry.

#### Connection Setup

```python
DB_PATH = "financial_model.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
```

`sqlite3.Row` allows column access by name (`row["ticker"]`) across all callers.

#### Tables and Their Functions

**`watchlist`**

| Function | Description |
|---|---|
| `add_to_watchlist(ticker, company_name, is_owned, is_active)` | Inserts row, UNIQUE on ticker |
| `get_watchlist()` | Returns all rows ordered by `date_added DESC` |
| `get_watchlist_item_by_ticker(ticker)` | Single lookup |
| `update_watchlist_item(ticker, company_name, is_owned, is_active)` | Only updates the three mutable fields |
| `remove_from_watchlist(ticker)` | Hard delete |

**`fundamentals_cache`**

Caches `yf.Ticker.info` responses keyed by ticker. TTL is controlled by `last_updated` timestamp.

| Function | Description |
|---|---|
| `get_fundamentals_cache(ticker)` | Returns cached row or `None` |
| `upsert_fundamentals_cache(ticker, data_dict)` | `INSERT OR REPLACE` with current timestamp |
| `clear_stale_cache(max_age_hours=24)` | Deletes rows older than threshold — available but not currently wired to a scheduled call |

**`news_analysis_cache`**

Stores serialized sentiment results per ticker.

| Function | Description |
|---|---|
| `get_news_analysis_cache(ticker)` | Returns row; deserializes `risk_flags` and `positive_catalysts` from JSON strings |
| `upsert_news_analysis_cache(ticker, analysis_dict)` | Serializes lists to JSON, stores with timestamp |

**`predictions`**

| Function | Description |
|---|---|
| `prediction_exists(model_name, ticker, prediction_date)` | Returns bool; used to prevent duplicates on the same calendar day |
| `prediction_exists_in_batch(batch_id, ticker, model_name)` | Returns bool; used by `scripts/run_monthly_backtest.py` to make monthly cron runs idempotent |
| `save_prediction(...)` | Inserts a new row. Accepts optional keyword-only batch metadata: `batch_id`, `batch_date`, `prediction_source` (default `"manual"`), `recommendation_rank`, `recommendation_score`. |
| `get_predictions(model_name=None, ticker=None, status=None)` | Returns all rows or filtered by model / ticker / status |
| `get_pending_predictions()` | Returns rows where `status = 'pending'` |
| `get_predictions_by_batch(batch_id)` | Returns all rows for a single batch ordered by `recommendation_rank` then `model_name` |
| `get_batch_ids()` | Returns one row per distinct `batch_id` (newest first) with `batch_date`, `prediction_source`, and `total_predictions` for summary endpoints |
| `delete_prediction(prediction_id)` | Hard delete by ID |
| `evaluate_prediction(prediction_id, actual_price, actual_return, actual_direction, direction_correct, magnitude_comparison, prediction_error)` | Updates evaluation columns |
| `get_model_performance(model_name)` | Aggregates evaluated rows — direction accuracy (%), count, avg predicted return, avg actual return, avg MAE — all in percent scale |
| `_table_columns(conn, table)` | Internal helper. Wraps `PRAGMA table_info(<table>)` and returns the set of existing column names. |
| `_migrate_predictions_table(conn)` | Internal helper run from `init_db()`. Compares the live `predictions` schema against `_PREDICTION_BATCH_COLUMNS` and issues an `ALTER TABLE ADD COLUMN` for every column that is missing. Returns the list of columns it actually added (useful for tests / logging). Production databases upgrade automatically on the next app boot or cron run, without losing existing rows. |

---

### `models/__init__.py` — Model Registry

**Purpose:** Single source of truth for which prediction models exist and how to invoke them. Both `app.py` and the cron scripts import from here so adding a future "Model 3" is a one-line change.

#### Exports

```python
MODEL_BUILDERS: dict[str, Callable[..., dict]] = {
    "Model 1": build_prediction_m1,
    "Model 2": build_prediction_m2,
}

def get_available_models() -> list[str]: ...
def run_model(model_name: str, ticker: str) -> dict: ...
def return_direction(value: float) -> str: ...
```

| Symbol | Description |
|---|---|
| `MODEL_BUILDERS` | The actual registry dict. To add Model 3: import its `build_prediction` and add one entry. |
| `get_available_models()` | Returns the list of registered model names in stable order. Drives the UI dropdown via `/api/models` and the iteration in the monthly backtest. |
| `run_model(model_name, ticker)` | Looks up the builder and invokes it. Falls back to Model 1 if the name is unknown to preserve the existing route behaviour. |
| `return_direction(value)` | Maps a return float to `"up"` / `"down"` / `"neutral"`. Shared by `app.py`, the evaluation service, and the monthly backtest script so the direction-mapping logic lives in one place. |
| `PredictionError` | Re-exported from `models.model_1` so callers can `from models import PredictionError` instead of reaching into the model file. |

---

### `models/model_1.py` — Technical Predictor

**Purpose:** XGBoost regression model trained on 18 price/volume features to predict 30-day forward return.

#### Constants

```python
DEFAULT_START_DATE = "2020-01-01"
FORECAST_HORIZON_DAYS = 30
TRAIN_RATIO = 0.8
MIN_TRAIN_SAMPLES = 40
MIN_TEST_SAMPLES = 10
TICKER_PATTERN = r'^[A-Z]{1,5}$'
```

#### Feature Set (18 Features)

```python
FEATURE_COLUMNS = [
    "return_1d", "return_5d", "return_20d",        # momentum
    "ma_5", "ma_20", "ma_50",                        # moving averages
    "ma_ratio_5_20", "ma_ratio_20_50",               # MA crossover ratios
    "vol_ratio_5_20",                                # volume trend
    "price_vs_ma20", "price_vs_ma50",                # price position
    "high_low_range", "close_vs_high", "close_vs_low",  # intraday structure
    "volume_change",                                 # volume momentum
    "rsi",                                           # RSI (14-period)
    "volatility_20d",                                # realized vol
    "return_lag1",                                   # autoregressive lag
]
```

#### Key Functions

**`normalize_ticker(ticker: str) -> str`**

Strips whitespace, converts to uppercase, validates against `TICKER_PATTERN`. Raises `PredictionError` for invalid format.

**`download_data(ticker: str, start: str) -> pd.DataFrame`**

Calls `yf.download(ticker, start=start, progress=False, auto_adjust=True)`. Flattens MultiIndex columns. Raises `PredictionError` if result is empty or lacks a `Close` column.

**`add_features(df: pd.DataFrame) -> pd.DataFrame`**

Computes all 18 features in-place using vectorized pandas operations (`.pct_change()`, `.rolling().mean()`, `.rolling().std()`). Calls `add_rsi` for the RSI column.

**`add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame`**

Wilder RSI: computes daily gain/loss series, rolling mean of gains and losses, then `RSI = 100 - (100 / (1 + RS))`. Handles division-by-zero with `replace(0, 1e-10)`.

**`create_target(df: pd.DataFrame) -> pd.DataFrame`**

```python
df["target_30d_return"] = df["Close"].shift(-30) / df["Close"] - 1
```

Forward-fills the 30-day forward return as the regression target. Rows where the target cannot be computed (last 30 rows) are dropped.

**`time_split(df, ratio=0.8)`**

Chronological train/test split — no shuffling. Returns `(X_train, X_test, y_train, y_test)`. Validates that both splits meet minimum sample thresholds.

**`train_model(X_train, y_train) -> XGBRegressor`**

```python
XGBRegressor(
    n_estimators=300,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    random_state=42,
    verbosity=0,
)
```

**`evaluate_model(model, X_test, y_test) -> dict`**

Returns MAE, RMSE, R², and directional accuracy (fraction of test samples where predicted and actual return share the same sign).

**`build_prediction(ticker: str) -> dict`**

Full pipeline: normalize → download → add features → create target → time split → train → evaluate → predict on most-recent row. Returns a dict with keys: `ticker`, `model`, `predicted_return`, `predicted_price`, `current_price`, `prediction_date`, `target_date`, `metrics` (MAE, RMSE, R², direction accuracy), `feature_count`, `train_samples`, `test_samples`.

**`format_prediction_report(result: dict) -> str`**

Formats `build_prediction` output into a human-readable string (used by `main()` CLI).

---

### `models/model_2.py` — Market-Aware Predictor

**Purpose:** Extends Model 1 with 11 additional features derived from SPY and VIX data, bringing the total feature count to 29.

#### Additional Feature Sets

```python
SPY_FEATURE_COLUMNS = [
    "spy_return_1d", "spy_return_5d", "spy_return_20d",
    "spy_volatility_20d", "relative_ret_5",
    "stock_spy_corr_20d",
]

VIX_FEATURE_COLUMNS = [
    "vix_close", "vix_return_1d",
    "vix_ma_5", "vix_ma_20",
    "vix_vs_ma20",
]
```

#### Key Functions

**`_download_close(ticker: str, start: str) -> pd.Series`**

Downloads a single ticker, flattens MultiIndex if needed, returns the `Close` series. Used for SPY and VIX separately.

**`_add_spy_features(df, spy_close) -> pd.DataFrame`**

Aligns SPY close to the stock's date index via inner join. Computes `spy_return_1d`, `spy_return_5d`, `spy_return_20d`, `spy_volatility_20d`, `relative_ret_5` (stock 5d return minus SPY 5d return), and `stock_spy_corr_20d` (20-day rolling Pearson correlation of daily returns).

**`_add_vix_features(df, vix_close) -> pd.DataFrame`**

Aligns VIX close, computes `vix_close`, `vix_return_1d`, `vix_ma_5`, `vix_ma_20`, `vix_vs_ma20` (VIX level relative to its own 20-day MA).

**`build_prediction(ticker: str) -> dict`**

Downloads stock, SPY (`"SPY"`), and VIX (`"^VIX"`) in parallel order, adds Model 1 features, then appends SPY and VIX features. Uses `COMBINED_FEATURE_COLUMNS` (29 total) for training. All downstream steps (target creation, split, train, evaluate, predict) are identical to Model 1.

---

### `services/recommendations.py` — Screener Engine

**Purpose:** Fetches fundamentals for a universe of stocks, scores them 0–100, applies user filters, enriches with news sentiment, and returns ranked results.

#### Configuration

```python
MAX_WORKERS = 8               # ThreadPoolExecutor size for parallel fetches
CACHE_TTL_HOURS = 24          # Fundamentals cache lifetime
NEWS_CACHE_TTL_HOURS = 4      # News sentiment cache lifetime

WEIGHT_CONFIG = {
    "near_52w_high_penalty":  -15,   # stock within 5% of 52w high (overbought)
    "near_52w_low_bonus":     +10,   # stock within 10% of 52w low (potential value)
    "bad_1m_return_penalty":  -10,   # 1-month return worse than -5%
    "below_ma200_penalty":    -10,   # price below 200-day MA
    "not_profitable_penalty": -15,   # trailing EPS <= 0
    "no_fcf_penalty":         -10,   # free cash flow <= 0
    "revenue_growth_bonus":   +10,   # revenue growth > 5%
    "low_debt_bonus":         +10,   # debt-to-equity < 0.5
    "high_roe_bonus":         +10,   # ROE > 15%
}
```

#### Key Functions

**`_calc_month_return(ticker: str) -> float | None`**

Downloads ~35 days of price data, computes `(last_close / first_close) - 1`. Returns `None` on failure. Called inside `_fetch_stock_data` when `monthReturn` is missing from `Ticker.info`.

**`_fetch_stock_data(ticker: str) -> dict | None`**

1. Checks `fundamentals_cache` in DB; returns cached data if fresh (within `CACHE_TTL_HOURS`)
2. Calls `yf.Ticker(ticker).info` for live data
3. Normalizes `debtToEquity` (yfinance returns it ×100, divides by 100)
4. Computes month return if not provided
5. Upserts result into `fundamentals_cache`
6. Returns normalized data dict

**`compute_score(data: dict) -> tuple[int, list[str]]`**

Starts at 50. Applies each `WEIGHT_CONFIG` rule sequentially. Returns `(clamped_score, reasons_list)` where `score` is clamped to `[0, 100]` and `reasons` is a list of human-readable explanation strings shown in the UI alongside the score.

**`apply_filters(stocks, min_market_cap, profitable_only, max_debt_equity, require_positive_cashflow) -> list`**

Pure filter function — no yfinance calls. Returns the subset of `stocks` that pass all specified thresholds.

**`get_ticker_news(ticker: str) -> list[dict]`**

Calls `yf.Ticker(ticker).news`, normalizes the irregular yfinance news structure (handles both `content.title` nesting and flat `title` keys), returns a list of `{"title": ..., "link": ..., "publisher": ..., "published": ...}` dicts.

**`_get_news_analysis(ticker: str) -> dict`**

1. Checks `news_analysis_cache` — returns cached result if within `NEWS_CACHE_TTL_HOURS`
2. Fetches headlines via `get_ticker_news`
3. Runs `analyze_headlines(headlines)` from `news_analysis.py`
4. Upserts result into `news_analysis_cache`

**`_enrich_with_news(stocks: list) -> list`**

Uses `ThreadPoolExecutor(max_workers=8)` with `as_completed` to run `_get_news_analysis` for every stock concurrently. Merges returned sentiment fields into each stock dict. Exceptions per ticker are caught and logged without aborting the batch.

**`get_recommendations(sector, industry, limit, min_market_cap, profitable_only, sort_by) -> list`**

Full screener pipeline:
1. `get_candidates(sector, industry)` — filtered universe
2. Parallel `_fetch_stock_data` via `ThreadPoolExecutor(max_workers=8)`
3. `apply_filters`
4. `compute_score` for each passing stock
5. `_enrich_with_news` for news-adjusted score
6. Multi-key sort (`score DESC`, then secondary criteria by `sort_by`)
7. Slice to `limit`

---

### `services/downside_risk.py` — Downside Risk Scanner

**Purpose:** Mirror image of `recommendations.py` — scans the same universe for stocks showing **technical weakness, negative model forecasts, or downside risk**, and returns them as ranked "Likely Decliners". Implements a 3-layer pipeline that mirrors the recommendations engine's structure but with downside-oriented signals and a deliberately separated news layer.

#### Configuration

```python
MAX_WORKERS = 8                # ThreadPoolExecutor size for parallel fetches
NEWS_CACHE_TTL_HOURS = 4       # Layer 3 news sentiment cache lifetime
MODEL_PRED_TTL_HOURS = 12      # Process-level Model 1 prediction cache TTL

WEIGHT_CONFIG = {
    # Model-driven (Layer 1.A)
    "model_negative_return_max_pts": 25,
    "model_negative_return_full":   -0.10,   # -10 % predicted -> full points

    # Recent price action
    "neg_month_return_max_pts": 15,
    "neg_month_return_full":   -0.20,        # -20 % monthly return -> full
    "neg_momentum_max_pts": 10,
    "neg_momentum_full":   -0.10,            # -10 % over 10 days -> full
    "below_ma50_pts":  8,
    "below_ma200_pts": 7,

    # Volatility & RSI
    "high_volatility_max_pts": 10,
    "high_volatility_full":    0.50,         # 50 % annualised vol -> full
    "rsi_weakness_max_pts":   10,
    "rsi_oversold_threshold": 30.0,
    "rsi_overbought_threshold": 70.0,

    # Range / volume
    "near_52w_low_max_pts": 10,
    "near_52w_low_full":    0.10,            # within bottom 30% of range -> scaled
    "volume_spike_pts":     5,
    "volume_spike_ratio":   1.5,             # today vol > 1.5x 10-day avg
}

RISK_LEVELS = [(70, "High"), (40, "Medium"), (0, "Low")]
```

#### 3-Layer Architecture

| Layer | Function | What it does |
|---|---|---|
| **Layer 1** | `calculate_downside_risk_score(signals)` | Quantitative 0–100 downside score from technical + Model 1 signals only. |
| **Layer 2** | `generate_downside_explanation(signals, fired)` | Beginner-friendly "Why Flagged" reason strings derived from which signals fired. |
| **Layer 3** | `classify_news_sentiment(ticker)` | Recent yfinance headlines classified as positive / neutral / negative. **Display-only — never modifies the score.** |

The strict separation between Layer 1 and Layer 3 is intentional: news sentiment is shown as a contextual green/yellow/red dot beside the score but is mathematically independent from it.

#### Key Functions

**`_download_price_history(ticker: str, days: int = 365) -> pd.DataFrame | None`**

Downloads ~1 year of OHLCV data via `yf.download(..., auto_adjust=True, progress=False)`. Flattens MultiIndex columns; returns `None` on any failure (which is treated as "skip this ticker").

**`_compute_signals(price_df, info) -> dict | None`**

Reuses `add_features` and `add_rsi` from `models/model_1.py` to derive the 18-feature frame, then computes the additional signals the scanner needs:

- `ma_50` (from existing column), `ma_200` (rolling mean from raw close)
- `month_return` and `momentum_10d` from positional indexing on the close series
- `volatility_annual` = `volatility_20 × sqrt(252)`
- `rsi_14` (from `add_rsi`)
- `week52_high`/`week52_low` from `Ticker.info`, with a fallback to historical max/min
- `vol_spike_on_decline` = today's volume ≥ 1.5× the 10-day avg AND today's return < 0

Returns a flat dict that the scoring layer can consume directly.

**`_fetch_ticker_data(ticker: str) -> dict | None`**

Combines `_download_price_history` and `_compute_signals` for a single ticker. Treats any missing field or fetch failure as "skip", so the scan never crashes on a bad ticker.

**`calculate_downside_risk_score(signals: dict) -> tuple[float, list[tuple[str, float]]]`**

Layer 1. Adds points for each signal that fires:

| Signal | Max pts | Trigger |
|---|---|---|
| `model_negative_return` | 25 | Model 1 predicted return < 0 (full at –10 %) |
| `neg_month_return` | 15 | 1-month return < 0 (full at –20 %) |
| `neg_momentum` | 10 | 10-day return < 0 (full at –10 %) |
| `below_ma50` | 8 | Current price < 50-day MA |
| `below_ma200` | 7 | Current price < 200-day MA |
| `high_volatility` | 10 | Annualised vol > 20 % (full at 50 %) |
| `rsi_oversold` | 10 | RSI < 30, scaled by depth below 30 |
| `rsi_overbought` | 10 | RSI > 70, scaled by depth above 70 |
| `near_52w_low` | 10 | Bottom 30 % of 52-week range, scaled by closeness to low |
| `vol_spike_on_decline` | 5 | Volume spike during a down day |

Total caps at 100. Returns `(score, fired_signals)` where `fired_signals` is a list of `(key, points)` tuples used by the explanation layer.

**`_risk_level(score: float) -> str`**

Maps a 0–100 score to `Low` / `Medium` / `High` using `RISK_LEVELS` thresholds (0–39 / 40–69 / 70–100).

**`generate_downside_explanation(signals, fired_signals) -> list[str]`**

Layer 2. Translates each fired signal into a beginner-friendly reason string with the actual numeric values formatted in (e.g. `"Recent 1-month return is negative (-15.0%)."`, `"RSI is overbought (75), raising the risk of a pullback."`). Wording is intentionally cautious — never claims the stock will drop.

**`get_stock_news(ticker: str, max_items: int = 8) -> list[dict]`**

Wraps `services.recommendations.get_ticker_news` and adds a `relative_time` field (`"3h ago"`, `"2d ago"`, etc.) computed by `_relative_time` for display in the details overlay. Returns the spec's news object shape: `{title, publisher, link, published, relative_time}`.

**`classify_news_sentiment(ticker: str) -> dict`**

Layer 3. Fetches recent headlines, runs `analyze_headlines` from `news_analysis.py`, and returns the simplified shape needed by the UI:

```python
{
    "sentiment":       "positive" | "neutral" | "negative",
    "color":           "green"    | "yellow"  | "red",
    "summary":         "...",        # short prose summary
    "headline_count":  <int>,
}
```

Cached in a process-level dict (`_NEWS_SENTIMENT_CACHE`) for `NEWS_CACHE_TTL_HOURS`. Critically, this function is called *outside* `calculate_downside_risk_score` — by design, sentiment never reaches the scoring path.

**`_model1_predicted_return(ticker: str) -> float | None`**

Calls `models.model_1.build_prediction` and extracts `predicted_return`. Caches the result in a process-level dict (`_MODEL_PRED_CACHE`) for `MODEL_PRED_TTL_HOURS` to avoid retraining XGBoost on every scan. Returns `None` on `PredictionError` or any exception, so the scoring layer can degrade gracefully (the `model_negative_return` signal simply does not fire).

**`get_downside_risk_stocks(sector, industry, limit, min_market_cap, use_model=True, model_top_n=30) -> list[dict]`**

Full scanner pipeline (7 stages):

1. **Stage 1 — Parallel data fetch.** `ThreadPoolExecutor(max_workers=8)` runs `_fetch_ticker_data` across the filtered universe. Tickers with missing data are skipped silently.
2. **Stage 2 — Technical-only ranking.** `calculate_downside_risk_score` runs without any model prediction set. The top `model_top_n` (default 30) candidates by technical score advance to Stage 3.
3. **Stage 3 — Selective Model 1 pass.** Parallel `_model1_predicted_return` runs on the shortlist only — this is the slow step (~30–60 s on a cold cache for 30 tickers, near-instant when warm). Skipped entirely when `use_model=False`.
4. **Stage 4 — Final scoring.** `calculate_downside_risk_score` runs again now that `predicted_return` is populated. `generate_downside_explanation` builds the "Why Flagged" list. `_risk_level` assigns Low / Medium / High.
5. **Stage 5 — Layer 3 news sentiment.** Parallel `classify_news_sentiment` calls produce the green / yellow / red dot color and a short blurb. Failures fall back to `"neutral"` / `"yellow"`.
6. **Stage 6 — Sort.** Final list sorted by `downside_score DESC`.
7. **Stage 7 — Output shaping.** Returns a list of plain dicts matching the API contract: `ticker`, `company_name`, `predicted_return` (in percent), `downside_score`, `risk_level`, `news_sentiment`, `news_sentiment_color`, `news_summary`, `why_flagged`, plus a technical snapshot for the details overlay (`current_price`, `month_return`, `momentum_10d`, `volatility_annual`, `rsi`, `ma_50`, `ma_200`, `week52_high`, `week52_low`, `market_cap`, `sector`, `industry`).

The two-stage design (technical-only filter → Model 1 on shortlist) is the key performance choice — see [Bugs-Issues.md § 1.9](./Bugs-Issues.md#19-two-stage-model-1-filter-in-the-downside-risk-scanner).

---

### `services/evaluation.py` — Pending-Prediction Evaluator

**Purpose:** Single implementation of the "evaluate every prediction whose 30-day horizon has elapsed" pipeline. Used by both the `POST /api/predictions/evaluate` route and the daily cron script (`scripts/run_evaluation.py`) so behaviour is identical regardless of trigger source.

#### Key Functions

**`evaluate_pending_predictions(today: date | None = None) -> dict`**

Orchestrates the evaluation pass. Pulls pending rows via `db.get_pending_predictions()`, skips any whose `prediction_date + forecast_horizon_days` is still in the future, then for each eligible row:

1. Calls `_fetch_target_close(ticker, target_date)` — `yf.download` with a 7-day window to handle market holidays, returns the first available close.
2. Computes `actual_return = (actual_price - latest_close) / latest_close`.
3. Maps direction with `models.return_direction`, builds the magnitude label (`equal` / `bigger` / `smaller`), computes `prediction_error = actual_return - predicted_return`.
4. Persists via `db.evaluate_prediction(...)`.

Errors per ticker are caught and appended to the `errors` list so a single bad symbol does not kill the whole batch.

Returns the dict the existing UI button has always seen: `{ "evaluated_count": int, "evaluated_ids": [int, ...], "errors": [str, ...] }`.

**`_fetch_target_close(ticker, target_date)`** — internal helper; flattens MultiIndex columns and returns `None` on empty data.

**`_magnitude_label(predicted_return, actual_return)`** — returns `"equal"` (within 0.01 % of each other), `"bigger"` (actual > predicted in magnitude), or `"smaller"`.

---

### `services/backtests.py` — Monthly Batch Helpers

**Purpose:** Tiny helper module shared by the monthly cron script and the `/api/backtests` endpoints. Two responsibilities:

1. **Generate canonical batch IDs.** Calling the script in May 2026 produces `recommendations_2026_05`. Two runs in the same month produce the same ID, which is exactly what powers idempotency.
2. **Aggregate per-batch / per-model stats** for the read-only inspection API.

#### Constants

```python
BACKTEST_SOURCE = "monthly_backtest"
```

The `prediction_source` value written into the `predictions` table for every row created by `scripts/run_monthly_backtest.py`. Manual predictions made from the UI keep the default `"manual"` so the two are easy to tell apart.

#### Key Functions

**`make_batch_id(when: date | None = None, prefix: str = "recommendations") -> str`**

Returns `f"{prefix}_{when.year:04d}_{when.month:02d}"`. Defaults to today's date when called without arguments.

**`summarize_batch(batch_id: str) -> dict | None`**

Pulls every row for `batch_id` via `db.get_predictions_by_batch`, groups by `model_name`, and returns:

```python
{
    "batch_id":              "recommendations_2026_05",
    "batch_date":            "2026-05-01",
    "source":                "monthly_backtest",
    "tickers":               ["AAPL", "MSFT", ...],          # sorted, deduplicated
    "total_predictions":     100,
    "completed_predictions": 50,
    "pending_predictions":   50,
    "models": [
        {
            "model_name":            "Model 1",
            "total_predictions":     50,
            "completed_predictions": 25,
            "pending_predictions":   25,
            "direction_accuracy":    64.0,    # % of completed where direction was correct
            "avg_prediction_error":  3.2,     # mean absolute error in %
        },
        ...
    ],
}
```

Returns `None` if the batch is unknown so the API can return a clean 404.

**`list_batch_summaries() -> list[dict]`**

Calls `db.get_batch_ids()` then maps each entry to a `summarize_batch` result. Newest batch first.

**`_aggregate(predictions)`** — internal reducer that turns a flat list of prediction rows into the totals + per-model structure above.

---

### `services/yf_resilience.py` — Yahoo Finance Retry Helper

**Purpose:** Tiny, self-contained resilience layer for transient HTTP / network failures from Yahoo Finance. Used by the monthly backtest cron to keep one bad ticker (or one of Yahoo's frequent `401 Unauthorized` blips) from poisoning the whole run.

#### Public API

```python
from services.yf_resilience import is_transient_error, with_retries

result = with_retries(
    run_model,
    "Model 1",
    "AAPL",
    attempts=3,
    base_delay=1.5,
    logger=log,
    label="AAPL/Model 1",
)
```

| Symbol | Description |
|---|---|
| `is_transient_error(exc)` | Returns `True` if *exc* — or any exception in its `__cause__` / `__context__` chain — looks like a transient HTTP/network failure. Detects HTTP status codes (`401`, `429`, `500`, `502`, `503`, `504`), common message patterns (`unauthorized`, `too many requests`, `rate limit`, `timed out`, `connection refused/reset/aborted`, `temporarily unavailable`, `bad gateway`, `service unavailable`, `gateway timeout`, `remote end closed`), and exception types (`urllib.error.URLError/HTTPError`, `requests.exceptions.{ConnectionError,Timeout,HTTPError}`, `TimeoutError`, `ConnectionError`). Walks the cause chain because `models.model_1.download_data` raises `PredictionError` *from* the underlying HTTP error. |
| `with_retries(fn, *args, attempts=3, base_delay=1.5, max_delay=10, logger=None, label="operation", sleeper=None, **kwargs)` | Calls `fn(*args, **kwargs)`. On a transient error, waits `base_delay * 2**(attempt-1)` (capped at `max_delay`) and retries — so default delays are 1.5 s, 3 s, then give up. **Non-transient errors are re-raised immediately**, so a model's "not enough history" `PredictionError` does not trigger 3 expensive retries. The `sleeper` argument exists for tests and is resolved at call time so a monkeypatched `time.sleep` works. |
| `DEFAULT_ATTEMPTS = 3` | Total tries (initial + 2 retries) used when the cron script does not override it. |
| `DEFAULT_BASE_DELAY = 1.5` | Seconds for the first backoff. |

#### Why retry-on-401

Yahoo's `401 Unauthorized` responses are *not* genuine auth failures in our usage — they're a session/cookie-rotation artifact and almost always succeed when the same call is repeated seconds later. The retry helper treats `401` like any other transient code so we don't have to special-case it everywhere.

#### How the cron uses it

`scripts/run_monthly_backtest.py` wraps every `run_model(model_name, ticker)` call in `with_retries(...)` with `label=f"{ticker}/{model_name}"`. After all attempts fail, the calling code uses `is_transient_error(exc)` to classify the failure as `data_failure` (transient — gets recorded in `failed_tickers`) or `model_failure` (genuine — recorded as a regular error).

---

### `services/news_analysis.py` — Sentiment Engine

**Purpose:** Converts a list of raw headline strings into a structured sentiment report with score, label, theme flags, and a prose summary.

#### Configuration

```python
NEWS_ADJUSTMENT_BOUNDS = (-10, +10)   # max score delta applied to screener
RECENCY_DECAY_DAYS = 14               # headlines older than this get near-zero weight
DUPLICATE_SIMILARITY = 0.8            # SequenceMatcher ratio threshold

POSITIVE_KEYWORDS = [...]             # ~40 terms: "beat", "upgrade", "surge", "record", ...
NEGATIVE_KEYWORDS = [...]             # ~40 terms: "miss", "downgrade", "crash", "loss", ...
RISK_THEMES = [...]                   # multi-word phrases: "SEC investigation", "class action", ...
CATALYST_THEMES = [...]               # multi-word phrases: "FDA approval", "partnership", ...
```

#### Key Functions

**`_normalize_text(text: str) -> str`**

Lowercases, strips punctuation with `re.sub`, collapses whitespace.

**`_is_near_duplicate(a: str, b: str) -> bool`**

```python
SequenceMatcher(None, a, b).ratio() >= DUPLICATE_SIMILARITY
```

**`_recency_weight(published_ts: int | None) -> float`**

Converts Unix timestamp to days-ago delta. Returns `1.0` for articles ≤1 day old, decaying linearly to ~0 at `RECENCY_DECAY_DAYS`. Headlines with no timestamp get weight `0.5`.

**`score_headline(title: str) -> int`**

Returns `+1` (positive keyword match), `-1` (negative keyword match), or `0` (neutral) based on normalized text scan.

**`_detect_flags(headlines: list) -> tuple[list, list]`**

Scans all headline texts for `RISK_THEMES` and `CATALYST_THEMES` strings. Returns `(risk_flags, positive_catalysts)` — lists of matched theme phrases.

**`_generate_summary(label, score, count, risk_flags, positive_catalysts) -> str`**

Produces a one or two sentence prose summary combining the sentiment label, article count, and any detected theme flags.

**`analyze_headlines(headlines: list[dict]) -> dict`**

Full pipeline:
1. Deduplicates headlines using `_is_near_duplicate` (O(n²) pairwise check; acceptable at news scale)
2. Weights each headline's score by `_recency_weight`
3. Aggregates into a float `raw_score`
4. Maps to `sentiment_label` and `sentiment_color`
5. Calls `compute_news_adjustment`, `compute_final_score`, `get_final_stance`
6. Calls `_detect_flags`, `_generate_summary`
7. Returns a unified analysis dict with all fields needed by the screener

**`compute_news_adjustment(raw_score, article_count) -> float`**

Scales `raw_score` by article count confidence and clamps to `NEWS_ADJUSTMENT_BOUNDS`.

**`compute_final_score(base_score, news_adjustment) -> int`**

`clamp(base_score + news_adjustment, 0, 100)`

**`get_final_stance(sentiment_label, direction) -> str`**

Maps the combination of sentiment label and screener direction to a single human-readable stance string (e.g., "Bullish with Strong Value Signal").

---

### `services/stock_universe.py` — Ticker Universe

**Purpose:** Provides the static list of ~200+ large-cap US stocks that the screener operates over.

**Data format:**
```python
_UNIVERSE = [
    {"ticker": "AAPL", "sector": "Technology", "industry": "Consumer Electronics"},
    ...
]
```

| Function | Description |
|---|---|
| `get_sectors()` | Sorted unique sector names across universe |
| `get_industries(sector=None)` | Sorted unique industries, optionally scoped to a sector |
| `get_candidates(sector, industry)` | Deep-copied subset filtered by sector and/or industry |

---

### `scripts/run_monthly_backtest.py` — Monthly Cron Runner

**Purpose:** End-to-end automation that turns the recommendation engine into a monthly backtest dataset. Designed to be invoked from cron once a month (see `Documents/DEPLOYMENT.md` § Cron jobs).

#### Behaviour

1. Calls `db.init_db()` so any pending column migrations happen before writes.
2. Builds `batch_id = make_batch_id(today)` → e.g. `recommendations_2026_05`.
3. Calls `services.recommendations.get_recommendations(...)` with the **same defaults the Recommendations page uses**:
   - `sector=None`, `industry=None`
   - `min_market_cap=1_000_000_000.0` (matches the UI's `$1 B+` default)
   - `profitable_only=False`
   - `sort_by="score"`
   - `limit=DEFAULT_TOP_N` (default 50, override with `--top-n`)
4. For each `(ticker, model)` pair in the cartesian product of the top-50 list × `models.get_available_models()`:
   - If `db.prediction_exists_in_batch(batch_id, ticker, model_name)` is True, skip with an `[skip]` log line.
   - Otherwise call `with_retries(models.run_model, model_name, ticker, attempts=3, base_delay=1.5)` (see `services/yf_resilience.py`). Transient HTTP errors (401/429/5xx/timeouts/connection issues) are retried with exponential backoff before being recorded as a *data-fetch failure*; non-transient errors short-circuit and are recorded as *model failures*.
   - On success, persist via `db.save_prediction(...)` with full batch metadata: `batch_id`, `batch_date`, `prediction_source="monthly_backtest"`, `recommendation_rank`, `recommendation_score`.
   - `PredictionError`, `Exception`, and database-write errors are all caught per call so a single bad ticker / model never kills the run.
5. Sleeps `SLEEP_BETWEEN_TICKERS_SECONDS` (default 1.0 s) between *tickers* (not between models for the same ticker, since the cache makes those near-instant) so the script does not hammer Yahoo Finance during a 50-ticker run.
6. Prints a structured summary on stdout — easy to grep in the cron log file:

```jsonc
{
  "batch_id":             "recommendations_2026_05",
  "batch_date":           "2026-05-01",
  "recommendation_count": 50,
  "models":               ["Model 1", "Model 2"],
  "attempted":            100,
  "saved":                98,
  "skipped":              0,
  "data_failure_count":   2,                  // transient yfinance failures
  "model_failure_count":  0,                  // genuine prediction errors
  "error_count":          2,                  // sum of the two above
  "failed_tickers":       ["XYZ"],            // tickers with at least one data failure
  "data_failures":        [
    { "ticker": "XYZ", "model_name": "Model 1", "error": "Could not download…" },
    { "ticker": "XYZ", "model_name": "Model 2", "error": "Could not download…" }
  ],
  "model_failures":       [],
  "errors":               [
    "XYZ/Model 1 (data): Could not download…",
    "XYZ/Model 2 (data): Could not download…"
  ]
}
```

The script exits non-zero only when nothing was saved AND there were errors, so a broken cron is visible in the system mail without the noisy "every run is a failure" pattern that comes from exiting non-zero on partial-success runs.

#### Resilience constants

| Constant | Default | Purpose |
|---|---|---|
| `RETRY_ATTEMPTS` | 3 | Initial call + 2 retries per `(ticker, model)` |
| `RETRY_BASE_DELAY_SECONDS` | 1.5 | First backoff delay; doubles each subsequent retry |
| `SLEEP_BETWEEN_TICKERS_SECONDS` | 1.0 | Pause between tickers (passed to `run(sleep_between_tickers=...)` in tests) |

#### CLI

```
usage: run_monthly_backtest.py [-h] [--top-n TOP_N] [--verbose]
```

| Flag | Default | Purpose |
|---|---|---|
| `--top-n N` | 50 | Override the recommendation count, useful for testing. |
| `--verbose` | off | Switch logging to DEBUG. |

#### Public API

The script also exports `run(top_n=50, verbose=False) -> dict` so other Python code (and tests) can invoke the same pipeline without going through `argparse`.

---

### `scripts/run_evaluation.py` — Daily Cron Runner

**Purpose:** Runs the same evaluation pipeline as the **Evaluate** button on the Predictions page, but from the command line so it can be cron-driven.

The whole script is intentionally a thin wrapper. It calls `db.init_db()`, then `services.evaluation.evaluate_pending_predictions()`, logs the result, and prints the JSON summary. Because both this script and the route share the underlying function, the behaviour stays consistent regardless of trigger source.

#### CLI

```
usage: run_evaluation.py [-h] [--verbose]
```

The summary printed on stdout matches the route response shape exactly:

```
{
  "evaluated_count": 12,
  "evaluated_ids":   [101, 102, 103, ...],
  "errors":          []
}
```

---

### `tests/test_recommendations.py` — Test Suite

**Purpose:** 873-line pytest suite providing broad coverage of the screener, sentiment, database, and Flask routes.

**Test categories:**

| Category | What is tested |
|---|---|
| Universe | `get_sectors`, `get_industries`, `get_candidates` correctness |
| Score computation | All 9 `WEIGHT_CONFIG` rules triggered individually and in combination |
| Filters | `min_market_cap`, `profitable_only`, edge cases (empty input, zero cap) |
| Sort options | All `SORT_OPTIONS` variants produce correctly ordered output |
| Recommendations | `get_recommendations` end-to-end with mocked `_fetch_stock_data` and `_enrich_with_news` |
| Flask routes | `/api/recommendations`, `/api/industries`, `/api/news/<ticker>` via `app.test_client()` |
| News analysis | `score_headline`, `_is_near_duplicate`, `_recency_weight`, `analyze_headlines` |
| DB cache | `upsert_fundamentals_cache`, `get_fundamentals_cache`, TTL expiry, `upsert_news_analysis_cache` |
| Sentiment pipeline | Full `analyze_headlines` output shape and field types |

A temporary SQLite database is created per test session using pytest fixtures; `_enrich_with_news` is monkeypatched to a no-op in most tests to isolate screener logic from live network calls.

---

### `tests/test_backtests.py` — Backtest Test Suite

**Purpose:** Pytest coverage for the monthly automation, batch metadata, summary aggregation, the resilience layer, and `/api/backtests` endpoints. 45 tests, all using the same temporary-SQLite fixture pattern as `test_recommendations.py`.

| Test class | What is covered |
|---|---|
| `TestBatchId` | `make_batch_id` formatting, single-digit month padding, default `today` behaviour, custom prefix |
| `TestPredictionBatchMetadata` | `save_prediction` accepts and persists batch fields; `prediction_exists_in_batch` returns True / False correctly across `(batch_id, ticker, model_name)` combinations; `get_predictions_by_batch` orders by `recommendation_rank`; the unique partial index rejects duplicate batch rows but allows multiple `batch_id IS NULL` rows |
| `TestLegacyDatabaseMigration` | Hand-builds a pre-feature `predictions` table, inserts a legacy row, calls `init_db()` and asserts the migration ran, columns exist, legacy data survives, the unique index was created, and `_migrate_predictions_table` is idempotent |
| `TestMonthlyBacktestRunner` | Top-50 default + correct kwargs passed to `get_recommendations`; iterates over **every registered model**; saves full batch metadata on each row; **idempotency** (running the script twice in the same month creates no duplicates and reports correct skipped/saved counts); `PredictionError` is caught and recorded, never raised |
| `TestBacktestSummary` | `summarize_batch` produces correct totals + per-model accuracy; returns `None` for unknown IDs; `list_batch_summaries` returns one entry per distinct batch |
| `TestEvaluationScript` | The cron script's `run()` delegates to `services.evaluation.evaluate_pending_predictions` rather than reimplementing logic |
| `TestBacktestApi` | `/api/backtests` returns the summary list; `/api/backtests/<batch_id>` returns the per-model breakdown; unknown batch IDs return 404 |
| `TestIsTransientError` | Detects 401/429/timeout/5xx/connection patterns; walks the `__cause__` chain (so a `PredictionError` chained from a 401 still counts as transient); returns False for genuine model errors |
| `TestWithRetries` | Returns immediately on success; recovers after transient failures within the attempt budget; raises the last exception once attempts are exhausted; **does not retry non-transient errors** |
| `TestRetryAndResilience` | Cron script retries transient errors; classifies them as `data_failure` after exhaustion; classifies non-transient errors as `model_failure` and does not retry; one bad ticker does not break the rest of the batch and shows up exactly once in `failed_tickers` |
| `TestDownloadCacheReuse` | The in-process cache in `models.model_1.download_data` makes the second call for the same ticker skip the network |
| `TestStaleFundamentalsFallback` | When `yf.Ticker(...).info` raises (e.g. a 401), `services.recommendations._fetch_stock_data` returns the stale fundamentals row instead of dropping the ticker |

`run_model` and `get_recommendations` are mocked in every backtest-runner test so the pytest run never touches Yahoo Finance, and a `_no_sleeps` autouse fixture monkeypatches `time.sleep` so retry backoff and inter-ticker pauses do not stretch the suite.

---

## 4. Frontend Layer

| File | Purpose |
|---|---|
| `templates/base.html` | Shared layout, nav bar, CSS/JS includes |
| `templates/index.html` | Dual model comparison form and result cards |
| `templates/watchlist.html` | Watchlist table with run/run-all controls |
| `templates/predictions.html` | History table, evaluate button, performance panel |
| `templates/recommendations.html` | Tabbed view: **Likely Gainers** (filter sidebar + results table) and **Likely Decliners** (filter sidebar + downside-card grid). Shared details overlay element for both tabs. |
| `static/style.css` | Shared styles, including tab switcher, decliner cards, risk-level badges, and traffic-light news sentiment dot |
| `static/script.js` | Parallel `fetch` to `/predict` for both models, fake progress bar animation |
| `static/watchlist.js` | CRUD calls to watchlist API, run-all orchestration |
| `static/predictions.js` | History load, filter, evaluate trigger, performance display |
| `static/recommendations.js` | Both tabs: gainers screener (filters, table render, news overlay, add-to-watchlist) and decliners scanner (filters, card grid render, downside details overlay with technical snapshot, sentiment dot, "Why Flagged" list, recent headlines, Add-to-Watchlist / Close actions) |

`script.js` uses `Promise.all([fetch("/predict", m1_body), fetch("/predict", m2_body)])` to fire both model predictions simultaneously, cutting total wait time roughly in half compared to sequential requests.

---

## 5. Database Schema

```sql
CREATE TABLE watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT UNIQUE NOT NULL,
    company_name    TEXT,
    is_owned        INTEGER DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    date_added      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE fundamentals_cache (
    ticker          TEXT PRIMARY KEY,
    last_updated    TEXT,
    current_price   REAL,
    week_52_high    REAL,
    week_52_low     REAL,
    ma_200          REAL,
    month_return    REAL,
    market_cap      REAL,
    trailing_eps    REAL,
    free_cash_flow  REAL,
    revenue_growth  REAL,
    debt_to_equity  REAL,
    return_on_equity REAL
);

CREATE TABLE news_analysis_cache (
    ticker              TEXT PRIMARY KEY,
    last_updated        TEXT,
    sentiment_label     TEXT,
    sentiment_score     REAL,
    sentiment_color     TEXT,
    news_adjustment     REAL,
    risk_flags          TEXT,    -- JSON array
    positive_catalysts  TEXT,    -- JSON array
    summary             TEXT
);

CREATE TABLE predictions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name            TEXT NOT NULL DEFAULT 'Model 1',
    ticker                TEXT NOT NULL,
    prediction_date       TEXT NOT NULL,
    latest_close          REAL NOT NULL,
    predicted_return      REAL NOT NULL,
    predicted_price       REAL NOT NULL,
    predicted_direction   TEXT NOT NULL,
    forecast_horizon_days INTEGER NOT NULL DEFAULT 30,
    status                TEXT NOT NULL DEFAULT 'pending',
    actual_price          REAL,
    actual_return         REAL,
    actual_direction      TEXT,
    direction_correct     INTEGER,
    magnitude_comparison  TEXT,
    prediction_error      REAL,
    evaluated_at          TEXT,
    created_at            TEXT NOT NULL,

    -- Monthly backtest metadata (added by the automated backtesting feature).
    -- Existing databases are migrated in-place via _ensure_prediction_batch_columns.
    batch_id              TEXT,                                  -- e.g. 'recommendations_2026_05'
    batch_date            TEXT,                                  -- ISO date the cron run started
    prediction_source     TEXT NOT NULL DEFAULT 'manual',        -- 'manual' | 'monthly_backtest'
    recommendation_rank   INTEGER,                               -- rank in the top-N list at time of run
    recommendation_score  REAL                                   -- recommendation_score at time of run
);

-- Unique partial index: enforces (batch_id, ticker, model_name) uniqueness
-- for monthly-backtest rows while leaving legacy / manual rows
-- (batch_id IS NULL) out of the uniqueness check entirely.  Also serves
-- as the read index for batch lookups since batch_id is the leading column.
CREATE UNIQUE INDEX idx_predictions_batch_unique
    ON predictions(batch_id, ticker, model_name)
    WHERE batch_id IS NOT NULL;
```

#### Monthly backtest columns

| Column | Populated when | Used by |
|---|---|---|
| `batch_id` | `scripts/run_monthly_backtest.py` writes `recommendations_YYYY_MM`. Manual UI predictions leave it `NULL`. | `prediction_exists_in_batch`, `get_predictions_by_batch`, `/api/backtests/<batch_id>` |
| `batch_date` | Set to the cron run's calendar date | Sort order in `/api/backtests` |
| `prediction_source` | `'manual'` for UI rows, `'monthly_backtest'` for cron rows | Distinguishes automated vs ad-hoc rows in summaries |
| `recommendation_rank` | Set to the row's position in the top-N list (1-indexed) | Sort order in `get_predictions_by_batch` |
| `recommendation_score` | Captured from `recommendation_score` at the moment the prediction is made | Lets the inspection API show the score that drove inclusion |

`_ensure_prediction_batch_columns` runs as part of `init_db()` and uses `PRAGMA table_info(predictions)` to detect missing columns, then issues `ALTER TABLE ADD COLUMN` for each one. Existing databases pick up the new schema on the next app boot or cron run; no manual migration step is required.

---

## 6. Automated Monthly Backtesting

This section is the canonical reference for the monthly backtest automation. The high-level pitch lives in `Documents/Overview.md` § 3a; what follows is the implementation-level detail.

### 6.1 Why this exists

Manually picking 50 stocks each month and running every model on each one would be tedious and inconsistent. Automating it:

- Produces a **fair, repeatable** comparison dataset across models.
- Makes "Model X is better than Model Y" a measurable claim instead of a feeling.
- Auto-evaluates itself once the 30-day horizon elapses — no human action required.

### 6.2 How a monthly batch is created

```
cron @ first-of-month  →  scripts/run_monthly_backtest.py
                              │
                              ├─ make_batch_id(today)     # → 'recommendations_2026_05'
                              ├─ get_recommendations(...) # top 50, page defaults
                              └─ for each (ticker, model) in 50 × all_models:
                                   ├─ if prediction_exists_in_batch → skip
                                   ├─ run_model(model, ticker)
                                   └─ db.save_prediction(..., batch_id, batch_date,
                                                         prediction_source='monthly_backtest',
                                                         recommendation_rank,
                                                         recommendation_score)
```

The page defaults are hard-coded into the script as constants so they never drift from the UI:

```python
DEFAULT_TOP_N             = 50
DEFAULT_MIN_MARKET_CAP    = 1_000_000_000.0   # matches HTML `$1 B+` selected option
DEFAULT_SORT_BY           = "score"
DEFAULT_FORECAST_HORIZON_DAYS = 30
```

### 6.3 How all available models are used

The cron script never references "Model 1" or "Model 2" by name. It iterates over `models.get_available_models()` instead, which reads from `MODEL_BUILDERS`. Adding a future model:

1. Implement `models/model_3.py` exporting a `build_prediction(ticker)` function with the same return shape.
2. Import it into `models/__init__.py` and add `"Model 3": build_prediction_m3` to the `MODEL_BUILDERS` dict.

That's the entire change. The next cron run will automatically include Model 3 alongside the others.

### 6.4 How `batch_id` works

| Aspect | Detail |
|---|---|
| Format | `recommendations_YYYY_MM` (e.g. `recommendations_2026_05`) |
| Source | `services.backtests.make_batch_id(date.today())` |
| Persistence | Written to every row created by the cron run |
| Indexing | `CREATE UNIQUE INDEX idx_predictions_batch_unique ON predictions(batch_id, ticker, model_name) WHERE batch_id IS NOT NULL` — doubles as duplicate protection at the DB level *and* a fast lookup index since `batch_id` is the leading column |
| Use cases | Idempotency check; per-batch summaries; `/api/backtests/<batch_id>` lookup; future UI grouping |

### 6.5 How duplicate protection works

Two layers protect against duplicates:

1. **Application layer.** Before each `(ticker, model_name)` write, the script calls `db.prediction_exists_in_batch(batch_id, ticker, model_name)` — a one-row lookup against the predictions table. If a row already exists the script logs `[skip]` and increments the skipped counter; nothing is written.
2. **Database layer.** `idx_predictions_batch_unique` is a `UNIQUE` partial index on `(batch_id, ticker, model_name) WHERE batch_id IS NOT NULL`. Even if the application-level check is bypassed (concurrent execution, manual `INSERT`, etc.), SQLite raises `IntegrityError` on the second insert. Manual rows with `batch_id IS NULL` are excluded from the uniqueness check by the `WHERE` clause, so they do not collide with one another.

Two consequences:

1. **Re-running the script in the same calendar month is safe.** If cron fires twice (e.g. you re-run it manually), no duplicate rows are created.
2. **Re-running the script in a *different* month is also safe.** Different months produce different `batch_id`s, so the same ticker/model produces a fresh row.

The duplicate check is intentionally separate from the existing `prediction_exists(model_name, ticker, prediction_date)` helper because they answer different questions: the older one prevents two predictions for the same calendar day from any source, and the new one prevents two predictions in the same monthly batch.

### 6.6 How the evaluation script works

`scripts/run_evaluation.py` is **not** a separate evaluation algorithm. It is a thin wrapper that:

1. Calls `db.init_db()` (to apply any pending migrations).
2. Calls `services.evaluation.evaluate_pending_predictions()` — the same function the **Evaluate** UI button has always called.
3. Logs and prints the JSON summary.

The shared service iterates over `db.get_pending_predictions()`, skips rows where `today < prediction_date + forecast_horizon_days`, and for each eligible row downloads the actual close price via `yf.download` and records `actual_return`, `direction_correct`, `magnitude_comparison`, and `prediction_error`. Predictions made by the monthly backtest are picked up automatically because they have `status = 'pending'` like any other prediction.

### 6.7 How to run both scripts manually

From the repo root (or `/var/www/Financial-Model` on the server):

```bash
# Monthly backtest – top 50 by default, override with --top-n
venv/bin/python scripts/run_monthly_backtest.py
venv/bin/python scripts/run_monthly_backtest.py --top-n 5 --verbose   # quick smoke test

# Evaluate pending predictions whose horizon has elapsed
venv/bin/python scripts/run_evaluation.py
```

Both scripts emit a final JSON summary on stdout; logs go through Python's logging module with timestamps.

### 6.8 How to set up the cron jobs on DigitalOcean

See `Documents/DEPLOYMENT.md` § "Automated Cron Jobs" for the full command. The two recommended entries:

```
# Monthly backtest – first of every month at 09:00 UTC
0 9 1 * * cd /var/www/Financial-Model && /var/www/Financial-Model/venv/bin/python scripts/run_monthly_backtest.py >> /var/www/Financial-Model/logs/monthly_backtest.log 2>&1

# Daily evaluation – every day at midnight UTC
0 0 * * * cd /var/www/Financial-Model && /var/www/Financial-Model/venv/bin/python scripts/run_evaluation.py >> /var/www/Financial-Model/logs/evaluation.log 2>&1
```

The deployment doc also lists the one-time `mkdir -p /var/www/Financial-Model/logs` and `chmod` step.

### 6.9 Database fields added

Five columns added to the `predictions` table; an index added on `batch_id`. Schema details in § 5 above. Existing databases are migrated automatically by `_ensure_prediction_batch_columns(conn)` inside `init_db()` — no manual migration required.

### 6.10 Failure handling

The monthly backtest treats Yahoo Finance flakiness as a routine event rather than an exceptional one. The flow looks like:

```
for each (ticker, model):
    with_retries(run_model, model, ticker, attempts=3, base_delay=1.5)
        ├─ transient HTTP error → wait 1.5 s → retry → wait 3 s → retry → give up
        └─ non-transient error  → re-raise immediately (no retries)

after retries exhausted:
    is_transient_error(exc)?
        ├─ True  → outcome = data_failure   → ticker recorded in failed_tickers
        └─ False → outcome = model_failure
```

| Detection | Patterns matched (any) |
|---|---|
| HTTP status codes | 401, 429, 500, 502, 503, 504 |
| Message keywords | unauthorized, too many requests, rate limit, timed out, connection (refused/reset/aborted), temporarily unavailable, bad gateway, service unavailable, gateway timeout, remote end closed |
| Exception types | `urllib.error.URLError/HTTPError`, `requests.exceptions.{ConnectionError,Timeout,HTTPError}`, `TimeoutError`, `ConnectionError` |

Detection walks `__cause__` and `__context__`, so `PredictionError("Could not download stock data") from urllib.error.HTTPError(401)` is correctly classified as transient even though the outer exception type is `PredictionError`.

Two complementary mitigations reduce the load on Yahoo before retries even kick in:

1. **In-process download caches** in `models/model_1.py` and `models/model_2.py`. The first time `download_data("AAPL", ...)` is called, the resulting DataFrame is stored under `(ticker, start, end)` for 5 minutes; subsequent calls within that window skip the network. SPY and `^VIX` are cached the same way, so a 50-ticker Model 2 pass downloads them once instead of 100 times.
2. **Stale-cache fallback** in `services/recommendations._fetch_stock_data`. If the live `yf.Ticker(ticker).info` call raises, the function returns the existing (expired) fundamentals row instead of dropping the ticker entirely.

### 6.11 Assumptions and limitations

- **Yahoo Finance availability.** Both scripts depend on `yfinance` working at the moment they run. Per-ticker retries + per-pair classification keep a single transient blip from breaking the run, but a multi-hour outage will still produce a partly-empty batch (recorded in `failed_tickers`).
- **Top-50 is a snapshot.** The recommendation list reflects fundamentals + news at the moment the script runs. A stock that drops out of the top 50 next month is not removed from this month's batch — that is intentional, since each batch is a frozen forward-test.
- **Single-process SQLite.** The cron runs and the Flask app share the same `financial_model.db` file. SQLite's locking is fine for this volume (one cron run per month, low concurrent writes), but heavy concurrent traffic against the same DB could surface "database is locked" errors. If that becomes a problem, switching to WAL mode (`PRAGMA journal_mode=WAL`) is the cheap first step.
- **No retraining.** Each prediction trains a fresh XGBoost model from scratch using the existing `build_prediction` pipeline. Backtest accuracy is therefore equivalent to "what would a user have seen if they ran the model manually at this moment", which is the property the project explicitly wants.
- **Same-day duplicates from manual UI.** The `prediction_exists_in_batch` check only looks at the monthly batch. If a user happens to also click *Run* on the watchlist for the same `(ticker, model_name, date)` between the cron run and the duplicate check, the older `prediction_exists(...)` helper catches it instead. Both checks coexist cleanly.
