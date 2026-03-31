"""
Model 2 — Market-aware 30-day stock predictor.

Extends Model 1's stock-specific technical features with broader market
context from SPY (S&P 500 ETF) and VIX (CBOE Volatility Index).

Feature breakdown:
  - 18 stock-specific technical features  (same as Model 1)
  -  5 SPY-based market trend features
  -  4 VIX-based volatility regime features
  -  2 combined stock-vs-market features
  - 29 total features

Model 1 = stock technicals only
Model 2 = stock technicals + market context
"""

from __future__ import annotations

import warnings
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

# Reuse shared helpers from Model 1 instead of duplicating code
from models.model_1 import (
    DEFAULT_START_DATE,
    FEATURE_COLUMNS as STOCK_FEATURE_COLUMNS,
    FORECAST_HORIZON_DAYS,
    MIN_TEST_SAMPLES,
    MIN_TRAIN_SAMPLES,
    PredictionError,
    add_features,
    add_rsi,
    create_target,
    download_data,
    evaluate_model,
    normalize_ticker,
    time_split,
    train_model,
)

warnings.filterwarnings("ignore")

# -- SPY features (market trend) --
SPY_FEATURE_COLUMNS = [
    "spy_ret_1",       # SPY 1-day return
    "spy_ret_5",       # SPY 5-day return
    "spy_ret_30",      # SPY 30-day return
    "spy_ma_ratio_50", # SPY price relative to its 50-day MA
    "spy_above_ma50",  # 1 if SPY is above its 50-day MA, else 0
]

# -- VIX features (volatility regime) --
VIX_FEATURE_COLUMNS = [
    "vix_close",     # Current VIX level
    "vix_change_5",  # VIX 5-day percent change
    "vix_ma_20",     # VIX 20-day rolling average
    "vix_elevated",  # 1 if VIX > 25 (elevated volatility flag)
]

# -- Combined features (stock relative to market) --
COMBINED_FEATURE_COLUMNS = [
    "relative_ret_5",    # Stock 5-day return minus SPY 5-day return
    "stock_spy_corr_20", # 20-day rolling correlation between stock and SPY
]

