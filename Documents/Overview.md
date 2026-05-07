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

### 3a. Automated Monthly Backtesting

A pair of cron-driven scripts turn the predictions history into a continuously-running, fully automated monthly backtest:

- **`scripts/run_monthly_backtest.py`** — once a month, pulls the **top 50 recommendations** using the same defaults as the Recommendations page (no sector / industry / sort overrides, profitable-only off, $1 B+ market cap floor) and runs **every registered prediction model** (currently Model 1 + Model 2) against each ticker with a 30-day horizon. Each prediction is saved to the existing `predictions` table tagged with a monthly batch ID such as `recommendations_2026_05`.
- **`scripts/run_evaluation.py`** — runs daily, calling the same evaluation logic that powers the **Evaluate** button on the Predictions page. Any prediction whose 30-day horizon has elapsed is scored against actual Yahoo Finance closing prices.

The result is a self-updating, model-vs-model performance dataset: each monthly batch becomes a 30-day forward-test that is automatically evaluated as soon as the horizon completes.

**Why it exists:** comparing model accuracy fairly requires a constant stream of identical-conditions predictions. Manually picking 50 stocks and running both models every month would be tedious and inconsistent; this automation guarantees the same recommendation engine output is back-tested, by every model, every single month.

**Idempotency:** each prediction is keyed on `(batch_id, ticker, model_name)`. Running the script twice in the same month skips already-saved predictions instead of duplicating them.

**Future-proofing:** the cron script discovers models via `models.get_available_models()`. Adding "Model 3" requires importing it into `models/__init__.py` and adding one line to `MODEL_BUILDERS`; no further changes are needed to keep the automation in sync.

A read-only inspection API exposes batch results:

| Endpoint | Returns |
|---|---|
| `GET /api/backtests` | One summary row per monthly batch (totals + per-model accuracy) |
| `GET /api/backtests/<batch_id>` | Per-model breakdown for a single batch (e.g. `recommendations_2026_05`) |

See `Documents/Technical-Reference.md` for full schema, function-level details, and `Documents/DEPLOYMENT.md` for the production cron entries.

---

### 4. Stock Screener — Likely Gainers & Likely Decliners

The recommendations page is split into two complementary tabs:

#### Likely Gainers (rule-based "value + quality" screener)

A rule-based screener that scores a curated universe of ~200+ large-cap US stocks on a 0–100 scale using:

- **Value signals:** distance from 52-week high/low, 1-month return, position relative to 200-day moving average
- **Fundamental quality:** profitability, free cash flow, revenue growth, debt-to-equity ratio, return on equity

Filters available: sector, industry, minimum market cap, profitable companies only. Results can be sorted by score, price, distance from high, or market cap.

#### Likely Decliners (Downside Risk Scanner)

A separate, mirror-image screener that scores stocks 0–100 on **downside risk** rather than upside potential. It runs a 3-layer pipeline:

- **Layer 1 — Quantitative downside score (0–100):** technical and model signals only — negative predicted 30-day return (Model 1), negative 1-month return, weak 10-day momentum, price below the 50-day or 200-day moving average, elevated annualised volatility, RSI weakness or overbought reversal risk, price near the 52-week low, and volume spikes during a price decline.
- **Layer 2 — Explanation layer:** every flagged stock includes beginner-friendly "Why Flagged" reasons (e.g. "Price is below the 50-day moving average", "Volatility is elevated").
- **Layer 3 — News sentiment context:** recent headlines are classified as positive / neutral / negative and surfaced as a green / yellow / red dot beside the score. News is **display-only** — it never multiplies or distorts the quantitative downside score.

Each result is mapped to a risk level: **Low (0–39), Medium (40–69), High (70–100)**. Wording is intentionally cautious throughout ("likely decliners", "downside risk", "stocks showing weakness") — this is a risk-analysis signal, not a guarantee.

To keep scans fast, Model 1 is only retrained for the top technical-score candidates, and predictions are cached in-process. A "Include Model 1 prediction" checkbox lets users skip the slower model pass entirely for a fast technical-only scan.

---

### 5. News Sentiment Analysis

Recent yfinance headlines are converted into a structured sentiment report used in two places:

- **Likely Gainers screener:** sentiment produces a bounded ±10 score adjustment on top of the base value score, plus theme flags (risk events, positive catalysts) shown in the detail overlay.
- **Likely Decliners scanner:** sentiment is shown as a contextual green / yellow / red dot beside each card and as a short news blurb in the details overlay — but it is never used to adjust the downside score.

Underlying mechanics (shared by both):

- Headlines are pulled from Yahoo Finance via yfinance
- Scored using a keyword-based positive/negative dictionary and recency decay weighting
- Near-duplicate headlines are detected and removed to avoid double-counting
- A final sentiment label (Positive / Negative / Neutral) is produced

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
| `/recommendations` | Tabbed screener: Likely Gainers (value + quality) and Likely Decliners (downside risk) |

---

## Project Philosophy

- **Local-first:** No deployment config, no auth, no cloud services — runs with `flask run`
- **Transparent scoring:** Every screener penalty and bonus is explained alongside the score
- **Modular:** Models, services, database, and routes are clearly separated for easy extension
- **Research-oriented:** Designed for learning and experimentation rather than production trading
