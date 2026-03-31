"""
Tests for the recommendations feature.

Covers:
- scoring logic with deterministic data
- hard-filter behaviour
- result sorting
- explanation strings
- empty-state handling
- route 200 responses
- template key fields
"""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import patch

import pytest

# Ensure project root is on the path so imports work when running from tests/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import database as db
from services.news_analysis import (
    NEWS_ADJUSTMENT_BOUNDS,
    analyze_headlines,
    compute_final_score,
    compute_news_adjustment,
    get_final_stance,
    score_headline,
)
from services.recommendations import (
    WEIGHT_CONFIG,
    apply_filters,
    compute_score,
    get_recommendations,
)
from services.stock_universe import get_candidates, get_industries, get_sectors


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _use_temp_db(tmp_path, monkeypatch):
    """Point the database at a temporary file for every test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    db.init_db()


@pytest.fixture(autouse=True)
def _skip_news_enrichment(monkeypatch):
    """Prevent real news API calls in tests by replacing enrichment with
    a no-op that applies neutral defaults.  Tests that need real enrichment
    behaviour can override ``_enrich_with_news`` explicitly."""

    def _noop(results):
        for r in results:
            r.setdefault("base_score", r.get("recommendation_score", 0))
            r.setdefault("sentiment_label", "neutral")
            r.setdefault("sentiment_icon_color", "yellow")
            r.setdefault("news_adjustment", 0.0)
            r.setdefault("news_summary", "")
            r.setdefault("news_headline_count", 0)
            r.setdefault("risk_flags", [])
            r.setdefault("positive_catalysts", [])
            r.setdefault("final_stance", "")

    monkeypatch.setattr("services.recommendations._enrich_with_news", _noop)


@pytest.fixture()
def client():
    """Flask test client."""
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _make_stock(**overrides) -> dict:
    """Build a stock-data dict with sensible defaults, easy to customise."""
    base = {
        "ticker": "TEST",
        "company_name": "Test Corp",
        "sector": "Technology",
        "industry": "Semiconductors",
        "current_price": 80.0,
        "week52_high": 120.0,
        "week52_low": 60.0,
        "ma200": 100.0,
        "month_return": -0.10,
        "market_cap": 50_000_000_000,
        "net_income": 2_000_000_000,
        "operating_cashflow": 3_000_000_000,
        "free_cashflow": 1_500_000_000,
        "revenue_growth": 0.08,
        "debt_to_equity": 0.40,
        "roe": 0.15,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Stock universe tests
# ---------------------------------------------------------------------------

class TestStockUniverse:
    def test_get_sectors_returns_sorted_strings(self):
        sectors = get_sectors()
        assert isinstance(sectors, list)
        assert len(sectors) > 0
        assert sectors == sorted(sectors)

    def test_get_industries_returns_list(self):
        industries = get_industries()
        assert isinstance(industries, list)
        assert len(industries) > 0

    def test_get_industries_filters_by_sector(self):
        industries_all = get_industries()
        industries_tech = get_industries("Technology")
        assert len(industries_tech) < len(industries_all)

    def test_get_candidates_all(self):
        all_candidates = get_candidates()
        assert len(all_candidates) > 100

    def test_get_candidates_filters_sector(self):
        tech = get_candidates(sector="Technology")
        assert all(c["sector"] == "Technology" for c in tech)
        assert len(tech) > 0

    def test_get_candidates_filters_industry(self):
        semis = get_candidates(industry="Semiconductors")
        assert all(c["industry"] == "Semiconductors" for c in semis)
        assert len(semis) > 0


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------

class TestScoring:
    def test_score_range_is_0_to_100(self):
        stock = _make_stock()
        score, _ = compute_score(stock)
        assert 0 <= score <= 100

    def test_beaten_down_stock_scores_higher_on_price_signals(self):
        strong_dip = _make_stock(current_price=65.0, month_return=-0.25)
        mild_dip = _make_stock(current_price=110.0, month_return=-0.02)
        score_strong, _ = compute_score(strong_dip)
        score_mild, _ = compute_score(mild_dip)
        assert score_strong > score_mild

    def test_healthy_financials_add_points(self):
        healthy = _make_stock()
        unhealthy = _make_stock(
            net_income=-500_000_000,
            operating_cashflow=-100_000_000,
            free_cashflow=-200_000_000,
            revenue_growth=-0.10,
            debt_to_equity=3.0,
            roe=-0.05,
        )
        score_healthy, _ = compute_score(healthy)
        score_unhealthy, _ = compute_score(unhealthy)
        assert score_healthy > score_unhealthy

    def test_below_ma200_adds_points(self):
        below = _make_stock(current_price=90.0, ma200=100.0)
        above = _make_stock(current_price=110.0, ma200=100.0)
        score_below, _ = compute_score(below)
        score_above, _ = compute_score(above)
        assert score_below > score_above

    def test_zero_high_does_not_crash(self):
        stock = _make_stock(week52_high=0, week52_low=0, current_price=50)
        score, reasons = compute_score(stock)
        assert isinstance(score, float)

    def test_missing_optionals_still_scores(self):
        stock = _make_stock(
            ma200=None, month_return=None, net_income=None,
            operating_cashflow=None, free_cashflow=None,
            revenue_growth=None, debt_to_equity=None, roe=None,
        )
        score, reasons = compute_score(stock)
        assert score >= 0

    def test_max_score_all_signals_positive(self):
        stock = _make_stock(
            current_price=62.0,
            week52_high=120.0,
            week52_low=60.0,
            ma200=100.0,
            month_return=-0.25,
            net_income=5e9,
            operating_cashflow=6e9,
            free_cashflow=4e9,
            revenue_growth=0.20,
            debt_to_equity=0.30,
            roe=0.25,
        )
        score, _ = compute_score(stock)
        assert score >= 70


# ---------------------------------------------------------------------------
# Explanation-string tests
# ---------------------------------------------------------------------------

class TestExplanations:
    def test_reasons_are_produced(self):
        stock = _make_stock()
        _, reasons = compute_score(stock)
        assert len(reasons) > 0
        assert all(isinstance(r, str) for r in reasons)

    def test_below_52w_high_reason(self):
        stock = _make_stock(current_price=80, week52_high=120)
        _, reasons = compute_score(stock)
        assert any("52-week high" in r for r in reasons)

    def test_profitable_reason(self):
        stock = _make_stock(net_income=1e9)
        _, reasons = compute_score(stock)
        assert any("profitable" in r.lower() for r in reasons)

    def test_positive_fcf_reason(self):
        stock = _make_stock(free_cashflow=5e8)
        _, reasons = compute_score(stock)
        assert any("free cash flow" in r.lower() for r in reasons)

    def test_debt_equity_reason(self):
        stock = _make_stock(debt_to_equity=0.30)
        _, reasons = compute_score(stock)
        assert any("debt" in r.lower() for r in reasons)

    def test_no_below_high_reason_for_near_high(self):
        stock = _make_stock(current_price=119, week52_high=120)
        _, reasons = compute_score(stock)
        assert not any("52-week high" in r for r in reasons)


# ---------------------------------------------------------------------------
# Hard-filter tests
# ---------------------------------------------------------------------------

class TestFilters:
    def test_passes_when_all_conditions_met(self):
        stock = _make_stock()
        assert apply_filters(stock, min_market_cap=1e9, profitable_only=True) is True

    def test_fails_below_min_market_cap(self):
        stock = _make_stock(market_cap=500_000_000)
        assert apply_filters(stock, min_market_cap=1e9) is False

    def test_fails_missing_market_cap_when_required(self):
        stock = _make_stock(market_cap=None)
        assert apply_filters(stock, min_market_cap=1e9) is False

    def test_passes_zero_min_cap_with_missing(self):
        stock = _make_stock(market_cap=None)
        assert apply_filters(stock, min_market_cap=0) is True

    def test_profitable_only_filters_losses(self):
        stock = _make_stock(net_income=-100_000)
        assert apply_filters(stock, profitable_only=True) is False

    def test_profitable_only_passes_profit(self):
        stock = _make_stock(net_income=100_000)
        assert apply_filters(stock, profitable_only=True) is True

    def test_max_debt_equity_filters_high_debt(self):
        stock = _make_stock(debt_to_equity=2.5)
        assert apply_filters(stock, max_debt_equity=2.0) is False

    def test_max_debt_equity_passes_low_debt(self):
        stock = _make_stock(debt_to_equity=0.5)
        assert apply_filters(stock, max_debt_equity=2.0) is True

    def test_require_positive_cashflow(self):
        negative = _make_stock(operating_cashflow=-1e6)
        positive = _make_stock(operating_cashflow=1e6)
        assert apply_filters(negative, require_positive_cashflow=True) is False
        assert apply_filters(positive, require_positive_cashflow=True) is True


# ---------------------------------------------------------------------------
# Sorting tests
# ---------------------------------------------------------------------------

class TestSorting:
    @patch("services.recommendations._fetch_stock_data")
    def test_default_sort_is_score_descending(self, mock_fetch):
        stocks = [
            _make_stock(ticker="LOW", current_price=65),
            _make_stock(ticker="HIGH", current_price=115),
        ]
        mock_fetch.side_effect = lambda t: next((s for s in stocks if s["ticker"] == t), None)

        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": "LOW", "sector": "Technology", "industry": "Semiconductors"},
                {"ticker": "HIGH", "sector": "Technology", "industry": "Semiconductors"},
            ]
            results = get_recommendations(sector="Technology", sort_by="score")

        scores = [r["recommendation_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @patch("services.recommendations._fetch_stock_data")
    def test_sort_by_price_ascending(self, mock_fetch):
        stocks = [
            _make_stock(ticker="CHEAP", current_price=20),
            _make_stock(ticker="PRICEY", current_price=200),
        ]
        mock_fetch.side_effect = lambda t: next((s for s in stocks if s["ticker"] == t), None)

        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": "CHEAP", "sector": "Technology", "industry": "Semiconductors"},
                {"ticker": "PRICEY", "sector": "Technology", "industry": "Semiconductors"},
            ]
            results = get_recommendations(sector="Technology", sort_by="price")

        prices = [r["current_price"] for r in results]
        assert prices == sorted(prices)


# ---------------------------------------------------------------------------
# Orchestration tests (mocked data)
# ---------------------------------------------------------------------------

class TestOrchestration:
    @patch("services.recommendations._fetch_stock_data")
    def test_empty_when_no_candidates_pass_filters(self, mock_fetch):
        mock_fetch.return_value = _make_stock(market_cap=100_000)
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [{"ticker": "X", "sector": "Technology", "industry": "Semiconductors"}]
            results = get_recommendations(min_market_cap=1e12)
        assert results == []

    @patch("services.recommendations._fetch_stock_data")
    def test_skips_ticker_when_fetch_returns_none(self, mock_fetch):
        mock_fetch.return_value = None
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [{"ticker": "BAD", "sector": "Technology", "industry": "Semiconductors"}]
            results = get_recommendations()
        assert results == []

    @patch("services.recommendations._fetch_stock_data")
    def test_results_contain_expected_fields(self, mock_fetch):
        mock_fetch.return_value = _make_stock()
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [{"ticker": "TEST", "sector": "Technology", "industry": "Semiconductors"}]
            results = get_recommendations()

        assert len(results) == 1
        r = results[0]
        assert "ticker" in r
        assert "company_name" in r
        assert "recommendation_score" in r
        assert "reasons" in r
        assert "current_price" in r
        assert "week52_high" in r
        assert "pct_below_high" in r

    @patch("services.recommendations._fetch_stock_data")
    def test_limit_parameter(self, mock_fetch):
        def fake_fetch(ticker):
            return _make_stock(ticker=ticker)

        mock_fetch.side_effect = fake_fetch
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": f"T{i}", "sector": "Technology", "industry": "Semiconductors"}
                for i in range(10)
            ]
            results = get_recommendations(limit=3)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestRoutes:
    def test_recommendations_page_returns_200(self, client):
        resp = client.get("/recommendations")
        assert resp.status_code == 200

    def test_recommendations_page_contains_key_elements(self, client):
        resp = client.get("/recommendations")
        html = resp.data.decode()
        assert "Stock Screener" in html
        assert "rec-sector" in html
        assert "rec-industry" in html
        assert "Find Recommendations" in html

    def test_industries_api_returns_200(self, client):
        resp = client.get("/api/industries")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)

    def test_industries_api_filters_by_sector(self, client):
        resp = client.get("/api/industries?sector=Technology")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) > 0

    @patch("services.recommendations._fetch_stock_data")
    def test_recommendations_api_returns_200(self, mock_fetch, client):
        mock_fetch.return_value = _make_stock()
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [{"ticker": "TEST", "sector": "Technology", "industry": "Semiconductors"}]
            resp = client.get("/api/recommendations?sector=Technology&limit=5")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)

    @patch("services.recommendations._fetch_stock_data")
    def test_recommendations_api_empty_state(self, mock_fetch, client):
        mock_fetch.return_value = None
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [{"ticker": "BAD", "sector": "Technology", "industry": "Semiconductors"}]
            resp = client.get("/api/recommendations")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []

    @patch("app.get_ticker_news")
    def test_news_api_returns_200(self, mock_news, client):
        mock_news.return_value = [
            {"title": "Test headline", "publisher": "Reuters", "link": "https://example.com", "published": "2026-03-30T10:00:00Z"},
        ]
        resp = client.get("/api/news/AAPL")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert isinstance(data, list)
        assert data[0]["title"] == "Test headline"

    @patch("app.get_ticker_news")
    def test_news_api_empty(self, mock_news, client):
        mock_news.return_value = []
        resp = client.get("/api/news/AAPL")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []


# ---------------------------------------------------------------------------
# News function tests
# ---------------------------------------------------------------------------

class TestNews:
    @patch("services.recommendations.yf.Ticker")
    def test_get_ticker_news_returns_list(self, mock_ticker_cls):
        from services.recommendations import get_ticker_news
        mock_tk = mock_ticker_cls.return_value
        mock_tk.news = [
            {"content": {"title": "Big Deal", "provider": {"displayName": "CNBC"}, "canonicalUrl": {"url": "https://cnbc.com/1"}, "pubDate": "2026-03-30T08:00:00Z"}},
            {"content": {"title": "Earnings", "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "https://reuters.com/2"}, "pubDate": "2026-03-29T12:00:00Z"}},
        ]
        result = get_ticker_news("AAPL")
        assert len(result) == 2
        assert result[0]["title"] == "Big Deal"
        assert result[0]["publisher"] == "CNBC"

    @patch("services.recommendations.yf.Ticker")
    def test_get_ticker_news_empty_on_failure(self, mock_ticker_cls):
        from services.recommendations import get_ticker_news
        mock_ticker_cls.side_effect = Exception("API down")
        result = get_ticker_news("AAPL")
        assert result == []


# ---------------------------------------------------------------------------
# Database cache tests
# ---------------------------------------------------------------------------

class TestFundamentalsCache:
    def test_upsert_and_retrieve(self):
        data = _make_stock(ticker="CTEST")
        db.upsert_fundamentals_cache(data)
        cached = db.get_fundamentals_cache("CTEST")
        assert cached is not None
        assert cached["ticker"] == "CTEST"
        assert cached["current_price"] == 80.0

    def test_cache_returns_none_for_unknown(self):
        assert db.get_fundamentals_cache("ZZZZZ") is None

    def test_upsert_overwrites(self):
        data = _make_stock(ticker="UPD", current_price=50.0)
        db.upsert_fundamentals_cache(data)
        data["current_price"] = 55.0
        db.upsert_fundamentals_cache(data)
        cached = db.get_fundamentals_cache("UPD")
        assert cached["current_price"] == 55.0


# ---------------------------------------------------------------------------
# Sentiment classification tests
# ---------------------------------------------------------------------------

class TestSentimentClassification:
    def test_positive_headlines(self):
        headlines = [
            {"title": "Company beats expectations with record earnings"},
            {"title": "Analyst upgraded stock to buy rating"},
        ]
        result = analyze_headlines(headlines)
        assert result["sentiment_label"] == "positive"
        assert result["sentiment_icon_color"] == "green"

    def test_negative_headlines(self):
        headlines = [
            {"title": "Company missed expectations, profit warning issued"},
            {"title": "Stock downgraded after earnings miss"},
        ]
        result = analyze_headlines(headlines)
        assert result["sentiment_label"] == "negative"
        assert result["sentiment_icon_color"] == "red"

    def test_neutral_headlines(self):
        headlines = [
            {"title": "Company plans to host investor day next quarter"},
            {"title": "Annual meeting scheduled for next month"},
        ]
        result = analyze_headlines(headlines)
        assert result["sentiment_label"] == "neutral"
        assert result["sentiment_icon_color"] == "yellow"

    def test_empty_headlines_returns_neutral(self):
        result = analyze_headlines([])
        assert result["sentiment_label"] == "neutral"
        assert result["sentiment_icon_color"] == "yellow"
        assert result["news_adjustment"] == 0.0
        assert result["headline_count"] == 0

    def test_mixed_headlines_classified(self):
        headlines = [
            {"title": "Strong earnings beat expectations"},
            {"title": "Lawsuit filed against company"},
        ]
        result = analyze_headlines(headlines)
        assert result["sentiment_label"] in ("positive", "neutral", "negative")
        assert result["positive_count"] >= 1
        assert result["negative_count"] >= 1

    def test_near_duplicate_headlines_deduplicated(self):
        headlines = [
            {"title": "Company beats Q4 earnings expectations"},
            {"title": "Company beats Q4 earnings expectations today"},
        ]
        result = analyze_headlines(headlines)
        assert result["headline_count"] <= 2

    def test_headline_counts_correct(self):
        headlines = [
            {"title": "Earnings beat expectations"},
            {"title": "Annual shareholder meeting held"},
            {"title": "Stock downgraded by analyst"},
        ]
        result = analyze_headlines(headlines)
        total = result["positive_count"] + result["negative_count"] + result["neutral_count"]
        assert total == result["headline_count"]


# ---------------------------------------------------------------------------
# Headline scoring tests
# ---------------------------------------------------------------------------

class TestHeadlineScoring:
    def test_positive_keyword_scores_positive(self):
        assert score_headline("Company beats expectations") > 0

    def test_negative_keyword_scores_negative(self):
        assert score_headline("Stock downgraded by analysts") < 0

    def test_neutral_headline_scores_zero(self):
        assert score_headline("Company announces board meeting") == 0.0

    def test_empty_string_scores_zero(self):
        assert score_headline("") == 0.0

    def test_case_insensitive(self):
        assert score_headline("EARNINGS BEAT EXPECTATIONS") > 0


# ---------------------------------------------------------------------------
# News adjustment tests
# ---------------------------------------------------------------------------

class TestNewsAdjustment:
    def test_max_positive_bounded(self):
        adj = compute_news_adjustment(1.0)
        assert adj == NEWS_ADJUSTMENT_BOUNDS["max_positive"]

    def test_max_negative_bounded(self):
        adj = compute_news_adjustment(-1.0)
        assert adj == NEWS_ADJUSTMENT_BOUNDS["max_negative"]

    def test_zero_sentiment_zero_adjustment(self):
        assert compute_news_adjustment(0.0) == 0.0

    def test_moderate_positive(self):
        adj = compute_news_adjustment(0.5)
        assert 0 < adj < NEWS_ADJUSTMENT_BOUNDS["max_positive"]

    def test_moderate_negative(self):
        adj = compute_news_adjustment(-0.5)
        assert NEWS_ADJUSTMENT_BOUNDS["max_negative"] < adj < 0


# ---------------------------------------------------------------------------
# Final score composition tests
# ---------------------------------------------------------------------------

class TestFinalScore:
    def test_additive_composition(self):
        assert compute_final_score(60.0, 5.0) == 65.0
        assert compute_final_score(60.0, -10.0) == 50.0

    def test_clamped_at_100(self):
        assert compute_final_score(95.0, 10.0) == 100.0

    def test_clamped_at_0(self):
        assert compute_final_score(5.0, -10.0) == 0.0

    def test_zero_adjustment_preserves_base(self):
        assert compute_final_score(42.5, 0.0) == 42.5

    def test_base_plus_adjustment_equals_final(self):
        base, adj = 55.0, 7.5
        assert compute_final_score(base, adj) == base + adj


# ---------------------------------------------------------------------------
# Final stance tests
# ---------------------------------------------------------------------------

class TestFinalStance:
    def test_strong_candidate(self):
        assert get_final_stance(60.0, "positive") == "Strong candidate"
        assert get_final_stance(70.0, "neutral") == "Strong candidate"

    def test_candidate_with_caution(self):
        assert get_final_stance(60.0, "negative") == "Candidate with caution"

    def test_candidate_positive_mid_score(self):
        assert get_final_stance(40.0, "positive") == "Candidate"

    def test_hold_off_low_score(self):
        assert get_final_stance(20.0, "neutral") == "Hold off"

    def test_hold_off_negative_mid_score(self):
        assert get_final_stance(40.0, "negative") == "Hold off"

    def test_mixed_insufficient(self):
        assert get_final_stance(40.0, "neutral") == "Mixed / insufficient news confidence"


# ---------------------------------------------------------------------------
# News analysis cache tests
# ---------------------------------------------------------------------------

class TestNewsAnalysisCache:
    def test_upsert_and_retrieve(self):
        analysis = {
            "sentiment_label": "positive",
            "sentiment_icon_color": "green",
            "news_adjustment": 5.0,
            "summary": "Positive coverage.",
            "headline_count": 3,
            "positive_count": 2,
            "negative_count": 0,
            "neutral_count": 1,
            "sentiment_score": 0.6,
            "risk_flags": [],
            "positive_catalysts": ["Earnings beat"],
            "analyzed_at": "2026-03-31T00:00:00",
            "expires_at": "2026-03-31T12:00:00",
        }
        db.upsert_news_analysis_cache("TESTCACHE", analysis)
        cached = db.get_news_analysis_cache("TESTCACHE")
        assert cached is not None
        assert cached["sentiment_label"] == "positive"
        assert cached["news_adjustment"] == 5.0
        assert cached["positive_catalysts"] == ["Earnings beat"]

    def test_cache_returns_none_for_unknown(self):
        assert db.get_news_analysis_cache("UNKNOWN") is None

    def test_upsert_overwrites(self):
        a1 = {
            "sentiment_label": "positive",
            "sentiment_icon_color": "green",
            "news_adjustment": 5.0,
            "summary": "Good.",
            "headline_count": 2,
            "positive_count": 2,
            "negative_count": 0,
            "neutral_count": 0,
            "sentiment_score": 0.5,
            "risk_flags": [],
            "positive_catalysts": [],
            "analyzed_at": "2026-03-31T00:00:00",
            "expires_at": "2026-03-31T12:00:00",
        }
        db.upsert_news_analysis_cache("UPD2", a1)
        a1["sentiment_label"] = "negative"
        a1["sentiment_icon_color"] = "red"
        a1["news_adjustment"] = -5.0
        db.upsert_news_analysis_cache("UPD2", a1)
        cached = db.get_news_analysis_cache("UPD2")
        assert cached["sentiment_label"] == "negative"
        assert cached["news_adjustment"] == -5.0


# ---------------------------------------------------------------------------
# News-aware orchestration tests
# ---------------------------------------------------------------------------

class TestNewsAwareOrchestration:
    @patch("services.recommendations._fetch_stock_data")
    def test_results_include_news_fields(self, mock_fetch):
        mock_fetch.return_value = _make_stock()
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": "TEST", "sector": "Technology", "industry": "Semiconductors"},
            ]
            results = get_recommendations()

        assert len(results) == 1
        r = results[0]
        assert "base_score" in r
        assert "sentiment_label" in r
        assert "sentiment_icon_color" in r
        assert "news_adjustment" in r
        assert "news_summary" in r or "news_headline_count" in r
        assert "risk_flags" in r
        assert "positive_catalysts" in r

    @patch("services.recommendations._fetch_stock_data")
    def test_neutral_fallback_preserves_base_score(self, mock_fetch):
        """With mocked enrichment (neutral), recommendation_score == base_score."""
        mock_fetch.return_value = _make_stock()
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": "TEST", "sector": "Technology", "industry": "Semiconductors"},
            ]
            results = get_recommendations()

        r = results[0]
        assert r["recommendation_score"] == r["base_score"]
        assert r["news_adjustment"] == 0.0
        assert r["sentiment_label"] == "neutral"

    @patch("services.recommendations._fetch_stock_data")
    def test_sort_uses_recommendation_score(self, mock_fetch):
        stocks = [
            _make_stock(ticker="A", current_price=65),
            _make_stock(ticker="B", current_price=115),
        ]
        mock_fetch.side_effect = lambda t: next(
            (s for s in stocks if s["ticker"] == t), None
        )
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": "A", "sector": "Technology", "industry": "Semiconductors"},
                {"ticker": "B", "sector": "Technology", "industry": "Semiconductors"},
            ]
            results = get_recommendations(sort_by="score")

        scores = [r["recommendation_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    @patch("services.recommendations._fetch_stock_data")
    def test_one_bad_ticker_does_not_break_others(self, mock_fetch):
        def side(ticker):
            if ticker == "BAD":
                return None
            return _make_stock(ticker=ticker)

        mock_fetch.side_effect = side
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": "BAD", "sector": "Technology", "industry": "Semiconductors"},
                {"ticker": "GOOD", "sector": "Technology", "industry": "Semiconductors"},
            ]
            results = get_recommendations()

        assert len(results) == 1
        assert results[0]["ticker"] == "GOOD"


# ---------------------------------------------------------------------------
# News-aware route tests
# ---------------------------------------------------------------------------

class TestNewsAwareRoutes:
    @patch("services.recommendations._fetch_stock_data")
    def test_recommendations_api_includes_sentiment_fields(self, mock_fetch, client):
        mock_fetch.return_value = _make_stock()
        with patch("services.recommendations.get_candidates") as mock_cands:
            mock_cands.return_value = [
                {"ticker": "TEST", "sector": "Technology", "industry": "Semiconductors"},
            ]
            resp = client.get("/api/recommendations?limit=5")

        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data) >= 1
        r = data[0]
        assert "sentiment_label" in r
        assert "base_score" in r
        assert "news_adjustment" in r

    def test_recommendations_page_still_returns_200(self, client):
        resp = client.get("/recommendations")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Summary and flags tests
# ---------------------------------------------------------------------------

class TestSummaryAndFlags:
    def test_summary_non_empty_for_positive(self):
        headlines = [
            {"title": "Earnings beat expectations with record revenue growth"},
        ]
        result = analyze_headlines(headlines)
        assert len(result["summary"]) > 0

    def test_summary_for_no_news(self):
        result = analyze_headlines([])
        assert "unavailable" in result["summary"].lower() or "no recent" in result["summary"].lower()

    def test_risk_flags_detected(self):
        headlines = [
            {"title": "Lawsuit filed against company over fraud allegations"},
        ]
        result = analyze_headlines(headlines)
        assert len(result["risk_flags"]) > 0

    def test_positive_catalysts_detected(self):
        headlines = [
            {"title": "Earnings beat expectations, analyst upgraded to buy"},
        ]
        result = analyze_headlines(headlines)
        assert len(result["positive_catalysts"]) > 0

    def test_flags_empty_for_neutral_headlines(self):
        headlines = [
            {"title": "Company schedules next board meeting"},
        ]
        result = analyze_headlines(headlines)
        assert result["risk_flags"] == []
        assert result["positive_catalysts"] == []
