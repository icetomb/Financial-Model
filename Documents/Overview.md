# Financial Model — Project Overview

## What Is This Project?

**Financial Model** is a self-hosted stock analysis web application built with Python and Flask. It combines machine-learning-based price prediction, a fundamentals-driven stock screener, a personal watchlist, and a news-sentiment engine — all accessible through a clean browser interface. The project is designed for individual research and educational use, running entirely locally with no paid data sources or cloud dependencies.

---

## Core Features

### 1. 30-Day Price Prediction (Dual Model)

The home page lets you enter any US stock ticker and generate a **30-day forward return forecast** using two distinct XGBoost models side by side:

- **Model 1 — Technical Signals:** Trained on price-derived features such as moving averages, RSI, momentum, and volume. Captures stock-specific patterns.
- **Model 2 — Market-Aware:** Extends Model 1 by incorporating **SPY** (S&P 500 ETF) and **VIX** (volatility index) data, including rolling correlations between the stock and the broader market.

Both models are trained fresh on each prediction request using historical data from Yahoo Finance. Results include a predicted return percentage, a predicted price, and a directional label (up / down / neutral).

---

### 2. Watchlist

A personal tracker where you can:

- Add and remove tickers
- Mark tickers as owned or active
- Run predictions for a single ticker or all tickers at once directly from the watchlist view
- Have predictions automatically saved to history for later evaluation

---

### 3. Predictions History & Backtesting

The predictions page stores every saved forecast and evaluates it once the 30-day horizon has passed:

- Compares the predicted direction and return against the actual price movement fetched from Yahoo Finance
- Calculates error metrics (predicted vs. actual return)
- Displays a per-model performance summary including directional accuracy and average errors

---

### 4. Stock Screener & Recommendations

A rule-based screener that scores a curated universe of ~200+ large-cap US stocks on a 0–100 scale using:

- **Value signals:** distance from 52-week high/low, 1-month return, position relative to 200-day moving average
- **Fundamental quality:** profitability, free cash flow, revenue growth, debt-to-equity ratio, return on equity

Filters available: sector, industry, minimum market cap, profitable companies only. Results can be sorted by score, price, distance from high, or market cap.

---

### 5. News Sentiment Analysis

Every stock in the screener is enriched with a news sentiment score derived from recent headlines:

- Headlines are pulled from Yahoo Finance via yfinance
- Scored using a keyword-based positive/negative dictionary and recency decay weighting
- Near-duplicate headlines are detected and removed to avoid double-counting
- A final sentiment label (Bullish / Bearish / Neutral / Mixed) and score adjustment (±10 points) is applied to the screener score
- Theme flags (risk events, positive catalysts) are extracted and surfaced in the detail overlay

---

## Data Sources

All market data is retrieved from **Yahoo Finance** through the `yfinance` library — no API keys, no paid subscriptions. Sources include:

- Historical OHLCV price data for model training and evaluation
- Company fundamentals (`Ticker.info`) for the screener
- Recent news headlines for sentiment analysis
- SPY and VIX historical data for Model 2

---

## Technology Stack

| Layer | Technology |
|---|---|
| Web framework | Flask |
| Machine learning | XGBoost, scikit-learn |
| Data access | yfinance, pandas, numpy |
| Storage | SQLite (local file) |
| Frontend | Vanilla HTML, CSS, JavaScript (Jinja2 templates) |
| Testing | pytest |

---

## Application Pages

| Page | Purpose |
|---|---|
| `/` | Side-by-side Model 1 vs Model 2 prediction comparison |
| `/watchlist` | Manage tracked tickers, run predictions |
| `/predictions` | View prediction history, trigger evaluation |
| `/recommendations` | Browse screener results, filter, read news detail |

---

## Project Philosophy

- **Local-first:** No deployment config, no auth, no cloud services — runs with `flask run`
- **Transparent scoring:** Every screener penalty and bonus is explained alongside the score
- **Modular:** Models, services, database, and routes are clearly separated for easy extension
- **Research-oriented:** Designed for learning and experimentation rather than production trading