# Full feature set used by Model 2
FEATURE_COLUMNS = (
    list(STOCK_FEATURE_COLUMNS)
    + SPY_FEATURE_COLUMNS
    + VIX_FEATURE_COLUMNS
    + COMBINED_FEATURE_COLUMNS
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _download_close(ticker: str, start: str, end: str | None = None) -> pd.Series:
    """Download the Close price series for a market ticker (SPY or ^VIX)."""
    if end is None:
        end = (date.today() + timedelta(days=1)).isoformat()

    try:
        df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    except Exception as exc:
        raise PredictionError(
            f"Could not download {ticker} data. Please try again."
        ) from exc

    if df.empty:
        raise PredictionError(f"No data found for {ticker}.")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df["Close"].dropna()


# ---------------------------------------------------------------------------
# Feature engineering (market-specific)
# ---------------------------------------------------------------------------

def _add_spy_features(spy_close: pd.Series) -> pd.DataFrame:
    """Compute market trend features from SPY close prices."""
    out = pd.DataFrame(index=spy_close.index)
    out["spy_ret_1"] = spy_close.pct_change(1)
    out["spy_ret_5"] = spy_close.pct_change(5)
    out["spy_ret_30"] = spy_close.pct_change(30)
    spy_ma_50 = spy_close.rolling(50).mean()
    out["spy_ma_ratio_50"] = spy_close / spy_ma_50
    out["spy_above_ma50"] = (spy_close > spy_ma_50).astype(int)
    return out


def _add_vix_features(vix_close: pd.Series) -> pd.DataFrame:
    """Compute volatility regime features from VIX close prices."""
    out = pd.DataFrame(index=vix_close.index)
    out["vix_close"] = vix_close
    out["vix_change_5"] = vix_close.pct_change(5)
    out["vix_ma_20"] = vix_close.rolling(20).mean()
    out["vix_elevated"] = (vix_close > 25).astype(int)
    return out


# ---------------------------------------------------------------------------
# Main prediction pipeline
# ---------------------------------------------------------------------------

def build_prediction(
    ticker: str,
    start: str = DEFAULT_START_DATE,
    end: str | None = None,
    forecast_horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    """
    Build a 30-day prediction using stock technicals + SPY/VIX market context.

    Downloads three data sources (stock, SPY, VIX), engineers features from
    each, merges them by date, then trains an XGBoost regressor.

    Returns the same dict structure as Model 1 so the rest of the app can
    treat both models identically.
    """
    symbol = normalize_ticker(ticker)

    # -- Download all three data sources --
    stock_df = download_data(symbol, start=start, end=end)
    spy_close = _download_close("SPY", start=start, end=end)
    vix_close = _download_close("^VIX", start=start, end=end)

    # -- Stock-specific features (identical to Model 1) --
    stock_features = add_rsi(add_features(stock_df))

    # -- Market features --
    spy_features = _add_spy_features(spy_close)
    vix_features = _add_vix_features(vix_close)

    # -- Merge by date (inner join keeps only common trading days) --
    feature_df = stock_features.join(spy_features, how="inner")
    feature_df = feature_df.join(vix_features, how="inner")

    # -- Combined features (computed on the already-aligned dataframe) --
    feature_df["relative_ret_5"] = feature_df["ret_5"] - feature_df["spy_ret_5"]
    feature_df["stock_spy_corr_20"] = (
        feature_df["ret_1"].rolling(20).corr(feature_df["spy_ret_1"])
    )

    # -- Validate we have enough clean rows --
    latest_feature_rows = feature_df.dropna(subset=FEATURE_COLUMNS).copy()

    if latest_feature_rows.empty:
        raise PredictionError(
            f"Not enough clean data to build Model 2 features for {symbol}. "
            f"This can happen if SPY or VIX data does not overlap enough with the stock."
        )

    latest_row = latest_feature_rows.iloc[-1]

    # -- Create target and train/test split --
    model_df = create_target(feature_df, forecast_horizon=forecast_horizon).dropna().copy()

    if model_df.empty:
        raise PredictionError(
            f"Not enough historical data to train Model 2 for {symbol}."
        )

    X = model_df[FEATURE_COLUMNS].copy()
    y = model_df["target_30d_return"].copy()

    X_train, X_test, y_train, y_test = time_split(X, y)

    if len(X_train) < MIN_TRAIN_SAMPLES or len(X_test) < MIN_TEST_SAMPLES:
        raise PredictionError(
            f"{symbol} does not have enough history for a reliable Model 2 prediction."
        )

    # -- Train, evaluate, predict --
    model = train_model(X_train, y_train)
    test_predictions = model.predict(X_test)
    metrics = evaluate_model(y_test, test_predictions)

    latest_features = latest_row[FEATURE_COLUMNS].to_frame().T
    predicted_return = float(model.predict(latest_features)[0])
    latest_close = float(latest_row["Close"])
    estimated_price_30d = float(latest_close * (1 + predicted_return))

    # -- Build outlook text --
    if predicted_return > 0:
        outlook = "Bullish"
        summary = (
            f"Model 2 suggests a positive 30-day outlook for {symbol} "
            f"based on stock technicals and current market conditions."
        )
    elif predicted_return < 0:
        outlook = "Bearish"
        summary = (
            f"Model 2 suggests a weaker 30-day outlook for {symbol}, "
            f"considering both stock-specific and broader market signals."
        )
    else:
        outlook = "Neutral"
        summary = f"Model 2 expects a fairly flat 30-day move for {symbol}."

    latest_data_date = (
        latest_row.name.strftime("%Y-%m-%d")
        if hasattr(latest_row.name, "strftime")
        else str(latest_row.name)
    )

    return {
        "ticker": symbol,
        "latest_close": latest_close,
        "predicted_return": predicted_return,
        "estimated_price_30d": estimated_price_30d,
        "forecast_horizon_days": forecast_horizon,
        "outlook": outlook,
        "summary": summary,
        "latest_data_date": latest_data_date,
        "metrics": metrics,
        "samples": {
            "total": int(len(X)),
            "train": int(len(X_train)),
            "test": int(len(X_test)),
        },
    }
