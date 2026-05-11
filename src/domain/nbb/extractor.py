"""
NBB Extractor — orchestrator that converts raw NBB API data to DB-ready shape.

Input: raw JSON-XBRL dict (from fetcher.fetch_jsonxbrl) + filing meta (from get_references)
Output: dict with three sections:
  - 'filing':   row for fact_filings
  - 'lines':    list of rows for fact_financials_lines (~50 per filing)
  - 'evidence': aggregate KPIs for fact_financials_evidence (back-compat dual-write)

Lane A (live JSON-XBRL): use extract_from_jsonxbrl()
Lane B (XBRL XML from ZIPs): use extract_from_xbrl_xml() — parses via parser.parse_xbrl

This module has no DB I/O — pure transformation. Worker code calls these and
then writes via Supabase client.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from .parser import parse_xbrl

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

SOURCE_CODE = "SRC_NBB"

# Codes that are NOT EUR — workforce counts (FTE, total hours).
# Align with fetcher.PURE_COUNT_CODES.
# Note: 1023 (full-time wages) and 1024 (part-time wages) are EUR amounts,
# NOT counts — they're components of code 62 (personeelskosten).
COUNT_CODES = {"9087", "9088"}

# PCMN codes that map to evidence-aggregate KPIs.
# Multiple codes per KPI to handle parallel taxonomies:
#   - bas:mXX → primary MAR codes (e.g., 9901 for EBIT)
#   - be-gaap-ci → secondary codes seeded in W8-core (e.g., 649 for EBIT)
#   - Parser picks ONE per filing based on source namespace; both never
#     populated together. KPI computation tries codes in priority order.
#
# Format: KPI_NAME → tuple of pcmn_codes in priority order (first hit wins)

KPI_REVENUE       = ("70",)
KPI_EBIT          = ("9901", "649")           # bas vs be-gaap-ci
KPI_NET_INCOME    = ("9904",)
KPI_TOTAL_ASSETS  = ("20/58",)
KPI_TOTAL_EQUITY  = ("10/15",)
KPI_CASH          = ("50/53", "54/58")        # bas vs be-gaap-ci
KPI_INCOME_TAX    = ("67", "9134")            # bas vs be-gaap-ci (PL section)
KPI_FIN_LT_DEBT   = ("170/4",)
KPI_FIN_ST_DEBT   = ("42/43",)

# Codes used to DERIVE EBITDA (NBB has no direct EBITDA line)
# EBITDA = EBIT + Depreciation/Amortisation + Impairments + Provisions
DEPRECIATION_CODES = ("630", "631/4", "635/8")


# ─────────────────────────────────────────────────────────────────────────────
# Period helpers
# ─────────────────────────────────────────────────────────────────────────────

def _classify_period(period_months: int) -> str:
    """Return period_flag based on length in months."""
    if 10 <= period_months <= 14:
        return "normal"
    if period_months > 14:
        return "extended"
    return "shortened"


def _period_label(fy_end: date, period_months: int, period_flag: str) -> str:
    """Build human-readable period label, e.g. 'FY2024' or 'FY2024 (extended 18m)'."""
    base = f"FY{fy_end.year}"
    if period_flag != "normal":
        return f"{base} ({period_flag} {period_months}m)"
    return base


def _parse_iso_date(s: str) -> Optional[date]:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Lane A — Live JSON-XBRL extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_from_jsonxbrl(
    data: dict,
    filing_meta: dict,
    party_id: UUID | str,
    loaded_by: str = "v4g-ingestion-worker",
) -> dict:
    """
    Parse one Authentic Data JSON-XBRL response into DB-ready dict.

    Args:
        data: raw response from fetcher.fetch_jsonxbrl()
        filing_meta: one entry from fetcher.get_references() (has ReferenceNumber, etc)
        party_id: resolved party_id from DB (caller looked up party by kbo_nr)
        loaded_by: identifier of process loading (worker name/PID)

    Returns:
        {
          'filing':   {...},  # one row for fact_filings
          'lines':    [...],  # rows for fact_financials_lines
          'evidence': {...},  # aggregate KPIs for fact_financials_evidence
        }
    """
    # ── Metadata ──────────────────────────────────────────────────────────
    ref_num = filing_meta.get("referenceNumber") or filing_meta.get("ReferenceNumber", "")
    ex = filing_meta.get("ExerciseDates") or filing_meta.get("exerciseDates") or {}
    fy_start = _parse_iso_date(ex.get("startDate") or ex.get("StartDate") or "")
    fy_end = _parse_iso_date(ex.get("endDate") or ex.get("EndDate") or "")

    if not fy_end:
        raise ValueError(f"Filing {ref_num} has no fiscal year end date")

    if not fy_start:
        # fallback: assume 12 months
        fy_start = date(fy_end.year - 1, fy_end.month, fy_end.day) if fy_end.day > 1 else date(fy_end.year - 1, fy_end.month, 1)

    delta_days = (fy_end - fy_start).days
    period_months = round(delta_days / 30.4375)
    period_flag = _classify_period(period_months)
    period_label = str(fy_end.year)  # match existing fact_financials pattern; period_flag carries extended/shortened info

    deposit_date = _parse_iso_date(filing_meta.get("DepositDate") or filing_meta.get("depositDate") or "")
    model_type = (filing_meta.get("ModelType") or filing_meta.get("modelType") or "").strip()

    enterprise_name = (
        data.get("EnterpriseName")
        or filing_meta.get("EnterpriseName")
        or filing_meta.get("enterpriseName")
        or ""
    ).strip()

    legal_form = (
        filing_meta.get("LegalForm")
        or filing_meta.get("legalForm")
        or filing_meta.get("LegalFormCode")
        or ""
    ).strip() or None

    # Address: collect any address-like fields into jsonb
    raw_address = filing_meta.get("Address") or filing_meta.get("address") or None

    language = (filing_meta.get("Language") or filing_meta.get("language") or "").strip() or None
    consolidation = (filing_meta.get("Consolidation") or filing_meta.get("consolidation") or "standalone").strip()

    # ── Filing row ────────────────────────────────────────────────────────
    filing_row = {
        "party_id":          str(party_id),
        "source_code":       SOURCE_CODE,
        "filing_reference":  str(ref_num),
        "deposit_date":      deposit_date,
        "period_start":      fy_start,
        "period_end":        fy_end,
        "period_months":     period_months,
        "period_flag":       period_flag,
        "period_label":      period_label,
        "nbb_model_type":    model_type or None,
        "nbb_schema_subtype": None,  # not always present in API; parser fills this for XBRL XML
        "taxonomy_version":  None,
        "consolidation":     consolidation,
        "enterprise_name":   enterprise_name or None,
        "legal_form_code":   legal_form,
        "raw_address":       raw_address,
        "language":          language,
        "currency":          "EUR",
        "loaded_by":         loaded_by,
        # filing_id, loaded_at, superseded_* set by DB defaults
    }

    # ── Lines ─────────────────────────────────────────────────────────────
    rubrics = data.get("Rubrics") or data.get("rubrics") or []
    lines = []
    amounts_for_kpi: dict[str, Decimal] = {}    # pcmn_code -> Decimal (monetary, EUR)
    counts_for_kpi: dict[str, Decimal] = {}     # pcmn_code -> Decimal (workers/counts)

    for r in rubrics:
        code = r.get("Code") if r.get("Code") is not None else r.get("code")
        value = r.get("Value") if r.get("Value") is not None else r.get("value")
        period = r.get("Period") if r.get("Period") is not None else (r.get("period") or "N")

        # Only current period
        if code is None or value is None or period not in ("N", None):
            continue
        if period is None:
            period = "N"

        code_str = str(code)
        dtype_raw = str(r.get("DataType") or r.get("dataType") or "")

        # Determine DB data_type
        if code_str in COUNT_CODES or "pure" in dtype_raw or "dec1" in dtype_raw:
            data_type = "met:dec1"
        else:
            data_type = "met:am1"

        try:
            amount = Decimal(str(value).replace(",", "."))
        except (ValueError, TypeError, ArithmeticError):
            continue

        lines.append({
            "pcmn_code":     code_str,
            "amount_period": str(period),
            "data_type":     data_type,
            "amount_eur":    amount,
            "type_amount":   dtype_raw or None,
        })

        # Track for KPI computation (only current period)
        if period == "N":
            if data_type == "met:am1":
                amounts_for_kpi[code_str] = amount
            else:
                counts_for_kpi[code_str] = amount

    # ── Evidence aggregate (KPI computation in EUR millions) ─────────────
    def _first_hit_eur_m(codes: tuple[str, ...]) -> Optional[float]:
        """Try each code in priority order; return first hit as EUR millions."""
        for c in codes:
            v = amounts_for_kpi.get(c)
            if v is not None:
                return float(v / Decimal(1_000_000))
        return None

    def _first_hit_decimal(codes: tuple[str, ...]) -> Optional[Decimal]:
        for c in codes:
            v = amounts_for_kpi.get(c)
            if v is not None:
                return v
        return None

    revenue    = _first_hit_eur_m(KPI_REVENUE)
    ebit       = _first_hit_eur_m(KPI_EBIT)
    net_income = _first_hit_eur_m(KPI_NET_INCOME)

    # Derive EBITDA = EBIT + D&A + Impairments + Provisions
    if ebit is not None:
        depr_total = Decimal(0)
        has_depr = False
        for c in DEPRECIATION_CODES:
            v = amounts_for_kpi.get(c)
            if v is not None:
                depr_total += v
                has_depr = True
        ebitda = ebit + float(depr_total / Decimal(1_000_000)) if has_depr else None
    else:
        ebitda = None

    # Debt aggregates
    fin_lt_raw = _first_hit_decimal(KPI_FIN_LT_DEBT)
    fin_st_raw = _first_hit_decimal(KPI_FIN_ST_DEBT)
    cash_raw   = _first_hit_decimal(KPI_CASH)

    fin_lt = float(fin_lt_raw / Decimal(1_000_000)) if fin_lt_raw is not None else 0.0
    fin_st = float(fin_st_raw / Decimal(1_000_000)) if fin_st_raw is not None else 0.0
    cash   = float(cash_raw   / Decimal(1_000_000)) if cash_raw   is not None else 0.0

    total_debt = (fin_lt + fin_st) if (fin_lt_raw is not None or fin_st_raw is not None) else None
    net_debt   = (total_debt - cash) if total_debt is not None else None

    employees_raw = counts_for_kpi.get("9087")
    employees = int(employees_raw) if employees_raw is not None else None

    evidence_row = {
        "party_id":             str(party_id),
        "period_label":         period_label,
        "period_end":           fy_end,
        "period_type":          "Annual" if period_flag == "normal" else "Stub",
        "revenue_eur_m":        revenue,
        "ebitda_eur_m":         ebitda,
        "ebit_eur_m":           ebit,
        "net_income_eur_m":     net_income,
        "total_assets_eur_m":   _first_hit_eur_m(KPI_TOTAL_ASSETS),
        "total_equity_eur_m":   _first_hit_eur_m(KPI_TOTAL_EQUITY),
        "cash_eur_m":           _first_hit_eur_m(KPI_CASH),
        "total_debt_eur_m":     total_debt,
        "net_debt_eur_m":       net_debt,
        "employees":            employees,
        "amount_currency":      "EUR",
        "fx_rate_to_eur":       1.0,
        "nbb_model_type":       model_type or None,
        "nbb_filing_date":      deposit_date,
        "fiscal_year_start":    fy_start,
        "fiscal_year_end":      fy_end,
        "source_code":          SOURCE_CODE,
        "confidence":           "Confirmed",
        "notes":                None,
    }

    return {
        "filing":   filing_row,
        "lines":    lines,
        "evidence": evidence_row,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Lane B — XBRL XML extraction (from ZIP bulk download)
# ─────────────────────────────────────────────────────────────────────────────

def extract_from_xbrl_xml(
    xbrl_content: str,
    party_id: UUID | str,
    filing_reference: str,
    loaded_by: str = "v4g-ingestion-worker-laneB",
) -> dict:
    """
    Parse one XBRL XML file (bulk download format) into DB-ready dict.

    Args:
        xbrl_content: raw XBRL XML content (string)
        party_id: resolved party_id
        filing_reference: identifier (often from filename or ZIP entry name)
        loaded_by: identifier of process

    Returns:
        Same shape as extract_from_jsonxbrl()
    """
    parsed = parse_xbrl(xbrl_content)

    fy_end = _parse_iso_date(parsed.get("fiscal_end") or "")
    fy_start = _parse_iso_date(parsed.get("fiscal_start") or "")

    if not fy_end:
        raise ValueError(f"XBRL {filing_reference} has no fiscal year end")
    if not fy_start:
        fy_start = date(fy_end.year - 1, fy_end.month, max(fy_end.day, 1))

    period_months = round((fy_end - fy_start).days / 30.4375)
    period_flag = _classify_period(period_months)
    period_label = str(fy_end.year)  # match existing fact_financials pattern; period_flag carries extended/shortened info

    schema = parsed.get("schema", {})
    model_type = schema.get("model", "")  # m01/m02/m03/m04

    filing_row = {
        "party_id":           str(party_id),
        "source_code":        SOURCE_CODE,
        "filing_reference":   filing_reference,
        "deposit_date":       None,  # not in XBRL itself; comes from filename or ZIP meta
        "period_start":       fy_start,
        "period_end":         fy_end,
        "period_months":      period_months,
        "period_flag":        period_flag,
        "period_label":       period_label,
        "nbb_model_type":     model_type or None,
        "nbb_schema_subtype": schema.get("model_label", None),
        "taxonomy_version":   schema.get("version", None),
        "consolidation":      "standalone",
        "enterprise_name":    parsed.get("company") or None,
        "legal_form_code":    None,
        "raw_address":        None,
        "language":           None,
        "currency":           "EUR",
        "loaded_by":          loaded_by,
    }

    # Lines — parser returns data keyed by (xbrl_element, pcmn_code, label, section)
    lines = []
    amounts_for_kpi: dict[str, Decimal] = {}
    counts_for_kpi: dict[str, Decimal] = {}
    for key, val in parsed.get("data", {}).items():
        _xbrl_el, pcmn, _label, section = key

        # Determine data_type — only true counts (9087/9088) get dec1
        if pcmn in COUNT_CODES:
            data_type = "met:dec1"
        else:
            data_type = "met:am1"

        try:
            amount = Decimal(str(val))
        except (ValueError, ArithmeticError):
            continue

        # Skip if pcmn starts with '?' (unknown code from parser fallback)
        if pcmn.startswith("?"):
            continue

        lines.append({
            "pcmn_code":     pcmn,
            "amount_period": "N",
            "data_type":     data_type,
            "amount_eur":    amount,
            "type_amount":   None,
        })

        if data_type == "met:am1":
            amounts_for_kpi[pcmn] = amount
        else:
            counts_for_kpi[pcmn] = amount

    # Reuse same KPI derivation as Lane A (multi-code fallback)
    def _first_hit_eur_m_b(codes: tuple[str, ...]) -> Optional[float]:
        for c in codes:
            v = amounts_for_kpi.get(c)
            if v is not None:
                return float(v / Decimal(1_000_000))
        return None

    def _first_hit_decimal_b(codes: tuple[str, ...]) -> Optional[Decimal]:
        for c in codes:
            v = amounts_for_kpi.get(c)
            if v is not None:
                return v
        return None

    revenue    = _first_hit_eur_m_b(KPI_REVENUE)
    ebit       = _first_hit_eur_m_b(KPI_EBIT)
    net_income = _first_hit_eur_m_b(KPI_NET_INCOME)

    if ebit is not None:
        depr_total = Decimal(0)
        has_depr = False
        for c in DEPRECIATION_CODES:
            v = amounts_for_kpi.get(c)
            if v is not None:
                depr_total += v
                has_depr = True
        ebitda = ebit + float(depr_total / Decimal(1_000_000)) if has_depr else None
    else:
        ebitda = None

    fin_lt_raw = _first_hit_decimal_b(KPI_FIN_LT_DEBT)
    fin_st_raw = _first_hit_decimal_b(KPI_FIN_ST_DEBT)
    cash_raw   = _first_hit_decimal_b(KPI_CASH)

    fin_lt = float(fin_lt_raw / Decimal(1_000_000)) if fin_lt_raw is not None else 0.0
    fin_st = float(fin_st_raw / Decimal(1_000_000)) if fin_st_raw is not None else 0.0
    cash   = float(cash_raw   / Decimal(1_000_000)) if cash_raw   is not None else 0.0

    total_debt = (fin_lt + fin_st) if (fin_lt_raw is not None or fin_st_raw is not None) else None
    net_debt   = (total_debt - cash) if total_debt is not None else None

    employees_raw = counts_for_kpi.get("9087")
    employees = int(employees_raw) if employees_raw is not None else None

    evidence_row = {
        "party_id":            str(party_id),
        "period_label":        period_label,
        "period_end":          fy_end,
        "period_type":         "Annual" if period_flag == "normal" else "Stub",
        "revenue_eur_m":       revenue,
        "ebitda_eur_m":        ebitda,
        "ebit_eur_m":          ebit,
        "net_income_eur_m":    net_income,
        "total_assets_eur_m":  _first_hit_eur_m_b(KPI_TOTAL_ASSETS),
        "total_equity_eur_m":  _first_hit_eur_m_b(KPI_TOTAL_EQUITY),
        "cash_eur_m":          _first_hit_eur_m_b(KPI_CASH),
        "total_debt_eur_m":    total_debt,
        "net_debt_eur_m":      net_debt,
        "employees":           employees,
        "amount_currency":     "EUR",
        "fx_rate_to_eur":      1.0,
        "nbb_model_type":      model_type or None,
        "nbb_filing_date":     None,
        "fiscal_year_start":   fy_start,
        "fiscal_year_end":     fy_end,
        "source_code":         SOURCE_CODE,
        "confidence":          "Confirmed",
        "notes":                None,
    }

    return {
        "filing":   filing_row,
        "lines":    lines,
        "evidence": evidence_row,
    }


__all__ = [
    "extract_from_jsonxbrl",
    "extract_from_xbrl_xml",
    "SOURCE_CODE",
    "COUNT_CODES",
    "KPI_MAPPING",
]


# ─────────────────────────────────────────────────────────────────────────────
# Worker entry point — consumes parse_rubrics output (NOT raw)
# ─────────────────────────────────────────────────────────────────────────────

def extract_filing_and_lines_from_parsed(
    parsed: dict,
    filing_reference: str,
    party_id: UUID | str,
    filing_meta: Optional[dict] = None,
    loaded_by: str = "v4g-ingestion-worker",
) -> tuple[dict, list[dict]]:
    """Build (filing_row, lines[]) from fetcher.parse_rubrics() output.

    Primary integration entry point for the live worker. The worker already
    calls parse_rubrics() — this function reshapes that output for the new
    fact_filings + fact_financials_lines tables.

    Args:
        parsed: dict returned by fetcher.parse_rubrics — must have keys:
                year, fy_start, fy_end, period_months, model_type, amounts,
                filing_date (added in LB-005)
        filing_reference: NBB reference number (from fetch_all_xbrl tuple)
        party_id: resolved party UUID
        filing_meta: optional original filing meta from get_references()
                     — used for legal_form_code, raw_address, language
                     if available. Defaults to None (those fields → NULL).
        loaded_by: identifier of the loading process

    Returns:
        (filing_row, lines) — both dicts ready for DB insert.
        Caller writes filing first (gets filing_id back), then lines.
    """
    fy_end = _parse_iso_date(parsed.get("fy_end") or "")
    fy_start = _parse_iso_date(parsed.get("fy_start") or "")

    if not fy_end:
        raise ValueError(f"Filing {filing_reference} has no fiscal year end")
    if not fy_start:
        fy_start = date(fy_end.year - 1, fy_end.month, max(fy_end.day, 1))

    period_months = int(parsed.get("period_months") or
                        round((fy_end - fy_start).days / 30.4375))
    period_flag = _classify_period(period_months)
    period_label = str(fy_end.year)  # match existing fact_financials pattern; period_flag carries extended/shortened info

    deposit_date = _parse_iso_date(parsed.get("filing_date") or "")
    model_type = (parsed.get("model_type") or "").strip() or None

    enterprise_name = (parsed.get("company_name") or "").strip() or None

    # Optional fields from filing_meta if provided
    fm = filing_meta or {}
    legal_form = (fm.get("LegalForm") or fm.get("legalForm")
                  or fm.get("LegalFormCode") or "").strip() or None
    raw_address = fm.get("Address") or fm.get("address") or None
    language = (fm.get("Language") or fm.get("language") or "").strip() or None
    consolidation = (fm.get("Consolidation") or fm.get("consolidation")
                     or "standalone").strip()

    filing_row = {
        "party_id":           str(party_id),
        "source_code":        SOURCE_CODE,
        "filing_reference":   str(filing_reference),
        "deposit_date":       deposit_date,
        "period_start":       fy_start,
        "period_end":         fy_end,
        "period_months":      period_months,
        "period_flag":        period_flag,
        "period_label":       period_label,
        "nbb_model_type":     model_type,
        "nbb_schema_subtype": None,
        "taxonomy_version":   None,
        "consolidation":      consolidation,
        "enterprise_name":    enterprise_name,
        "legal_form_code":    legal_form,
        "raw_address":        raw_address,
        "language":           language,
        "currency":           "EUR",
        "loaded_by":          loaded_by,
    }

    # Build lines from amounts dict
    # parsed["amounts"] = {"70": 5400000.0, "9901": 720000.0, "_count_9087": True, ...}
    lines = []
    amounts = parsed.get("amounts", {})
    for code, val in amounts.items():
        # Skip internal count-markers
        if code.startswith("_count_"):
            continue

        # Skip if marked as a count via companion _count_{code} key
        is_count = amounts.get(f"_count_{code}") is True or code in COUNT_CODES
        data_type = "met:dec1" if is_count else "met:am1"

        try:
            amount = Decimal(str(val))
        except (ValueError, ArithmeticError, TypeError):
            continue

        lines.append({
            "pcmn_code":     code,
            "amount_period": "N",
            "data_type":     data_type,
            "amount_eur":    amount,
            "type_amount":   None,
        })

    return filing_row, lines


__all__ = __all__ + ["extract_filing_and_lines_from_parsed"]
