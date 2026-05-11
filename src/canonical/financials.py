"""Canonical financial fact models.

Pydantic v2 models for validating rows before upsert/insert to Golden Safe
financial tables.

Models:
  - FinancialFact   → public.fact_financials  (compat view; INSTEAD OF
                      trigger routes writes to fact_financials_evidence)
  - FilingRecord    → public.fact_filings     (W8-core; NBB-primary truth)
  - FinancialLine   → public.fact_financials_lines  (W8-core; per-PCMN
                      granularity)

Constraint enforcement:
  FinancialFact:
    - amount_currency ∈ {EUR, USD, GBP, CHF, SEK, DKK, NOK, PLN, CZK}
    - confidence ∈ {Confirmed, Estimated, Indicative}
    - period_type ∈ {Annual, TTM, OTB, Semi-Annual, Quarterly, LTM, Interim}
    - Unique key: (party_id, period_label, source_code)
  FilingRecord:
    - period_flag ∈ {normal, extended, shortened}
    - Unique key: (party_id, source_code, filing_reference)
  FinancialLine:
    - amount_period ∈ {N, N1, N2}
    - data_type ∈ {met:am1, met:dec1}
    - Composite PK: (filing_id, pcmn_code, amount_period, data_type)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Currency = Literal["EUR", "USD", "GBP", "CHF", "SEK", "DKK", "NOK", "PLN", "CZK"]
Confidence = Literal["Confirmed", "Estimated", "Indicative"]
PeriodType = Literal["Annual", "TTM", "OTB", "Semi-Annual", "Quarterly", "LTM", "Interim"]
PeriodFlag = Literal["normal", "extended", "shortened"]
AmountPeriod = Literal["N", "N1", "N2"]
LineDataType = Literal["met:am1", "met:dec1"]


# ─────────────────────────────────────────────────────────────────────────────
# FinancialFact — fact_financials (compat view → fact_financials_evidence)
# ─────────────────────────────────────────────────────────────────────────────

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


# ─────────────────────────────────────────────────────────────────────────────
# FilingRecord — fact_filings (W8-core)
# ─────────────────────────────────────────────────────────────────────────────

class FilingRecord(BaseModel):
    """One row of fact_filings — validated, ready for upsert.

    Unique key: (party_id, source_code, filing_reference). Upsert semantics:
    same reference re-fetched updates in place. Different references for the
    same (party_id, period_end) are SEPARATE rows; supersession is a separate
    workflow via superseded_by / superseded_at columns (not touched here).

    period_label uses the same convention as FinancialFact (just the year
    string, e.g. "2024") to enable cross-table joins. period_flag and
    period_months carry the extended/shortened nuance.
    """
    model_config = ConfigDict(str_strip_whitespace=True)

    party_id:           UUID
    source_code:        str = Field(default="SRC_NBB")
    filing_reference:   str = Field(..., min_length=1, max_length=64)
    deposit_date:       date | None = None

    period_start:       date
    period_end:         date
    period_months:      int = Field(..., ge=1, le=24)
    period_flag:        PeriodFlag
    period_label:       str = Field(..., min_length=1, max_length=32)

    nbb_model_type:     str | None = None    # m01, m02, m03, m04
    nbb_schema_subtype: str | None = None    # e.g., "abbreviated", "full"
    taxonomy_version:   str | None = None
    consolidation:      str = "standalone"   # or "consolidated"
    enterprise_name:    str | None = None
    legal_form_code:    str | None = None
    raw_address:        dict[str, Any] | None = None
    language:           str | None = None    # nl, fr, de, en
    currency:           str = "EUR"
    loaded_by:          str | None = None

    def unique_key(self) -> tuple[str, str, str]:
        """Matches fact_filings UNIQUE (party_id, source_code, filing_reference)."""
        return (str(self.party_id), self.source_code, self.filing_reference)


# ─────────────────────────────────────────────────────────────────────────────
# FinancialLine — fact_financials_lines (W8-core)
# ─────────────────────────────────────────────────────────────────────────────

class FinancialLine(BaseModel):
    """One row of fact_financials_lines — validated, ready for bulk insert.

    filing_id is left optional during extraction (the extractor doesn't know
    it yet); the writer sets it during serialization, before insert.

    Composite PK in DB: (filing_id, pcmn_code, amount_period, data_type).
    The same filing typically produces ~50 lines for a full-format (m02) NBB
    filing, fewer for abbreviated (m01/m03).
    """
    model_config = ConfigDict(str_strip_whitespace=True)

    filing_id:     UUID | None = None  # set by FinancialsWriter.write_lines
    pcmn_code:     str = Field(..., min_length=1, max_length=16)
    amount_period: AmountPeriod = "N"
    data_type:     LineDataType = "met:am1"
    amount_eur:    float | None = None
    type_amount:   str | None = None


__all__ = [
    "FinancialFact",
    "FilingRecord",
    "FinancialLine",
    "Currency",
    "Confidence",
    "PeriodType",
    "PeriodFlag",
    "AmountPeriod",
    "LineDataType",
]
