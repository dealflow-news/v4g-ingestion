"""NBB → fact_financials aggregator.

Maps raw PCMN codes (Belgian chart of accounts) extracted by parser.py
to the derived metrics stored in `public.fact_financials`.

Source amounts from NBB are in EUR units. Destination columns use suffix
`_eur_m` (millions). Pure count fields (employees, hours) pass through.

PCMN code mapping (authoritative, derived from v4g_accounts excel_builder.py
and the canonical BE-GAAP chart):

  Income statement:
    revenue             70
    ebit                9901
    net_income          9904
    depreciation        630                 (for EBITDA add-back)
    exc_depreciation    631/4                (optional, null-safe)
    provisions          635/8                (optional, null-safe)

  Balance sheet:
    total_assets        20/58                (preferred; fallback 20/28)
    total_equity        10/15
    cash                50/53
    current_assets      29/58                (for working_capital)
    current_liabilities 42/48                (for working_capital)
    lt_financial_debt   170/4                (M&A: financial debt only, not all 17)
    st_financial_debt   42/43                (M&A: financial debt only, not all 42)

  People:
    employees           9087                 (FTE, pass-through count)

Composite formulas:
    ebitda          = ebit + depreciation + exc_depreciation + provisions
    total_debt      = lt_financial_debt + st_financial_debt  (M&A convention)
    net_debt        = total_debt - cash
    working_capital = current_assets - current_liabilities

All composites are null-safe: if any required input is None, the composite
returns None (no partial computation).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

# ── PCMN code catalog ────────────────────────────────────────────────────────
# Direct mappings — one PCMN code → one fact_financials field.
DIRECT_MAP: dict[str, str] = {
    "70":    "revenue",            # Omzet
    "9901":  "ebit",               # Bedrijfsresultaat
    "9904":  "net_income",         # Winst v.h. boekjaar
    "20/58": "total_assets",       # Totaal activa (preferred)
    "10/15": "total_equity",       # Eigen vermogen
    "54/58": "cash",               # Liquide middelen (cbso-new canonical, see LB-009)
    "50/53": "cash",               # Liquide middelen (pfs-old fallback — see scale logic below)
}

# Secondary mappings for composites — NOT written to fact_financials directly.
COMPOSITE_INPUT_MAP: dict[str, str] = {
    "630":    "_depreciation",
    "631/4":  "_exc_depreciation",
    "635/8":  "_provisions",
    "29/58":  "_current_assets",
    "42/48":  "_current_liabilities",
    "170/4":  "_lt_financial_debt",   # M&A convention: financial debt only
    "42/43":  "_st_financial_debt",   # M&A convention: financial debt only
    "20/28":  "_total_assets_fallback",
}

# Pass-through counts (no EUR scaling).
COUNT_MAP: dict[str, str] = {
    "9087": "employees",
}

EUR_SCALE = Decimal("1000000")  # raw EUR → EUR millions


# ── Canonical output shape ───────────────────────────────────────────────────
@dataclass
class AggregatedYear:
    """One year of aggregated financials, ready for fact_financials upsert."""
    period_label: str          # e.g. "2024" or "2024-fiscal"
    period_end: date | None
    period_type: str = "Annual"
    fiscal_year_start: date | None = None
    fiscal_year_end: date | None = None
    nbb_model_type: str | None = None      # m01 / m02 / m03
    nbb_filing_date: date | None = None

    # Direct metrics (all Optional Decimal, in EUR millions except employees)
    revenue_eur_m: Decimal | None = None
    ebit_eur_m: Decimal | None = None
    net_income_eur_m: Decimal | None = None
    total_assets_eur_m: Decimal | None = None
    total_equity_eur_m: Decimal | None = None
    cash_eur_m: Decimal | None = None

    # Composites
    ebitda_eur_m: Decimal | None = None
    total_debt_eur_m: Decimal | None = None
    net_debt_eur_m: Decimal | None = None
    working_capital_eur_m: Decimal | None = None

    # Counts
    employees: int | None = None

    # Audit
    source_code: str = "SRC_NBB"
    amount_currency: str = "EUR"
    fx_rate_to_eur: Decimal = Decimal("1.0")
    confidence: str = "Confirmed"
    notes: str | None = None

    def as_upsert_row(self, party_id: str) -> dict[str, Any]:
        """Render for the Supabase fact_financials upsert."""
        return {
            "party_id":              party_id,
            "period_label":          self.period_label,
            "period_end":            self.period_end.isoformat() if self.period_end else None,
            "period_type":           self.period_type,
            "fiscal_year_start":     self.fiscal_year_start.isoformat() if self.fiscal_year_start else None,
            "fiscal_year_end":       self.fiscal_year_end.isoformat() if self.fiscal_year_end else None,
            "nbb_model_type":        self.nbb_model_type,
            "nbb_filing_date":       self.nbb_filing_date.isoformat() if self.nbb_filing_date else None,
            "revenue_eur_m":         _f(self.revenue_eur_m),
            "ebit_eur_m":            _f(self.ebit_eur_m),
            "ebitda_eur_m":          _f(self.ebitda_eur_m),
            "net_income_eur_m":      _f(self.net_income_eur_m),
            "total_assets_eur_m":    _f(self.total_assets_eur_m),
            "total_equity_eur_m":    _f(self.total_equity_eur_m),
            "cash_eur_m":            _f(self.cash_eur_m),
            "total_debt_eur_m":      _f(self.total_debt_eur_m),
            "net_debt_eur_m":        _f(self.net_debt_eur_m),
            "working_capital_eur_m": _f(self.working_capital_eur_m),
            "employees":             self.employees,
            "source_code":           self.source_code,
            "amount_currency":       self.amount_currency,
            "fx_rate_to_eur":        _f(self.fx_rate_to_eur),
            "confidence":            self.confidence,
            "notes":                 self.notes,
        }


def _f(v: Decimal | None) -> float | None:
    """Decimal → float for Supabase numeric columns (they accept JSON floats)."""
    return float(v) if v is not None else None


# ── Core aggregation ─────────────────────────────────────────────────────────
def aggregate_year(
    year_data: dict[str, float | int | None],
    *,
    period_label: str,
    period_end: date | None = None,
    fiscal_year_start: date | None = None,
    fiscal_year_end: date | None = None,
    nbb_model_type: str | None = None,
    nbb_filing_date: date | None = None,
) -> AggregatedYear:
    """Aggregate one year's raw {pcmn_code: eur_amount} dict to fact_financials shape.

    Input `year_data` comes from parser.py / fetcher.py: {pcmn_code: eur_amount}
    where amounts are in RAW EUR (not thousands, not millions).

    Returns an AggregatedYear with all computable metrics filled in; missing
    inputs produce None for that metric (but do not block other metrics).
    """
    # Unpack direct fields
    raw: dict[str, Decimal | None] = {}
    for code in list(DIRECT_MAP) + list(COMPOSITE_INPUT_MAP):
        v = year_data.get(code)
        raw[code] = _to_dec(v)

    # Special: employees is a count, don't scale
    employees_raw = year_data.get("9087")
    employees = int(employees_raw) if employees_raw is not None else None

    # Direct → EUR millions
    def scale(code: str) -> Decimal | None:
        v = raw.get(code)
        return (v / EUR_SCALE).quantize(Decimal("0.001")) if v is not None else None

    revenue      = scale("70")
    ebit         = scale("9901")
    net_income   = scale("9904")
    total_equity = scale("10/15")

    # Cash: cbso-new uses 54/58 (Liquide middelen), pfs-old historically packed
    # cash into 50/53 (which in cbso-new is Geldbeleggingen / current investments).
    # Prefer 54/58; fallback to 50/53 for pre-2021 filings.
    cash         = scale("54/58") or scale("50/53")

    # Total assets — prefer 20/58, fallback 20/28 (older schemas use 20/28 alone)
    total_assets = scale("20/58") or scale("20/28")

    # Composite: EBITDA = EBIT + D&A + exceptional deprec + provisions
    # All null-safe: if EBIT is None, EBITDA is None. Optional components
    # that are None contribute 0.
    if ebit is not None:
        add_back = (
            (scale("630")    or Decimal(0))
            + (scale("631/4") or Decimal(0))
            + (scale("635/8") or Decimal(0))
        )
        ebitda = (ebit + add_back).quantize(Decimal("0.001"))
    else:
        ebitda = None

    # Composite: total_debt = LT financial + ST financial (M&A convention —
    # excludes trade payables, taxes, social debts). For a broader "total
    # liabilities" figure, use raw codes 17 + 42/48 or the sum 10/49 - 10/15.
    ltd = scale("170/4")
    std = scale("42/43")
    if ltd is not None or std is not None:
        total_debt = ((ltd or Decimal(0)) + (std or Decimal(0))).quantize(Decimal("0.001"))
    else:
        total_debt = None

    # Composite: net_debt = total_debt - cash
    if total_debt is not None and cash is not None:
        net_debt = (total_debt - cash).quantize(Decimal("0.001"))
    else:
        net_debt = None

    # Composite: working_capital = current_assets - current_liabilities
    ca = scale("29/58")
    cl = scale("42/48")
    wc = (ca - cl).quantize(Decimal("0.001")) if ca is not None and cl is not None else None

    return AggregatedYear(
        period_label=period_label,
        period_end=period_end,
        fiscal_year_start=fiscal_year_start,
        fiscal_year_end=fiscal_year_end,
        nbb_model_type=nbb_model_type,
        nbb_filing_date=nbb_filing_date,
        revenue_eur_m=revenue,
        ebit_eur_m=ebit,
        ebitda_eur_m=ebitda,
        net_income_eur_m=net_income,
        total_assets_eur_m=total_assets,
        total_equity_eur_m=total_equity,
        cash_eur_m=cash,
        total_debt_eur_m=total_debt,
        net_debt_eur_m=net_debt,
        working_capital_eur_m=wc,
        employees=employees,
    )


def _to_dec(v: float | int | str | None) -> Decimal | None:
    """Null-safe cast to Decimal."""
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except Exception:
        return None


__all__ = ["AggregatedYear", "aggregate_year", "DIRECT_MAP", "COUNT_MAP"]
