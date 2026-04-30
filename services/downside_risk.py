"""
Downside Risk Scanner – identifies stocks showing weakness or downside risk.

This is the *mirror image* of the recommendations engine.  Where the
recommendation screener looks for stocks that are currently low **and**
fundamentally strong, this scanner looks for stocks that show technical
weakness, negative model forecasts, and (optionally) negative recent news.

3-Layer Architecture
--------------------

  **Layer 1 – Quantitative downside score (0–100)**
    Technical / model signals only.  Each signal contributes a bounded
    number of points; a stock can hit the cap from any combination.

      A. Negative predicted 30-day return  (Model 1)   0–25 pts
      B. Negative recent 1-month return                0–15 pts
      C. Negative 10-day momentum                      0–10 pts
      D. Below 50-day moving average                       8 pts
      E. Below 200-day moving average                      7 pts
      F. Elevated 20-day volatility                    0–10 pts
      G. RSI weakness OR overbought reversal risk      0–10 pts
      H. Near 52-week low (within 10 %)                0–10 pts
      I. Volume spike during a down day                    5 pts

    Total possible: 100 pts.

  **Layer 2 – Explanation layer**
    Human-readable "why flagged" reasons are appended whenever a signal
    fires (e.g. "Price is below the 50-day moving average").

  **Layer 3 – News sentiment context**
    Recent yfinance headlines are classified as positive / neutral /
    negative using the existing news_analysis module.  News is shown
    *beside* the score as a contextual signal.  It does NOT multiply or
    distort the quantitative downside score (per design).

Important behaviour
-------------------
  - Wording everywhere is "likely decliners" / "downside risk", never
    "guaranteed to drop".  This is a risk-analysis screener, not advice.
  - Tickers with missing or unusable data are skipped silently.
  - News fetch failures fall back to "neutral" sentiment.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from models.model_1 import (
    FEATURE_COLUMNS,
    PredictionError,
    add_features,
    add_rsi,
    build_prediction as build_prediction_m1,
)
from services.news_analysis import analyze_headlines
from services.recommendations import get_ticker_news
from services.stock_universe import get_candidates

logger = logging.getLogger(__name__)

MAX_WORKERS = 8

# Maximum points each signal can contribute.  Together they cap at 100.
WEIGHT_CONFIG = {
    # Model-driven
    "model_negative_return_max_pts": 25,
    "model_negative_return_full":   -0.10,   # -10 % predicted → full points

    # Recent price action
    "neg_month_return_max_pts": 15,
    "neg_month_return_full":   -0.20,        # -20 % monthly return → full

    "neg_momentum_max_pts": 10,
    "neg_momentum_full":   -0.10,            # -10 % over 10 days → full

    "below_ma50_pts":  8,
    "below_ma200_pts": 7,

    # Volatility & RSI
    "high_volatility_max_pts": 10,
    "high_volatility_full":    0.50,         # 50 % annualised vol → full

    "rsi_weakness_max_pts": 10,
    "rsi_oversold_threshold": 30.0,
    "rsi_overbought_threshold": 70.0,

    # Range / volume
    "near_52w_low_max_pts": 10,
    "near_52w_low_full":    0.10,            # within 10 % of low → full

    "volume_spike_pts":    5,
    "volume_spike_ratio":  1.5,              # today vol > 1.5x 10-day avg
}

# Risk-level thresholds (per spec)
RISK_LEVELS = [
    (70, "High"),
    (40, "Medium"),
    (0,  "Low"),
]

NEWS_CACHE_TTL_HOURS = 4

# Process-level cache for Model 1 predictions to avoid retraining.
# Keyed by ticker → (predicted_return, fetched_at_iso)
_MODEL_PRED_CACHE: dict[str, tuple[float, str]] = {}
MODEL_PRED_TTL_HOURS = 12


# ---------------------------------------------------------------------------
# Data fetching – price history + technical signals
# ---------------------------------------------------------------------------

def _download_price_history(ticker: str, days: int = 365) -> pd.DataFrame | None:
    """Download ``days`` of OHLCV data via yfinance.  Returns None on failure."""
    try:
        end = date.today() + timedelta(days=1)
        start = end - timedelta(days=days + 30)
        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
        )
    except Exception:
        return None

    if df is None or df.empty:
        return None

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    needed = ["Open", "High", "Low", "Close", "Volume"]
    if any(col not in df.columns for col in needed):
        return None

    return df[needed].dropna()


def _compute_signals(price_df: pd.DataFrame, info: dict) -> dict[str, Any] | None:
    """Compute all technical signals required for downside scoring.

    Returns a dict of derived metrics, or None if data is too sparse.
    """
    if price_df is None or len(price_df) < 60:
        return None

    df = add_rsi(add_features(price_df))
    latest = df.iloc[-1]

    close = float(latest["Close"])
    if not np.isfinite(close) or close <= 0:
        return None

    # MA50 already provided by add_features; MA200 is computed here.
    ma_50 = float(latest["ma_50"]) if pd.notna(latest.get("ma_50")) else None
    ma_200 = (
        float(df["Close"].rolling(200).mean().iloc[-1]) if len(df) >= 200 else None
    )

    # 1-month return: today vs ~21 trading days ago
    if len(df) >= 22:
        month_ago_close = float(df["Close"].iloc[-22])
        month_return = (close - month_ago_close) / month_ago_close if month_ago_close else None
    else:
        month_return = None

    # 10-day momentum return
    if len(df) >= 11:
        ten_ago = float(df["Close"].iloc[-11])
        momentum_10d = (close - ten_ago) / ten_ago if ten_ago else None
    else:
        momentum_10d = None

    # Annualised 20-day volatility (std of daily returns × sqrt(252))
    if "volatility_20" in df.columns and pd.notna(latest.get("volatility_20")):
        vol_annual = float(latest["volatility_20"]) * np.sqrt(252)
    else:
        vol_annual = None

    rsi = float(latest["rsi_14"]) if pd.notna(latest.get("rsi_14")) else None

    # 52-week range from yfinance .info (or fall back to historical max/min)
    week52_high = info.get("fiftyTwoWeekHigh")
    week52_low = info.get("fiftyTwoWeekLow")
    if not week52_high or not week52_low:
        if len(df) >= 252:
            week52_high = float(df["Close"].iloc[-252:].max())
            week52_low = float(df["Close"].iloc[-252:].min())
        else:
            week52_high = float(df["Close"].max())
            week52_low = float(df["Close"].min())

    # Volume spike on a down day: today's vol > 1.5x 10-day avg AND price down
    vol_ratio_10 = float(latest["vol_ratio_10"]) if pd.notna(latest.get("vol_ratio_10")) else None
    today_return = float(latest["ret_1"]) if pd.notna(latest.get("ret_1")) else None
    vol_spike_on_decline = (
        vol_ratio_10 is not None
        and today_return is not None
        and vol_ratio_10 >= WEIGHT_CONFIG["volume_spike_ratio"]
        and today_return < 0
    )

    market_cap = info.get("marketCap")
    company_name = info.get("shortName") or info.get("longName") or ""
    sector = info.get("sector", "")
    industry = info.get("industry", "")

    return {
        "current_price": close,
        "ma_50": ma_50,
        "ma_200": ma_200,
        "month_return": month_return,
        "momentum_10d": momentum_10d,
        "volatility_annual": vol_annual,
        "rsi": rsi,
        "week52_high": float(week52_high) if week52_high else None,
        "week52_low": float(week52_low) if week52_low else None,
        "volume_ratio_10": vol_ratio_10,
        "today_return": today_return,
        "vol_spike_on_decline": vol_spike_on_decline,
        "market_cap": market_cap,
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        # Carry the model-ready feature row for an optional Model 1 pass
        "_latest_features": latest[FEATURE_COLUMNS]
            if all(c in df.columns for c in FEATURE_COLUMNS)
            and df[FEATURE_COLUMNS].iloc[-1].notna().all()
            else None,
    }


def _fetch_ticker_data(ticker: str) -> dict[str, Any] | None:
    """Download price history + .info, then compute all technical signals.

    Returns None if any critical data is unavailable.
    """
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
    except Exception:
        info = {}

    price_df = _download_price_history(ticker, days=365)
    if price_df is None:
        return None

    signals = _compute_signals(price_df, info)
    if signals is None:
        return None

    signals["ticker"] = ticker
    signals["_price_df"] = price_df  # kept around for potential Model 1 reuse
    return signals


# ---------------------------------------------------------------------------
# Layer 1 – Quantitative downside scoring
# ---------------------------------------------------------------------------

def calculate_downside_risk_score(signals: dict[str, Any]) -> tuple[float, list[str]]:
    """Compute the 0–100 downside risk score plus a list of (signal_key, points)
    pairs that fired.  The reasons list is built separately by
    ``generate_downside_explanation`` for clean separation.

    Returns ``(score, fired_signals)`` where ``fired_signals`` is a list of
    ``(signal_key, points_contributed)`` tuples used by the explanation layer.
    """
    cfg = WEIGHT_CONFIG
    score = 0.0
    fired: list[tuple[str, float]] = []

    # A. Model-predicted negative 30-day return
    pred = signals.get("predicted_return")
    if pred is not None and pred < 0:
        ratio = min(abs(pred) / abs(cfg["model_negative_return_full"]), 1.0)
        pts = ratio * cfg["model_negative_return_max_pts"]
        score += pts
        fired.append(("model_negative_return", pts))

    # B. Negative 1-month return
    mret = signals.get("month_return")
    if mret is not None and mret < 0:
        ratio = min(abs(mret) / abs(cfg["neg_month_return_full"]), 1.0)
        pts = ratio * cfg["neg_month_return_max_pts"]
        score += pts
        fired.append(("neg_month_return", pts))

    # C. Negative 10-day momentum
    mom = signals.get("momentum_10d")
    if mom is not None and mom < 0:
        ratio = min(abs(mom) / abs(cfg["neg_momentum_full"]), 1.0)
        pts = ratio * cfg["neg_momentum_max_pts"]
        score += pts
        fired.append(("neg_momentum", pts))

    # D. Below 50-day MA
    price = signals.get("current_price")
    ma50 = signals.get("ma_50")
    if price and ma50 and price < ma50:
        score += cfg["below_ma50_pts"]
        fired.append(("below_ma50", cfg["below_ma50_pts"]))

    # E. Below 200-day MA
    ma200 = signals.get("ma_200")
    if price and ma200 and price < ma200:
        score += cfg["below_ma200_pts"]
        fired.append(("below_ma200", cfg["below_ma200_pts"]))

    # F. Elevated annualised volatility
    vol = signals.get("volatility_annual")
    if vol is not None and vol > 0.20:  # only flag if reasonably elevated
        ratio = min(vol / cfg["high_volatility_full"], 1.0)
        pts = ratio * cfg["high_volatility_max_pts"]
        score += pts
        fired.append(("high_volatility", pts))

    # G. RSI: weakness OR overbought reversal risk
    rsi = signals.get("rsi")
    if rsi is not None:
        if rsi < cfg["rsi_oversold_threshold"]:
            # weakness: deeper below 30 = more risk of further selling
            severity = (cfg["rsi_oversold_threshold"] - rsi) / cfg["rsi_oversold_threshold"]
            pts = min(severity, 1.0) * cfg["rsi_weakness_max_pts"]
            score += pts
            fired.append(("rsi_oversold", pts))
        elif rsi > cfg["rsi_overbought_threshold"]:
            # overbought: deeper above 70 = bigger reversal risk
            severity = (rsi - cfg["rsi_overbought_threshold"]) / (100 - cfg["rsi_overbought_threshold"])
            pts = min(severity, 1.0) * cfg["rsi_weakness_max_pts"]
            score += pts
            fired.append(("rsi_overbought", pts))

    # H. Near 52-week low
    high = signals.get("week52_high")
    low = signals.get("week52_low")
    if price and high and low and high > low:
        # closeness to low: 1.0 if price == low, 0.0 if price == high
        closeness_to_low = (high - price) / (high - low)
        if closeness_to_low > 0.7:  # within bottom 30 % of range
            ratio = min((closeness_to_low - 0.7) / 0.3, 1.0)
            pts = ratio * cfg["near_52w_low_max_pts"]
            score += pts
            fired.append(("near_52w_low", pts))

    # I. Volume spike on a down day
    if signals.get("vol_spike_on_decline"):
        score += cfg["volume_spike_pts"]
        fired.append(("vol_spike_on_decline", cfg["volume_spike_pts"]))

    # Cap score at 100 (sum of all max points is exactly 100; this is just safety)
    score = max(0.0, min(100.0, score))
    return round(score, 1), fired


def _risk_level(score: float) -> str:
    """Map a 0–100 score to Low / Medium / High."""
    for threshold, label in RISK_LEVELS:
        if score >= threshold:
            return label
    return "Low"


# ---------------------------------------------------------------------------
# Layer 2 – Human-readable explanations
# ---------------------------------------------------------------------------

def generate_downside_explanation(
    signals: dict[str, Any],
    fired_signals: list[tuple[str, float]],
) -> list[str]:
    """Convert fired signals into beginner-friendly explanation strings."""
    reasons: list[str] = []

    # Build a quick lookup from (signal_key) -> True
    fired_keys = {key for key, _ in fired_signals}

    if "model_negative_return" in fired_keys:
        pred = signals.get("predicted_return") or 0
        reasons.append(
            f"Model predicts a negative 30-day return ({pred * 100:+.1f}%)."
        )

    if "neg_month_return" in fired_keys:
        mret = signals.get("month_return") or 0
        reasons.append(
            f"Recent 1-month return is negative ({mret * 100:+.1f}%)."
        )

    if "neg_momentum" in fired_keys:
        mom = signals.get("momentum_10d") or 0
        reasons.append(
            f"Short-term momentum is weak ({mom * 100:+.1f}% over 10 days)."
        )

    if "below_ma50" in fired_keys:
        reasons.append("Price is below the 50-day moving average.")

    if "below_ma200" in fired_keys:
        reasons.append("Price is below the 200-day moving average.")

    if "high_volatility" in fired_keys:
        vol = signals.get("volatility_annual") or 0
        reasons.append(
            f"Volatility is elevated ({vol * 100:.0f}% annualised)."
        )

    if "rsi_oversold" in fired_keys:
        rsi = signals.get("rsi") or 0
        reasons.append(
            f"RSI is weak ({rsi:.0f}), suggesting continued selling pressure."
        )

    if "rsi_overbought" in fired_keys:
        rsi = signals.get("rsi") or 0
        reasons.append(
            f"RSI is overbought ({rsi:.0f}), raising the risk of a pullback."
        )

    if "near_52w_low" in fired_keys:
        reasons.append("Price is near its 52-week low.")

    if "vol_spike_on_decline" in fired_keys:
        reasons.append(
            "Recent trading volume increased during a price decline."
        )

    if not reasons:
        reasons.append("No strong downside signals detected.")

    return reasons


# ---------------------------------------------------------------------------
# Layer 3 – News sentiment context (display-only, no score impact)
# ---------------------------------------------------------------------------

# Cache (ticker -> (analysis_dict, fetched_at_iso))
_NEWS_SENTIMENT_CACHE: dict[str, tuple[dict[str, Any], str]] = {}


def get_stock_news(ticker: str, max_items: int = 8) -> list[dict[str, Any]]:
    """Fetch recent news for *ticker* with a relative-time string for display.

    Reuses the existing yfinance-backed news fetcher from the recommendations
    module to keep behaviour consistent with the existing details overlay.
    """
    raw = get_ticker_news(ticker, max_items=max_items)
    out: list[dict[str, Any]] = []
    for item in raw:
        out.append({
            "title": item.get("title", ""),
            "publisher": item.get("publisher", ""),
            "link": item.get("link", ""),
            "published": item.get("published", ""),
            "relative_time": _relative_time(item.get("published", "")),
        })
    return out


def _relative_time(iso_string: str) -> str:
    """Convert an ISO timestamp to a 'Xh ago' / 'Xd ago' style string."""
    if not iso_string:
        return ""
    try:
        dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{int(seconds // 60)}m ago"
        if seconds < 86400:
            return f"{int(seconds // 3600)}h ago"
        days = int(seconds // 86400)
        if days == 1:
            return "1d ago"
        if days < 30:
            return f"{days}d ago"
        return dt.strftime("%b %d")
    except (ValueError, TypeError):
        return ""


def classify_news_sentiment(ticker: str) -> dict[str, Any]:
    """Classify the recent news tone for *ticker* as positive / neutral / negative.

    Note: the result is **purely contextual** – it is shown beside the score
    but never multiplies or distorts the quantitative downside score.

    Returns a dict with:
      - sentiment: "positive" | "neutral" | "negative"
      - color:    "green"     | "yellow"  | "red"
      - summary:  short human-readable summary string
      - headline_count: number of headlines analysed
    """
    cached = _NEWS_SENTIMENT_CACHE.get(ticker)
    if cached:
        analysis, fetched_at = cached
        try:
            age = datetime.utcnow() - datetime.fromisoformat(fetched_at)
            if age < timedelta(hours=NEWS_CACHE_TTL_HOURS):
                return analysis
        except ValueError:
            pass

    try:
        headlines = get_ticker_news(ticker)
        analysis = analyze_headlines(headlines)
    except Exception:
        logger.warning("News sentiment fetch failed for %s", ticker)
        analysis = analyze_headlines([])

    label = analysis.get("sentiment_label", "neutral")
    color = analysis.get("sentiment_icon_color", "yellow")
    if label == "positive":
        color = "green"
    elif label == "negative":
        color = "red"
    else:
        color = "yellow"

    result = {
        "sentiment": label,
        "color": color,
        "summary": analysis.get("summary", ""),
        "headline_count": analysis.get("headline_count", 0),
    }
    _NEWS_SENTIMENT_CACHE[ticker] = (result, datetime.utcnow().isoformat())
    return result


# ---------------------------------------------------------------------------
# Model 1 prediction (shared)
# ---------------------------------------------------------------------------

def _model1_predicted_return(ticker: str) -> float | None:
    """Run Model 1 to get the predicted 30-day return for *ticker*.

    Caches results in-process for ``MODEL_PRED_TTL_HOURS`` to avoid
    retraining XGBoost on every scan.  Returns None on any failure so the
    caller can degrade gracefully.
    """
    cached = _MODEL_PRED_CACHE.get(ticker)
    if cached:
        pred, fetched_at = cached
        try:
            age = datetime.utcnow() - datetime.fromisoformat(fetched_at)
            if age < timedelta(hours=MODEL_PRED_TTL_HOURS):
                return pred
        except ValueError:
            pass

    try:
        result = build_prediction_m1(ticker)
        pred = float(result.get("predicted_return"))
    except (PredictionError, Exception):
        logger.info("Model 1 prediction unavailable for %s", ticker)
        return None

    _MODEL_PRED_CACHE[ticker] = (pred, datetime.utcnow().isoformat())
    return pred


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def get_downside_risk_stocks(
    *,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    limit: int = 20,
    min_market_cap: float = 0,
    use_model: bool = True,
    model_top_n: int = 30,
) -> list[dict[str, Any]]:
    """Run the full downside-risk pipeline and return ranked decliners.

    Parameters
    ----------
    sector, industry : optional filters from the candidate universe.
    limit : max number of results to return.
    min_market_cap : exclude stocks with market cap below this value.
    use_model : if True, run Model 1 on the top ``model_top_n`` candidates
        (by technical-only score) to refine ranking.  This is the slow part
        of the scan; results are cached for ``MODEL_PRED_TTL_HOURS`` hours.
    model_top_n : how many top technical-score candidates to send through
        Model 1.  Smaller = faster scan, larger = more accurate ranking.

    Each result dict matches the spec format:
        ticker, company_name, predicted_return, downside_score, risk_level,
        news_sentiment, news_sentiment_color, why_flagged, news, ...
    """
    candidates = get_candidates(sector=sector, industry=industry)

    # ---- Stage 1: Fetch + technical signals (parallel) -------------------
    enriched: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_ticker_data, c["ticker"]): c
            for c in candidates
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                data = future.result()
            except Exception:
                logger.warning("Fetch failed for %s – skipping", entry["ticker"])
                continue
            if data is None:
                continue
            # Apply hard filters
            cap = data.get("market_cap")
            if min_market_cap and (cap is None or cap < min_market_cap):
                continue
            # Fall back to universe metadata if .info was incomplete
            data["sector"] = data.get("sector") or entry.get("sector", "")
            data["industry"] = data.get("industry") or entry.get("industry", "")
            enriched.append(data)

    # ---- Stage 2: Compute technical-only score, rank, take top N ---------
    for d in enriched:
        score, fired = calculate_downside_risk_score(d)
        d["_technical_score"] = score
        d["_fired"] = fired

    enriched.sort(key=lambda x: x["_technical_score"], reverse=True)
    top_candidates = enriched[: max(model_top_n, limit * 2)] if use_model else enriched

    # ---- Stage 3: Run Model 1 on top candidates (parallel, cached) -------
    if use_model and top_candidates:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(_model1_predicted_return, d["ticker"]): d
                for d in top_candidates
            }
            for future in as_completed(futures):
                d = futures[future]
                try:
                    pred = future.result()
                except Exception:
                    pred = None
                d["predicted_return"] = pred

    # ---- Stage 4: Recompute final score now that predicted_return is set --
    final: list[dict[str, Any]] = []
    pool_for_scoring = top_candidates if use_model else enriched
    for d in pool_for_scoring:
        score, fired = calculate_downside_risk_score(d)
        reasons = generate_downside_explanation(d, fired)
        d["downside_score"] = score
        d["risk_level"] = _risk_level(score)
        d["why_flagged"] = reasons
        final.append(d)

    # ---- Stage 5: Layer 3 – News sentiment context (parallel) ------------
    final = final[: limit * 2]  # only fetch news for plausible results
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(classify_news_sentiment, d["ticker"]): d for d in final}
        for future in as_completed(futures):
            d = futures[future]
            try:
                sentiment = future.result()
            except Exception:
                sentiment = {"sentiment": "neutral", "color": "yellow",
                             "summary": "", "headline_count": 0}
            d["news_sentiment"] = sentiment["sentiment"]
            d["news_sentiment_color"] = sentiment["color"]
            d["news_summary"] = sentiment["summary"]
            d["news_headline_count"] = sentiment["headline_count"]

    # ---- Stage 6: Final sort by downside score (descending) ---------------
    final.sort(key=lambda x: x["downside_score"], reverse=True)

    # ---- Stage 7: Shape the output (drop internal fields) -----------------
    output: list[dict[str, Any]] = []
    for d in final[:limit]:
        output.append({
            "ticker": d["ticker"],
            "company_name": d.get("company_name", ""),
            "sector": d.get("sector", ""),
            "industry": d.get("industry", ""),
            "current_price": d.get("current_price"),
            "predicted_return": (
                round(d["predicted_return"] * 100, 2)
                if d.get("predicted_return") is not None
                else None
            ),
            "downside_score": d["downside_score"],
            "risk_level": d["risk_level"],
            "news_sentiment": d.get("news_sentiment", "neutral"),
            "news_sentiment_color": d.get("news_sentiment_color", "yellow"),
            "news_summary": d.get("news_summary", ""),
            "news_headline_count": d.get("news_headline_count", 0),
            "why_flagged": d["why_flagged"],
            # Extra context fields that the details overlay can use
            "month_return": d.get("month_return"),
            "momentum_10d": d.get("momentum_10d"),
            "volatility_annual": d.get("volatility_annual"),
            "rsi": d.get("rsi"),
            "ma_50": d.get("ma_50"),
            "ma_200": d.get("ma_200"),
            "week52_high": d.get("week52_high"),
            "week52_low": d.get("week52_low"),
            "market_cap": d.get("market_cap"),
        })

    return output
