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
