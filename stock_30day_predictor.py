# stock_30day_predictor.py

import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from xgboost import XGBRegressor


def download_data(ticker: str, start: str, end: str) -> pd.DataFrame:
    df = yf.download(ticker, start=start, end=end, auto_adjust=True)

    if df.empty:
        raise ValueError(f"No data downloaded for ticker {ticker}.")

    # Keep only needed columns
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    df = df.dropna()
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()

    # Basic returns
    out["ret_1"] = out["Close"].pct_change(1)
    out["ret_5"] = out["Close"].pct_change(5)
    out["ret_10"] = out["Close"].pct_change(10)
    out["ret_20"] = out["Close"].pct_change(20)

    # Moving averages
    out["ma_5"] = out["Close"].rolling(5).mean()
    out["ma_10"] = out["Close"].rolling(10).mean()
    out["ma_20"] = out["Close"].rolling(20).mean()
    out["ma_50"] = out["Close"].rolling(50).mean()

    # Ratios to moving averages
    out["ma_ratio_5"] = out["Close"] / out["ma_5"]
    out["ma_ratio_10"] = out["Close"] / out["ma_10"]
    out["ma_ratio_20"] = out["Close"] / out["ma_20"]
    out["ma_ratio_50"] = out["Close"] / out["ma_50"]

    # Rolling volatility
    out["volatility_5"] = out["ret_1"].rolling(5).std()
    out["volatility_10"] = out["ret_1"].rolling(10).std()
    out["volatility_20"] = out["ret_1"].rolling(20).std()

    # Volume features
    out["vol_chg_1"] = out["Volume"].pct_change(1)
    out["vol_ma_10"] = out["Volume"].rolling(10).mean()
    out["vol_ratio_10"] = out["Volume"] / out["vol_ma_10"]

    # Price range features
    out["high_low_spread"] = (out["High"] - out["Low"]) / out["Close"]
    out["open_close_spread"] = (out["Close"] - out["Open"]) / out["Open"]

    # Momentum
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


def create_target(df: pd.DataFrame, forecast_horizon: int = 30) -> pd.DataFrame:
    out = df.copy()

    # Predict 30-day forward return
    out["target_30d_return"] = out["Close"].shift(-forecast_horizon) / out["Close"] - 1

    return out


def prepare_dataset(ticker: str, start: str, end: str, forecast_horizon: int = 30):
    df = download_data(ticker, start, end)
    df = add_features(df)
    df = add_rsi(df)
    df = create_target(df, forecast_horizon=forecast_horizon)

    df = df.dropna().copy()

    feature_cols = [
        "ret_1", "ret_5", "ret_10", "ret_20",
        "ma_ratio_5", "ma_ratio_10", "ma_ratio_20", "ma_ratio_50",
        "volatility_5", "volatility_10", "volatility_20",
        "vol_chg_1", "vol_ratio_10",
        "high_low_spread", "open_close_spread",
        "momentum_10", "momentum_20",
        "rsi_14",
    ]

    X = df[feature_cols].copy()
    y = df["target_30d_return"].copy()

    return df, X, y, feature_cols


def time_split(X: pd.DataFrame, y: pd.Series, train_ratio: float = 0.8):
    split_idx = int(len(X) * train_ratio)

    X_train = X.iloc[:split_idx]
    X_test = X.iloc[split_idx:]

    y_train = y.iloc[:split_idx]
    y_test = y.iloc[split_idx:]

    return X_train, X_test, y_train, y_test


def train_model(X_train: pd.DataFrame, y_train: pd.Series) -> XGBRegressor:
    model = XGBRegressor(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        objective="reg:squarederror",
        random_state=42
    )

    model.fit(X_train, y_train)
    return model


def evaluate_model(y_test: pd.Series, preds: np.ndarray) -> None:
    mae = mean_absolute_error(y_test, preds)
    rmse = np.sqrt(mean_squared_error(y_test, preds))
    r2 = r2_score(y_test, preds)

    direction_acc = np.mean((preds > 0) == (y_test > 0))

    print("\nModel Evaluation")
    print("-" * 40)
    print(f"MAE:               {mae:.6f}")
    print(f"RMSE:              {rmse:.6f}")
    print(f"R^2:               {r2:.6f}")
    print(f"Direction Accuracy:{direction_acc:.4f}")


def show_feature_importance(model: XGBRegressor, feature_cols: list) -> None:
    importances = model.feature_importances_
    importance_df = pd.DataFrame({
        "Feature": feature_cols,
        "Importance": importances
    }).sort_values("Importance", ascending=False)

    print("\nTop Feature Importances")
    print("-" * 40)
    print(importance_df.to_string(index=False))


def predict_next_30d_return(model: XGBRegressor, X: pd.DataFrame, df: pd.DataFrame) -> None:
    latest_features = X.iloc[[-1]]
    pred_return = model.predict(latest_features)[0]

    current_close = df["Close"].iloc[-1]
    predicted_price_30d = current_close * (1 + pred_return)

    print("\nLatest Forecast")
    print("-" * 40)
    print(f"Current Close Price:        {current_close:.2f}")
    print(f"Predicted 30-Day Return:    {pred_return:.4%}")
    print(f"Estimated Price in 30 Days: {predicted_price_30d:.2f}")


def main():
    ticker = "AAPL"
    start_date = "2015-01-01"
    end_date = "2025-12-31"
    forecast_horizon = 30

    print(f"Building 30-day stock return model for {ticker}...")

    df, X, y, feature_cols = prepare_dataset(
        ticker=ticker,
        start=start_date,
        end=end_date,
        forecast_horizon=forecast_horizon
    )

    X_train, X_test, y_train, y_test = time_split(X, y, train_ratio=0.8)

    print(f"Total samples: {len(X)}")
    print(f"Train samples: {len(X_train)}")
    print(f"Test samples:  {len(X_test)}")

    model = train_model(X_train, y_train)

    preds = model.predict(X_test)

    evaluate_model(y_test, preds)
    show_feature_importance(model, feature_cols)
    predict_next_30d_return(model, X, df)


if __name__ == "__main__":
    main()