"""
Starter stock universe organized by sector and industry.

This module provides the candidate tickers that the recommendations engine
evaluates.  The data structure is intentionally flat (list of dicts) so it can
later be swapped for a database table, CSV file, or external API without
changing the public interface.

To expand coverage, simply add entries to ``_UNIVERSE`` or replace
``get_candidates`` with a different data source.
"""

from __future__ import annotations

from typing import Optional

# Each entry carries the minimal classification needed for filtering.
# ``yfinance`` is used at runtime to fetch price / financial data, so we
# only store the mapping from ticker -> sector / industry here.

_UNIVERSE: list[dict] = [
    # ── Technology ──
    {"ticker": "AAPL",  "sector": "Technology", "industry": "Consumer Electronics"},
    {"ticker": "MSFT",  "sector": "Technology", "industry": "Software—Infrastructure"},
    {"ticker": "GOOGL", "sector": "Technology", "industry": "Internet Content & Information"},
    {"ticker": "META",  "sector": "Technology", "industry": "Internet Content & Information"},
    {"ticker": "NVDA",  "sector": "Technology", "industry": "Semiconductors"},
    {"ticker": "AMD",   "sector": "Technology", "industry": "Semiconductors"},
    {"ticker": "INTC",  "sector": "Technology", "industry": "Semiconductors"},
    {"ticker": "AVGO",  "sector": "Technology", "industry": "Semiconductors"},
    {"ticker": "QCOM",  "sector": "Technology", "industry": "Semiconductors"},
    {"ticker": "TSM",   "sector": "Technology", "industry": "Semiconductors"},
    {"ticker": "CRM",   "sector": "Technology", "industry": "Software—Application"},
    {"ticker": "ADBE",  "sector": "Technology", "industry": "Software—Application"},
    {"ticker": "ORCL",  "sector": "Technology", "industry": "Software—Infrastructure"},
    {"ticker": "CSCO",  "sector": "Technology", "industry": "Communication Equipment"},
    {"ticker": "IBM",   "sector": "Technology", "industry": "Information Technology Services"},
    {"ticker": "TXN",   "sector": "Technology", "industry": "Semiconductors"},
    {"ticker": "NOW",   "sector": "Technology", "industry": "Software—Application"},
    {"ticker": "INTU",  "sector": "Technology", "industry": "Software—Application"},
    {"ticker": "AMAT",  "sector": "Technology", "industry": "Semiconductor Equipment & Materials"},
    {"ticker": "MU",    "sector": "Technology", "industry": "Semiconductors"},

    # ── Healthcare ──
    {"ticker": "JNJ",   "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "UNH",   "sector": "Healthcare", "industry": "Healthcare Plans"},
    {"ticker": "PFE",   "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "ABBV",  "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "MRK",   "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "LLY",   "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "TMO",   "sector": "Healthcare", "industry": "Diagnostics & Research"},
    {"ticker": "ABT",   "sector": "Healthcare", "industry": "Medical Devices"},
    {"ticker": "DHR",   "sector": "Healthcare", "industry": "Diagnostics & Research"},
    {"ticker": "BMY",   "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "AMGN",  "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "GILD",  "sector": "Healthcare", "industry": "Drug Manufacturers—General"},
    {"ticker": "MDT",   "sector": "Healthcare", "industry": "Medical Devices"},
    {"ticker": "ISRG",  "sector": "Healthcare", "industry": "Medical Instruments & Supplies"},
    {"ticker": "CVS",   "sector": "Healthcare", "industry": "Healthcare Plans"},

    # ── Financial Services ──
    {"ticker": "JPM",   "sector": "Financial Services", "industry": "Banks—Diversified"},
    {"ticker": "BAC",   "sector": "Financial Services", "industry": "Banks—Diversified"},
    {"ticker": "WFC",   "sector": "Financial Services", "industry": "Banks—Diversified"},
    {"ticker": "GS",    "sector": "Financial Services", "industry": "Capital Markets"},
    {"ticker": "MS",    "sector": "Financial Services", "industry": "Capital Markets"},
    {"ticker": "C",     "sector": "Financial Services", "industry": "Banks—Diversified"},
    {"ticker": "BLK",   "sector": "Financial Services", "industry": "Asset Management"},
    {"ticker": "SCHW",  "sector": "Financial Services", "industry": "Capital Markets"},
    {"ticker": "AXP",   "sector": "Financial Services", "industry": "Credit Services"},
    {"ticker": "V",     "sector": "Financial Services", "industry": "Credit Services"},
    {"ticker": "MA",    "sector": "Financial Services", "industry": "Credit Services"},
    {"ticker": "SPGI",  "sector": "Financial Services", "industry": "Financial Data & Stock Exchanges"},
    {"ticker": "CME",   "sector": "Financial Services", "industry": "Financial Data & Stock Exchanges"},
    {"ticker": "USB",   "sector": "Financial Services", "industry": "Banks—Regional"},
    {"ticker": "PNC",   "sector": "Financial Services", "industry": "Banks—Regional"},

    # ── Consumer Cyclical ──
    {"ticker": "AMZN",  "sector": "Consumer Cyclical", "industry": "Internet Retail"},
    {"ticker": "TSLA",  "sector": "Consumer Cyclical", "industry": "Auto Manufacturers"},
    {"ticker": "HD",    "sector": "Consumer Cyclical", "industry": "Home Improvement Retail"},
    {"ticker": "NKE",   "sector": "Consumer Cyclical", "industry": "Footwear & Accessories"},
    {"ticker": "MCD",   "sector": "Consumer Cyclical", "industry": "Restaurants"},
    {"ticker": "SBUX",  "sector": "Consumer Cyclical", "industry": "Restaurants"},
    {"ticker": "LOW",   "sector": "Consumer Cyclical", "industry": "Home Improvement Retail"},
    {"ticker": "TGT",   "sector": "Consumer Cyclical", "industry": "Discount Stores"},
    {"ticker": "F",     "sector": "Consumer Cyclical", "industry": "Auto Manufacturers"},
    {"ticker": "GM",    "sector": "Consumer Cyclical", "industry": "Auto Manufacturers"},
    {"ticker": "BKNG",  "sector": "Consumer Cyclical", "industry": "Travel Services"},
    {"ticker": "MAR",   "sector": "Consumer Cyclical", "industry": "Lodging"},
    {"ticker": "ABNB",  "sector": "Consumer Cyclical", "industry": "Travel Services"},
    {"ticker": "ROST",  "sector": "Consumer Cyclical", "industry": "Apparel Retail"},
    {"ticker": "TJX",   "sector": "Consumer Cyclical", "industry": "Apparel Retail"},

    # ── Consumer Defensive ──
    {"ticker": "PG",    "sector": "Consumer Defensive", "industry": "Household & Personal Products"},
    {"ticker": "KO",    "sector": "Consumer Defensive", "industry": "Beverages—Non-Alcoholic"},
    {"ticker": "PEP",   "sector": "Consumer Defensive", "industry": "Beverages—Non-Alcoholic"},
    {"ticker": "COST",  "sector": "Consumer Defensive", "industry": "Discount Stores"},
    {"ticker": "WMT",   "sector": "Consumer Defensive", "industry": "Discount Stores"},
    {"ticker": "PM",    "sector": "Consumer Defensive", "industry": "Tobacco"},
    {"ticker": "MO",    "sector": "Consumer Defensive", "industry": "Tobacco"},
    {"ticker": "CL",    "sector": "Consumer Defensive", "industry": "Household & Personal Products"},
    {"ticker": "MDLZ",  "sector": "Consumer Defensive", "industry": "Confectioners"},
    {"ticker": "KHC",   "sector": "Consumer Defensive", "industry": "Packaged Foods"},
    {"ticker": "GIS",   "sector": "Consumer Defensive", "industry": "Packaged Foods"},
    {"ticker": "SYY",   "sector": "Consumer Defensive", "industry": "Food Distribution"},
    {"ticker": "KR",    "sector": "Consumer Defensive", "industry": "Grocery Stores"},
    {"ticker": "ADM",   "sector": "Consumer Defensive", "industry": "Farm Products"},
    {"ticker": "STZ",   "sector": "Consumer Defensive", "industry": "Beverages—Wineries & Distilleries"},

    # ── Energy ──
    {"ticker": "XOM",   "sector": "Energy", "industry": "Oil & Gas Integrated"},
    {"ticker": "CVX",   "sector": "Energy", "industry": "Oil & Gas Integrated"},
    {"ticker": "COP",   "sector": "Energy", "industry": "Oil & Gas E&P"},
    {"ticker": "SLB",   "sector": "Energy", "industry": "Oil & Gas Equipment & Services"},
    {"ticker": "EOG",   "sector": "Energy", "industry": "Oil & Gas E&P"},
    {"ticker": "MPC",   "sector": "Energy", "industry": "Oil & Gas Refining & Marketing"},
    {"ticker": "PSX",   "sector": "Energy", "industry": "Oil & Gas Refining & Marketing"},
    {"ticker": "VLO",   "sector": "Energy", "industry": "Oil & Gas Refining & Marketing"},
    {"ticker": "OXY",   "sector": "Energy", "industry": "Oil & Gas E&P"},
    {"ticker": "HAL",   "sector": "Energy", "industry": "Oil & Gas Equipment & Services"},
    {"ticker": "DVN",   "sector": "Energy", "industry": "Oil & Gas E&P"},
    {"ticker": "FANG",  "sector": "Energy", "industry": "Oil & Gas E&P"},
    {"ticker": "WMB",   "sector": "Energy", "industry": "Oil & Gas Midstream"},
    {"ticker": "KMI",   "sector": "Energy", "industry": "Oil & Gas Midstream"},
    {"ticker": "OKE",   "sector": "Energy", "industry": "Oil & Gas Midstream"},

    # ── Industrials ──
    {"ticker": "CAT",   "sector": "Industrials", "industry": "Farm & Heavy Construction Machinery"},
    {"ticker": "DE",    "sector": "Industrials", "industry": "Farm & Heavy Construction Machinery"},
    {"ticker": "UNP",   "sector": "Industrials", "industry": "Railroads"},
    {"ticker": "BA",    "sector": "Industrials", "industry": "Aerospace & Defense"},
    {"ticker": "HON",   "sector": "Industrials", "industry": "Conglomerates"},
    {"ticker": "RTX",   "sector": "Industrials", "industry": "Aerospace & Defense"},
    {"ticker": "LMT",   "sector": "Industrials", "industry": "Aerospace & Defense"},
    {"ticker": "GE",    "sector": "Industrials", "industry": "Specialty Industrial Machinery"},
    {"ticker": "MMM",   "sector": "Industrials", "industry": "Conglomerates"},
    {"ticker": "UPS",   "sector": "Industrials", "industry": "Integrated Freight & Logistics"},
    {"ticker": "FDX",   "sector": "Industrials", "industry": "Integrated Freight & Logistics"},
    {"ticker": "WM",    "sector": "Industrials", "industry": "Waste Management"},
    {"ticker": "EMR",   "sector": "Industrials", "industry": "Specialty Industrial Machinery"},
    {"ticker": "ETN",   "sector": "Industrials", "industry": "Specialty Industrial Machinery"},
    {"ticker": "ITW",   "sector": "Industrials", "industry": "Specialty Industrial Machinery"},

    # ── Communication Services ──
    {"ticker": "GOOG",  "sector": "Communication Services", "industry": "Internet Content & Information"},
    {"ticker": "DIS",   "sector": "Communication Services", "industry": "Entertainment"},
    {"ticker": "NFLX",  "sector": "Communication Services", "industry": "Entertainment"},
    {"ticker": "CMCSA", "sector": "Communication Services", "industry": "Telecom Services"},
    {"ticker": "T",     "sector": "Communication Services", "industry": "Telecom Services"},
    {"ticker": "VZ",    "sector": "Communication Services", "industry": "Telecom Services"},
    {"ticker": "TMUS",  "sector": "Communication Services", "industry": "Telecom Services"},
    {"ticker": "ATVI",  "sector": "Communication Services", "industry": "Electronic Gaming & Multimedia"},
    {"ticker": "EA",    "sector": "Communication Services", "industry": "Electronic Gaming & Multimedia"},
    {"ticker": "TTWO",  "sector": "Communication Services", "industry": "Electronic Gaming & Multimedia"},

    # ── Utilities ──
    {"ticker": "NEE",   "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "DUK",   "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "SO",    "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "D",     "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "AEP",   "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "SRE",   "sector": "Utilities", "industry": "Utilities—Diversified"},
    {"ticker": "EXC",   "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "XEL",   "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "WEC",   "sector": "Utilities", "industry": "Utilities—Regulated Electric"},
    {"ticker": "ED",    "sector": "Utilities", "industry": "Utilities—Regulated Electric"},

    # ── Real Estate ──
    {"ticker": "AMT",   "sector": "Real Estate", "industry": "REIT—Specialty"},
    {"ticker": "PLD",   "sector": "Real Estate", "industry": "REIT—Industrial"},
    {"ticker": "CCI",   "sector": "Real Estate", "industry": "REIT—Specialty"},
    {"ticker": "EQIX",  "sector": "Real Estate", "industry": "REIT—Specialty"},
    {"ticker": "SPG",   "sector": "Real Estate", "industry": "REIT—Retail"},
    {"ticker": "PSA",   "sector": "Real Estate", "industry": "REIT—Specialty"},
    {"ticker": "O",     "sector": "Real Estate", "industry": "REIT—Retail"},
    {"ticker": "WELL",  "sector": "Real Estate", "industry": "REIT—Healthcare Facilities"},
    {"ticker": "AVB",   "sector": "Real Estate", "industry": "REIT—Residential"},
    {"ticker": "DLR",   "sector": "Real Estate", "industry": "REIT—Specialty"},

    # ── Basic Materials ──
    {"ticker": "LIN",   "sector": "Basic Materials", "industry": "Specialty Chemicals"},
    {"ticker": "APD",   "sector": "Basic Materials", "industry": "Specialty Chemicals"},
    {"ticker": "SHW",   "sector": "Basic Materials", "industry": "Specialty Chemicals"},
    {"ticker": "ECL",   "sector": "Basic Materials", "industry": "Specialty Chemicals"},
    {"ticker": "NEM",   "sector": "Basic Materials", "industry": "Gold"},
    {"ticker": "FCX",   "sector": "Basic Materials", "industry": "Copper"},
    {"ticker": "NUE",   "sector": "Basic Materials", "industry": "Steel"},
    {"ticker": "DOW",   "sector": "Basic Materials", "industry": "Chemicals"},
    {"ticker": "DD",    "sector": "Basic Materials", "industry": "Specialty Chemicals"},
    {"ticker": "PPG",   "sector": "Basic Materials", "industry": "Specialty Chemicals"},
]


def get_sectors() -> list[str]:
    """Return a sorted list of distinct sector names in the universe."""
    return sorted({entry["sector"] for entry in _UNIVERSE})


def get_industries(sector: Optional[str] = None) -> list[str]:
    """Return sorted industries, optionally filtered to a single sector."""
    pool = _UNIVERSE if sector is None else [
        e for e in _UNIVERSE if e["sector"] == sector
    ]
    return sorted({e["industry"] for e in pool})


def get_candidates(
    sector: Optional[str] = None,
    industry: Optional[str] = None,
) -> list[dict]:
    """Return universe entries matching the given sector/industry filters."""
    results = _UNIVERSE
    if sector:
        results = [e for e in results if e["sector"] == sector]
    if industry:
        results = [e for e in results if e["industry"] == industry]
    return [dict(e) for e in results]
