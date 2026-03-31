"""
Recommendation engine – transparent, rule-based stock screener.

Scores stocks on two axes:

  A. **Currently low** (0–50 pts)
     - Percent below 52-week high        (0–20 pts)
     - Closeness to 52-week low           (0–15 pts)
     - Negative recent 1-month return     (0–10 pts)
     - Below 200-day moving average       (0–5 pts)

  B. **Strong financials** (0–50 pts)
     - Positive net income                (0–10 pts)
     - Positive operating cash flow       (0–10 pts)
     - Positive free cash flow            (0–10 pts)
     - Revenue growth ≥ 0                 (0–8 pts)
     - Debt/equity ≤ 1.5                  (0–7 pts)
     - Positive return on equity          (0–5 pts)

Total score is 0–100.  Higher = more "beaten-down yet financially healthy".

All thresholds live in ``WEIGHT_CONFIG`` so they can be tuned in one place.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Optional

import yfinance as yf

import database as db
from services.stock_universe import get_candidates

logger = logging.getLogger(__name__)

MAX_WORKERS = 8

# ---------------------------------------------------------------------------
# Weight / threshold configuration  (single place to tune)
# ---------------------------------------------------------------------------

WEIGHT_CONFIG = {
    # --- "Currently low" signals (A) ---
    "pct_below_high_max_pts": 20,
    "pct_below_high_full":    0.40,   # 40 % below → full points

    "close_to_low_max_pts":   15,
    "close_to_low_full":      0.05,   # within 5 % of 52w low → full points

    "neg_return_1m_max_pts":  10,
    "neg_return_1m_full":     -0.20,  # −20 % monthly return → full points

    "below_ma200_pts":        5,

    # --- "Strong financials" signals (B) ---
    "positive_net_income_pts":   10,
    "positive_op_cashflow_pts":  10,
    "positive_free_cashflow_pts": 10,
    "revenue_growth_max_pts":     8,
    "revenue_growth_full":        0.15,  # 15 % growth → full points
    "debt_equity_max_pts":        7,
    "debt_equity_safe":           0.50,  # ≤ 0.5 → full points
    "debt_equity_max":            1.50,  # > 1.5 → 0 points
    "positive_roe_max_pts":       5,
    "roe_full":                   0.20,  # 20 % ROE → full points
}

CACHE_TTL_HOURS = 24


# ---------------------------------------------------------------------------
# Data fetching  (wraps yfinance, populates cache)
# ---------------------------------------------------------------------------

def _fetch_stock_data(ticker: str) -> dict[str, Any] | None:
    """Fetch price + fundamental data for *ticker* via yfinance.

    Returns a flat dict with the fields the scorer needs, or ``None`` when
    critical data is unavailable.  Results are cached in the DB so repeat
    page loads don't hammer the API.
    """
    cached = db.get_fundamentals_cache(ticker)
    if cached:
        age_hours = (
            datetime.utcnow() - datetime.fromisoformat(cached["last_updated"])
        ).total_seconds() / 3600
        if age_hours < CACHE_TTL_HOURS:
            return cached

    try:
        tk = yf.Ticker(ticker)
        info: dict = tk.info or {}
    except Exception:
        logger.warning("yfinance .info failed for %s", ticker)
        return None

    current_price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not current_price:
        return None

    week52_high = info.get("fiftyTwoWeekHigh")
    week52_low = info.get("fiftyTwoWeekLow")
    if not week52_high or not week52_low:
        return None

    ma200 = info.get("twoHundredDayAverage")

    # 1-month return: compare current price to the price ~21 trading days ago
    month_return = _calc_month_return(ticker, current_price)

    market_cap = info.get("marketCap")
    net_income = info.get("netIncomeToCommon")
    operating_cashflow = info.get("operatingCashflow")
    free_cashflow = info.get("freeCashflow")
    revenue_growth = info.get("revenueGrowth")
    debt_to_equity = info.get("debtToEquity")
    if debt_to_equity is not None:
        debt_to_equity = debt_to_equity / 100.0  # yfinance reports as percentage
    roe = info.get("returnOnEquity")
    company_name = info.get("shortName") or info.get("longName") or ""
    sector = info.get("sector", "")
    industry = info.get("industry", "")

    row = {
        "ticker": ticker,
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        "current_price": current_price,
        "week52_high": week52_high,
        "week52_low": week52_low,
        "ma200": ma200,
        "month_return": month_return,
        "market_cap": market_cap,
        "net_income": net_income,
        "operating_cashflow": operating_cashflow,
        "free_cashflow": free_cashflow,
        "revenue_growth": revenue_growth,
        "debt_to_equity": debt_to_equity,
        "roe": roe,
    }

    try:
        db.upsert_fundamentals_cache(row)
    except Exception:
        logger.warning("Cache write failed for %s", ticker)

    return row


def _calc_month_return(ticker: str, current_price: float) -> float | None:
    """Best-effort 1-month return using yfinance download."""
    try:
        end = date.today()
        start = end - timedelta(days=35)
        hist = yf.download(
            ticker, start=start.isoformat(), end=end.isoformat(), progress=False
        )
        if hist.empty or len(hist) < 2:
            return None
        first_close = float(hist["Close"].iloc[0])
        if first_close == 0:
            return None
        return (current_price - first_close) / first_close
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def compute_score(data: dict[str, Any]) -> tuple[float, list[str]]:
    """Return ``(score, reasons)`` for a single stock data dict.

    ``score`` is 0–100.  ``reasons`` is a list of human-readable explanation
    strings showing *why* the stock scored the way it did.
    """
    cfg = WEIGHT_CONFIG
    score = 0.0
    reasons: list[str] = []

    price = data["current_price"]
    high = data["week52_high"]
    low = data["week52_low"]

    # --- A: "Currently low" signals ---

    pct_below_high = (high - price) / high if high else 0
    pts = min(pct_below_high / cfg["pct_below_high_full"], 1.0) * cfg["pct_below_high_max_pts"]
    score += pts
    if pct_below_high > 0.05:
        reasons.append(f"{pct_below_high:.0%} below 52-week high")

    if high != low:
        range_position = (price - low) / (high - low)
        closeness = 1.0 - range_position
        pts = min(closeness / (1.0 - cfg["close_to_low_full"]), 1.0) * cfg["close_to_low_max_pts"]
        score += pts
        if range_position < 0.30:
            reasons.append(f"near 52-week low (bottom {range_position:.0%} of range)")

    month_ret = data.get("month_return")
    if month_ret is not None and month_ret < 0:
        ratio = min(abs(month_ret) / abs(cfg["neg_return_1m_full"]), 1.0)
        score += ratio * cfg["neg_return_1m_max_pts"]
        reasons.append(f"{month_ret:+.1%} return over past month")

    ma200 = data.get("ma200")
    if ma200 and price < ma200:
        score += cfg["below_ma200_pts"]
        reasons.append("trading below 200-day moving average")

    # --- B: "Strong financials" signals ---

    net_income = data.get("net_income")
    if net_income is not None and net_income > 0:
        score += cfg["positive_net_income_pts"]
        reasons.append("profitable (positive net income)")

    op_cf = data.get("operating_cashflow")
    if op_cf is not None and op_cf > 0:
        score += cfg["positive_op_cashflow_pts"]
        reasons.append("positive operating cash flow")

    fcf = data.get("free_cashflow")
    if fcf is not None and fcf > 0:
        score += cfg["positive_free_cashflow_pts"]
        reasons.append("positive free cash flow")

    rev_g = data.get("revenue_growth")
    if rev_g is not None:
        if rev_g > 0:
            ratio = min(rev_g / cfg["revenue_growth_full"], 1.0)
            score += ratio * cfg["revenue_growth_max_pts"]
            reasons.append(f"{rev_g:.1%} revenue growth")
        elif rev_g == 0:
            reasons.append("flat revenue growth")

    de = data.get("debt_to_equity")
    if de is not None:
        if de <= cfg["debt_equity_safe"]:
            score += cfg["debt_equity_max_pts"]
            reasons.append(f"low debt/equity ratio ({de:.2f})")
        elif de <= cfg["debt_equity_max"]:
            ratio = 1.0 - (de - cfg["debt_equity_safe"]) / (cfg["debt_equity_max"] - cfg["debt_equity_safe"])
            score += ratio * cfg["debt_equity_max_pts"]
            reasons.append(f"manageable debt/equity ratio ({de:.2f})")

    roe = data.get("roe")
    if roe is not None and roe > 0:
        ratio = min(roe / cfg["roe_full"], 1.0)
        score += ratio * cfg["positive_roe_max_pts"]
        reasons.append(f"{roe:.1%} return on equity")

    return round(score, 1), reasons


# ---------------------------------------------------------------------------
# Hard filters  (applied before scoring to remove bad candidates)
# ---------------------------------------------------------------------------

def apply_filters(
    data: dict[str, Any],
    *,
    min_market_cap: float = 0,
    profitable_only: bool = False,
    max_debt_equity: float | None = None,
    require_positive_cashflow: bool = False,
) -> bool:
    """Return True if *data* passes all hard filters."""
    cap = data.get("market_cap")
    if min_market_cap and (cap is None or cap < min_market_cap):
        return False

    if profitable_only:
        ni = data.get("net_income")
        if ni is None or ni <= 0:
            return False

    if max_debt_equity is not None:
        de = data.get("debt_to_equity")
        if de is not None and de > max_debt_equity:
            return False

    if require_positive_cashflow:
        cf = data.get("operating_cashflow")
        if cf is None or cf <= 0:
            return False

    return True


# ---------------------------------------------------------------------------
# Orchestration  (main entry point for the route)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# News  (on-demand, not part of the scoring pipeline)
# ---------------------------------------------------------------------------

def get_ticker_news(ticker: str, max_items: int = 8) -> list[dict[str, Any]]:
    """Fetch recent news headlines for *ticker* via yfinance.

    Returns a list of dicts with ``title``, ``publisher``, ``link``, and
    ``published`` (ISO timestamp string).  Returns an empty list on failure.
    """
    try:
        tk = yf.Ticker(ticker)
        raw = tk.news or []
    except Exception:
        logger.warning("News fetch failed for %s", ticker)
        return []

    items: list[dict[str, Any]] = []
    for article in raw[:max_items]:
        content = article.get("content", {})
        pub_date = content.get("pubDate", "")
        provider = content.get("provider", {})
        items.append({
            "title": content.get("title", article.get("title", "")),
            "publisher": provider.get("displayName", ""),
            "link": content.get("canonicalUrl", {}).get("url", article.get("link", "")),
            "published": pub_date,
        })
    return items


# ---------------------------------------------------------------------------
# Orchestration  (main entry point for the route)
# ---------------------------------------------------------------------------

SORT_OPTIONS = {
    "score":             lambda r: r["recommendation_score"],
    "price":             lambda r: r["current_price"],
    "pct_below_high":    lambda r: r.get("pct_below_high", 0),
    "market_cap":        lambda r: r.get("market_cap") or 0,
}


def get_recommendations(
    *,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    limit: int = 20,
    min_market_cap: float = 0,
    profitable_only: bool = False,
    max_debt_equity: float | None = None,
    require_positive_cashflow: bool = False,
    sort_by: str = "score",
) -> list[dict[str, Any]]:
    """Run the full recommendations pipeline and return ranked results.

    Each result dict contains:
    - ticker, company_name, sector, industry, current_price
    - week52_high, week52_low, pct_below_high
    - recommendation_score
    - reasons  (list of human-readable strings)
    - key financial metrics used in the decision
    """
    candidates = get_candidates(sector=sector, industry=industry)
    results: list[dict[str, Any]] = []

    # Fetch all tickers concurrently to avoid sequential API latency
    fetched: dict[str, dict[str, Any] | None] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {
            pool.submit(_fetch_stock_data, entry["ticker"]): entry
            for entry in candidates
        }
        for future in as_completed(futures):
            entry = futures[future]
            try:
                fetched[entry["ticker"]] = future.result()
            except Exception:
                logger.warning("Fetch failed for %s – skipping", entry["ticker"])
                fetched[entry["ticker"]] = None

    for entry in candidates:
        ticker = entry["ticker"]
        data = fetched.get(ticker)

        if data is None:
            logger.info("Incomplete data for %s – skipping", ticker)
            continue

        if not apply_filters(
            data,
            min_market_cap=min_market_cap,
            profitable_only=profitable_only,
            max_debt_equity=max_debt_equity,
            require_positive_cashflow=require_positive_cashflow,
        ):
            continue

        rec_score, reasons = compute_score(data)

        pct_below = (data["week52_high"] - data["current_price"]) / data["week52_high"] if data["week52_high"] else 0

        results.append({
            "ticker": ticker,
            "company_name": data.get("company_name", ""),
            "sector": data.get("sector") or entry.get("sector", ""),
            "industry": data.get("industry") or entry.get("industry", ""),
            "current_price": data["current_price"],
            "week52_high": data["week52_high"],
            "week52_low": data["week52_low"],
            "pct_below_high": round(pct_below, 4),
            "market_cap": data.get("market_cap"),
            "month_return": data.get("month_return"),
            "net_income": data.get("net_income"),
            "operating_cashflow": data.get("operating_cashflow"),
            "free_cashflow": data.get("free_cashflow"),
            "revenue_growth": data.get("revenue_growth"),
            "debt_to_equity": data.get("debt_to_equity"),
            "roe": data.get("roe"),
            "recommendation_score": rec_score,
            "reasons": reasons,
        })

    key_fn = SORT_OPTIONS.get(sort_by, SORT_OPTIONS["score"])
    reverse = True
    if sort_by == "price":
        reverse = False
    results.sort(key=key_fn, reverse=reverse)

    return results[:limit]
