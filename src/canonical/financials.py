"""Canonical financial fact model.

Pydantic v2 model for validating rows before upsert to fact_financials.
Mirrors the public.fact_financials schema (GS dictionary lines 1863-1925).

Constraints enforced:
- amount_currency ∈ {EUR, USD, GBP, CHF, SEK, DKK, NOK, PLN, CZK}
- confidence ∈ {Confirmed, Estimated, Indicative}
- period_type ∈ {Annual, TTM, OTB, Semi-Annual, Quarterly, LTM, Interim}
- Unique key: (party_id, period_label, source_code)
"""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Currency = Literal["EUR", "USD", "GBP", "CHF", "SEK", "DKK", "NOK", "PLN", "CZK"]
Confidence = Literal["Confirmed", "Estimated", "Indicative"]
PeriodType = Literal["Annual", "TTM", "OTB", "Semi-Annual", "Quarterly", "LTM", "Interim"]


class FinancialFact(BaseModel):
    """One row of fact_financials — validated, ready for upsert."""
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    party_id:       UUID
    period_label:   str = Field(..., min_length=1, max_length=32)
    source_code:    str = Field(default="SRC_NBB")
    period_type:    PeriodType = "Annual"
    period_end:     date | None = None

    fiscal_year_start: date | None = None
    fiscal_year_end:   date | None = None
    nbb_model_type:    str | None = None    # m01, m02, m03
    nbb_filing_date:   date | None = None

    # Monetary, EUR millions
    revenue_eur_m:          float | None = None
    ebitda_eur_m:           float | None = None
    ebit_eur_m:             float | None = None
    net_income_eur_m:       float | None = None
    total_assets_eur_m:     float | None = None
    total_equity_eur_m:     float | None = None
    cash_eur_m:             float | None = None
    total_debt_eur_m:       float | None = None
    net_debt_eur_m:         float | None = None
    working_capital_eur_m:  float | None = None
    enterprise_value_eur_m: float | None = None
    market_cap_eur_m:       float | None = None

    # Counts
    employees: int | None = None

    # FX
    amount_currency: Currency = "EUR"
    fx_rate_to_eur:  float = 1.0
    fx_date:         date | None = None

    # Meta
    confidence: Confidence = "Confirmed"
    notes:      str | None = None

    def unique_key(self) -> tuple[str, str, str]:
        """Matches the uq_fact_financials_party_period_source constraint."""
        return (str(self.party_id), self.period_label, self.source_code)


__all__ = ["FinancialFact", "Currency", "Confidence", "PeriodType"]
