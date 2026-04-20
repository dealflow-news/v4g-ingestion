"""Aggregator formula tests.

The aggregator is where the most fragile business logic lives — accounting
formulas that need to match what analysts expect. These tests use small
fixture dicts that match the output shape of parser.py.

If you change a formula, the tests MUST change in lockstep. Never silently
adjust one without the other.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.domain.nbb.aggregator import aggregate_year


# ── Fixtures ─────────────────────────────────────────────────────────────────
@pytest.fixture
def complete_year_m02() -> dict[str, float | int]:
    """Realistic full-filing (m02 volledig schema) year data.

    Numbers tuned so derived metrics are easy to check by hand:
      Revenue     10,000,000 EUR (= 10.0 M)
      EBIT         1,500,000 EUR (= 1.5 M)
      Depreciation   500,000 EUR (= 0.5 M)
      EBITDA       2,000,000 EUR (= 2.0 M)   ← EBIT + depreciation
      Total assets 8,000,000 EUR (= 8.0 M)
      Equity       3,000,000 EUR (= 3.0 M)
      Cash           800,000 EUR (= 0.8 M)
      LT debt      2,000,000 EUR (= 2.0 M)
      ST debt        500,000 EUR (= 0.5 M)
      Total debt   2,500,000 EUR (= 2.5 M)
      Net debt     1,700,000 EUR (= 1.7 M)   ← total_debt − cash
      Current assets 4,000,000 (= 4.0 M)
      Current liab.  1,800,000 (= 1.8 M)
      Working cap  2,200,000 (= 2.2 M)
      Net income   1,000,000 (= 1.0 M)
      FTE                12.5
    """
    return {
        "70":    10_000_000,       # Revenue
        "9901":   1_500_000,       # EBIT
        "9904":   1_000_000,       # Net income
        "630":      500_000,       # Depreciation
        "20/58":  8_000_000,       # Total assets
        "10/15":  3_000_000,       # Total equity
        "50/53":    800_000,       # Cash
        "170/4":  2_000_000,       # LT financial debt (M&A convention)
        "42/43":    500_000,       # ST financial debt (M&A convention)
        "29/58":  4_000_000,       # Current assets
        "42/48":  1_800_000,       # Current liabilities
        "9087":        12.5,       # Employees (FTE)
    }


# ── Direct field mapping ─────────────────────────────────────────────────────
def test_direct_fields_scale_to_millions(complete_year_m02):
    agg = aggregate_year(complete_year_m02, period_label="2024")
    assert agg.revenue_eur_m      == Decimal("10.000")
    assert agg.ebit_eur_m         == Decimal("1.500")
    assert agg.net_income_eur_m   == Decimal("1.000")
    assert agg.total_assets_eur_m == Decimal("8.000")
    assert agg.total_equity_eur_m == Decimal("3.000")
    assert agg.cash_eur_m         == Decimal("0.800")


def test_employees_passthrough_as_int(complete_year_m02):
    agg = aggregate_year(complete_year_m02, period_label="2024")
    # 12.5 FTE is kept as int via the int() cast — losing the 0.5 is OK
    # per the fact_financials schema (column type = integer).
    assert agg.employees == 12


# ── Composites ───────────────────────────────────────────────────────────────
def test_ebitda_equals_ebit_plus_da(complete_year_m02):
    agg = aggregate_year(complete_year_m02, period_label="2024")
    # EBITDA = EBIT (1.5) + depreciation (0.5) = 2.0
    assert agg.ebitda_eur_m == Decimal("2.000")


def test_ebitda_uses_all_addbacks():
    data = {
        "9901":   1_000_000,  # EBIT
        "630":      300_000,  # Normal depreciation
        "631/4":    100_000,  # Exceptional depreciation
        "635/8":     50_000,  # Provisions
    }
    agg = aggregate_year(data, period_label="2024")
    # 1.0 + 0.3 + 0.1 + 0.05 = 1.45
    assert agg.ebitda_eur_m == Decimal("1.450")


def test_ebitda_is_none_if_ebit_missing():
    data = {"630": 500_000}  # depreciation but no EBIT
    agg = aggregate_year(data, period_label="2024")
    assert agg.ebitda_eur_m is None


def test_net_debt_equals_total_debt_minus_cash(complete_year_m02):
    agg = aggregate_year(complete_year_m02, period_label="2024")
    # total_debt = LT (2.0) + ST (0.5) = 2.5
    # net_debt = 2.5 - cash (0.8) = 1.7
    assert agg.total_debt_eur_m == Decimal("2.500")
    assert agg.net_debt_eur_m   == Decimal("1.700")


def test_net_debt_is_none_without_cash():
    data = {"170/4": 1_000_000}  # only LT financial debt, no cash
    agg = aggregate_year(data, period_label="2024")
    assert agg.total_debt_eur_m == Decimal("1.000")
    assert agg.net_debt_eur_m is None  # cash required for net


def test_working_capital(complete_year_m02):
    agg = aggregate_year(complete_year_m02, period_label="2024")
    # CA (4.0) - CL (1.8) = 2.2
    assert agg.working_capital_eur_m == Decimal("2.200")


def test_working_capital_is_none_if_either_missing():
    data = {"29/58": 1_000_000}  # only current assets
    agg = aggregate_year(data, period_label="2024")
    assert agg.working_capital_eur_m is None


# ── Null safety ──────────────────────────────────────────────────────────────
def test_empty_year_yields_all_nones():
    agg = aggregate_year({}, period_label="2024")
    assert agg.revenue_eur_m is None
    assert agg.ebitda_eur_m is None
    assert agg.employees is None
    assert agg.period_label == "2024"


def test_period_metadata_passthrough():
    agg = aggregate_year(
        {"70": 1_000_000},
        period_label="2024-fiscal",
        period_end=date(2024, 12, 31),
        fiscal_year_start=date(2024, 1, 1),
        fiscal_year_end=date(2024, 12, 31),
        nbb_model_type="m02",
        nbb_filing_date=date(2025, 4, 15),
    )
    assert agg.period_label      == "2024-fiscal"
    assert agg.period_end        == date(2024, 12, 31)
    assert agg.nbb_model_type    == "m02"
    assert agg.nbb_filing_date   == date(2025, 4, 15)


# ── Fallback behavior ────────────────────────────────────────────────────────
def test_total_assets_fallback_to_20_28():
    """Older schemas (m01/m03 verkort/micro) may use 20/28 instead of 20/58."""
    data = {"20/28": 5_000_000}
    agg = aggregate_year(data, period_label="2024")
    assert agg.total_assets_eur_m == Decimal("5.000")


def test_total_assets_prefers_20_58_over_20_28():
    """When both present, prefer the complete 20/58 (full schema)."""
    data = {"20/58": 8_000_000, "20/28": 5_000_000}
    agg = aggregate_year(data, period_label="2024")
    assert agg.total_assets_eur_m == Decimal("8.000")


# ── Upsert shape ─────────────────────────────────────────────────────────────
def test_as_upsert_row_shape(complete_year_m02):
    agg = aggregate_year(
        complete_year_m02,
        period_label="2024",
        period_end=date(2024, 12, 31),
    )
    row = agg.as_upsert_row(party_id="87f123ef-64e0-463a-b79c-ad4c0bef2855")

    # Required fields for unique constraint
    assert row["party_id"]     == "87f123ef-64e0-463a-b79c-ad4c0bef2855"
    assert row["period_label"] == "2024"
    assert row["source_code"]  == "SRC_NBB"
    assert row["period_type"]  == "Annual"
    # Numeric fields are floats (JSON-safe)
    assert isinstance(row["revenue_eur_m"], float)
    assert row["revenue_eur_m"] == 10.0
    assert row["period_end"]   == "2024-12-31"
    assert row["amount_currency"] == "EUR"
    assert row["fx_rate_to_eur"] == 1.0
