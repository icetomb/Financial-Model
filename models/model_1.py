from __future__ import annotations

import argparse
import re
import warnings
from datetime import date, timedelta

import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from xgboost import XGBRegressor

warnings.filterwarnings("ignore")

DEFAULT_START_DATE = "2015-01-01"
FORECAST_HORIZON_DAYS = 30
TRAIN_RATIO = 0.8
MIN_TRAIN_SAMPLES = 40
MIN_TEST_SAMPLES = 10
TICKER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$")

FEATURE_COLUMNS = [
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_20",
    "ma_ratio_5",
    "ma_ratio_10",
    "ma_ratio_20",
    "ma_ratio_50",
    "volatility_5",
    "volatility_10",
    "volatility_20",
    "vol_chg_1",
    "vol_ratio_10",
    "high_low_spread",
    "open_close_spread",
    "momentum_10",
    "momentum_20",
    "rsi_14",
]


class PredictionError(ValueError):
    """Friendly error message that can be shown in the terminal or browser."""


def normalize_ticker(ticker: str) -> str:
    if not ticker or not ticker.strip():
        raise PredictionError("Please enter a ticker symbol.")

    cleaned = ticker.strip().upper()

    if not TICKER_PATTERN.fullmatch(cleaned):
        raise PredictionError(
            "Please enter a valid ticker symbol like AAPL, TSLA, MSFT, or BRK-B."
        )

    return cleaned


def download_data(ticker: str, start: str, end: str | None = None) -> pd.DataFrame:
    if end is None:
        end = (date.today() + timedelta(days=1)).isoformat()

    try:
        df = yf.download(
            ticker,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
        )
    except Exception as exc:
        raise PredictionError(
            "We could not download stock data right now. Please try again in a moment."
        ) from exc

    if df.empty:
        raise PredictionError(
            f"No price history was found for {ticker}. Check the ticker symbol and try again."
        )

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    required_columns = ["Open", "High", "Low", "Close", "Volume"]
    missing_columns = [column for column in required_columns if column not in df.columns]

    if missing_columns:
        raise PredictionError(
            "Downloaded data is missing required price columns for this ticker."
        )

    return df[required_columns].dropna().copy()


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    out["ret_1"] = out["Close"].pct_change(1)
    out["ret_5"] = out["Close"].pct_change(5)
    out["ret_10"] = out["Close"].pct_change(10)
    out["ret_20"] = out["Close"].pct_change(20)

    out["ma_5"] = out["Close"].rolling(5).mean()
    out["ma_10"] = out["Close"].rolling(10).mean()
    out["ma_20"] = out["Close"].rolling(20).mean()
    out["ma_50"] = out["Close"].rolling(50).mean()

    out["ma_ratio_5"] = out["Close"] / out["ma_5"]
    out["ma_ratio_10"] = out["Close"] / out["ma_10"]
    out["ma_ratio_20"] = out["Close"] / out["ma_20"]
    out["ma_ratio_50"] = out["Close"] / out["ma_50"]

    out["volatility_5"] = out["ret_1"].rolling(5).std()
    out["volatility_10"] = out["ret_1"].rolling(10).std()
    out["volatility_20"] = out["ret_1"].rolling(20).std()

    out["vol_chg_1"] = out["Volume"].pct_change(1)
    out["vol_ma_10"] = out["Volume"].rolling(10).mean()
    out["vol_ratio_10"] = out["Volume"] / out["vol_ma_10"]

    out["high_low_spread"] = (out["High"] - out["Low"]) / out["Close"]
    out["open_close_spread"] = (out["Close"] - out["Open"]) / out["Open"]

    out["momentum_10"] = out["Close"] - out["Close"].shift(10)
    out["momentum_20"] = out["Close"] - out["Close"].shift(20)

    return out


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()

    delta = out["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    rs = avg_gain / avg_loss
    out["rsi_14"] = 100 - (100 / (1 + rs))

    return out


def create_target(df: pd.DataFrame, forecast_horizon: int = FORECAST_HORIZON_DAYS) -> pd.DataFrame:
    out = df.copy()
    out["target_30d_return"] = out["Close"].shift(-forecast_horizon) / out["Close"] - 1
    return out


def time_split(
    X: pd.DataFrame, y: pd.Series, train_ratio: float = TRAIN_RATIO
) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
    split_index = int(len(X) * train_ratio)

    X_train = X.iloc[:split_index]
    X_test = X.iloc[split_index:]
    y_train = y.iloc[:split_index]
    y_test = y.iloc[split_index:]

    return X_train, X_test, y_train, y_test


def train_model(X_train: pd.DataFrame, y_train: pd.Series) -> XGBRegressor:
    model = XGBRegressor(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="reg:squarederror",
        random_state=42,
    )
    model.fit(X_train, y_train)
    return model


def evaluate_model(y_test: pd.Series, preds: np.ndarray) -> dict:
    mae = float(mean_absolute_error(y_test, preds))
    rmse = float(np.sqrt(mean_squared_error(y_test, preds)))
    r2 = float(r2_score(y_test, preds))
    direction_accuracy = float(np.mean((preds > 0) == (y_test > 0)))

    return {
        "mae": mae,
        "rmse": rmse,
        "r2": r2,
        "direction_accuracy": direction_accuracy,
    }


def build_prediction(
    ticker: str,
    start: str = DEFAULT_START_DATE,
    end: str | None = None,
    forecast_horizon: int = FORECAST_HORIZON_DAYS,
) -> dict:
    symbol = normalize_ticker(ticker)
    price_df = download_data(symbol, start=start, end=end)

    feature_df = add_rsi(add_features(price_df))
    latest_feature_rows = feature_df.dropna(subset=FEATURE_COLUMNS).copy()

    if latest_feature_rows.empty:
        raise PredictionError(
            f"There is not enough clean price history to build features for {symbol}."
        )

    latest_row = latest_feature_rows.iloc[-1]

    model_df = create_target(feature_df, forecast_horizon=forecast_horizon).dropna().copy()

    if model_df.empty:
        raise PredictionError(
            f"There is not enough historical data to train the model for {symbol}."
        )

    X = model_df[FEATURE_COLUMNS].copy()
    y = model_df["target_30d_return"].copy()

    X_train, X_test, y_train, y_test = time_split(X, y)

    if len(X_train) < MIN_TRAIN_SAMPLES or len(X_test) < MIN_TEST_SAMPLES:
        raise PredictionError(
            f"{symbol} does not have enough history for a reliable 30-day prediction yet."
        )

    model = train_model(X_train, y_train)
    test_predictions = model.predict(X_test)
    metrics = evaluate_model(y_test, test_predictions)

    latest_features = latest_row[FEATURE_COLUMNS].to_frame().T
    predicted_return = float(model.predict(latest_features)[0])
    latest_close = float(latest_row["Close"])
    estimated_price_30d = float(latest_close * (1 + predicted_return))

    if predicted_return > 0:
        outlook = "Bullish"
        summary = (
            f"The model suggests a positive 30-day outlook for {symbol} based on recent price trends."
        )
    elif predicted_return < 0:
        outlook = "Bearish"
        summary = (
            f"The model suggests a weaker 30-day outlook for {symbol}, so a cautious view may be helpful."
        )
    else:
        outlook = "Neutral"
        summary = f"The model expects a fairly flat 30-day move for {symbol}."

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


def format_prediction_report(result: dict) -> str:
    metrics = result["metrics"]

    lines = [
        f"30-Day Stock Prediction for {result['ticker']}",
        "-" * 40,
        f"Latest Close Price:         {result['latest_close']:.2f}",
        f"Predicted 30-Day Return:    {result['predicted_return']:.4%}",
        f"Estimated Price in 30 Days: {result['estimated_price_30d']:.2f}",
        f"Outlook:                    {result['outlook']}",
        "",
        "Model Evaluation",
        "-" * 40,
        f"MAE:                        {metrics['mae']:.6f}",
        f"RMSE:                       {metrics['rmse']:.6f}",
        f"R^2:                        {metrics['r2']:.6f}",
        f"Direction Accuracy:         {metrics['direction_accuracy']:.2%}",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Predict the next 30-day return for a stock ticker."
    )
    parser.add_argument("ticker", nargs="?", default="AAPL", help="Ticker symbol")
    args = parser.parse_args()

    try:
        result = build_prediction(args.ticker)
    except PredictionError as exc:
        print(f"Error: {exc}")
        raise SystemExit(1) from exc

    print(format_prediction_report(result))


if __name__ == "__main__":
    main()
