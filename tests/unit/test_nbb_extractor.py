"""
Unit tests for NBB extractor.

These tests run offline — no NBB API calls. Fixture is synthetic JSON-XBRL
modeled on AB LENS MOTOR FY2024 filing.

To run live integration test (requires NBB_API_KEY env):
    pytest tests/integration/test_nbb_live.py
"""
from datetime import date
from uuid import uuid4

import pytest

from src.domain.nbb import extract_from_jsonxbrl, SOURCE_CODE


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

CANARY_PARTY_ID = "982d23e1-9f6d-4551-84cf-12dcab4835ce"  # AB LENS MOTOR

# Synthetic JSON-XBRL response — minimal but covers all KPI mappings
SYNTHETIC_JSONXBRL = {
    "EnterpriseName": "AB LENS MOTOR BV",
    "Rubrics": [
        # Revenue (code 70)
        {"Code": "70",     "Value": 5_400_000,  "Period": "N", "DataType": "monetary"},
        # Operating result (9901 = EBIT)
        {"Code": "9901",   "Value": 720_000,    "Period": "N", "DataType": "monetary"},
        # D&A (630)
        {"Code": "630",    "Value": 180_000,    "Period": "N", "DataType": "monetary"},
        # Impairment (631/4)
        {"Code": "631/4",  "Value": 20_000,     "Period": "N", "DataType": "monetary"},
        # Provisions (635/8)
        {"Code": "635/8",  "Value": 30_000,     "Period": "N", "DataType": "monetary"},
        # Net income (9904)
        {"Code": "9904",   "Value": 540_000,    "Period": "N", "DataType": "monetary"},
        # Total assets (20/58)
        {"Code": "20/58",  "Value": 8_200_000,  "Period": "N", "DataType": "monetary"},
        # Equity (10/15)
        {"Code": "10/15",  "Value": 3_500_000,  "Period": "N", "DataType": "monetary"},
        # Cash (50/53)
        {"Code": "50/53",  "Value": 900_000,    "Period": "N", "DataType": "monetary"},
        # Fin LT debt (170/4)
        {"Code": "170/4",  "Value": 1_400_000,  "Period": "N", "DataType": "monetary"},
        # Fin ST debt (42/43)
        {"Code": "42/43",  "Value": 600_000,    "Period": "N", "DataType": "monetary"},
        # Workers (9087) — count, not EUR
        {"Code": "9087",   "Value": 24.5,       "Period": "N", "DataType": "pure"},
        # Previous year (should be filtered out)
        {"Code": "70",     "Value": 4_800_000,  "Period": "N1", "DataType": "monetary"},
    ],
}

SYNTHETIC_FILING_META = {
    "referenceNumber":   "2025-00231176",
    "ExerciseDates":     {"startDate": "2024-01-01", "endDate": "2024-12-31"},
    "DepositDate":       "2025-04-15",
    "ModelType":         "M02",
    "EnterpriseName":    "AB LENS MOTOR BV",
    "LegalForm":         "BV",
    "Language":          "nl",
    "Consolidation":     "standalone",
}


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_extract_returns_three_sections():
    """Extractor output has filing, lines, evidence keys."""
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    assert set(result.keys()) == {"filing", "lines", "evidence"}


def test_filing_row_shape():
    """Filing row has all required fields with correct values."""
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    f = result["filing"]

    assert f["party_id"] == CANARY_PARTY_ID
    assert f["source_code"] == SOURCE_CODE
    assert f["filing_reference"] == "2025-00231176"
    assert f["period_start"] == date(2024, 1, 1)
    assert f["period_end"] == date(2024, 12, 31)
    assert f["period_months"] == 12
    assert f["period_flag"] == "normal"
    assert f["period_label"] == "2024"  # just year string, consistent with existing fact_financials pattern
    assert f["nbb_model_type"] == "M02"
    assert f["deposit_date"] == date(2025, 4, 15)
    assert f["enterprise_name"] == "AB LENS MOTOR BV"
    assert f["legal_form_code"] == "BV"
    assert f["language"] == "nl"
    assert f["currency"] == "EUR"


def test_lines_only_current_period():
    """Lines should only include Period='N' rows (prior year filtered out)."""
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    lines = result["lines"]

    # All current period
    assert all(line["amount_period"] == "N" for line in lines)
    # We had 12 N-period rows in fixture (and 1 N1 to be skipped)
    assert len(lines) == 12


def test_line_data_type_classification():
    """Workers count should be met:dec1, others met:am1."""
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    by_code = {l["pcmn_code"]: l for l in result["lines"]}

    assert by_code["70"]["data_type"] == "met:am1"
    assert by_code["9087"]["data_type"] == "met:dec1"  # workers count


def test_evidence_kpi_aggregates_in_millions():
    """Evidence row computes KPIs in EUR millions."""
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    e = result["evidence"]

    assert e["revenue_eur_m"] == 5.4
    assert e["ebit_eur_m"] == 0.72
    assert e["net_income_eur_m"] == 0.54
    assert e["total_assets_eur_m"] == 8.2
    assert e["total_equity_eur_m"] == 3.5
    assert e["cash_eur_m"] == 0.9
    assert e["employees"] == 24  # int cast


def test_evidence_ebitda_derivation():
    """EBITDA = EBIT (720) + D&A (180) + Impairment (20) + Provisions (30) = 950 → 0.95M."""
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    e = result["evidence"]

    assert e["ebitda_eur_m"] == pytest.approx(0.95, abs=0.001)


def test_evidence_net_debt():
    """Net debt = (170/4 + 42/43) - cash = (1.4 + 0.6) - 0.9 = 1.1M."""
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    e = result["evidence"]

    assert e["total_debt_eur_m"] == pytest.approx(2.0, abs=0.001)
    assert e["net_debt_eur_m"] == pytest.approx(1.1, abs=0.001)


def test_extended_period_flag():
    """Periods >14m flagged as extended in period_flag column (period_label stays as year)."""
    meta = {**SYNTHETIC_FILING_META, "ExerciseDates": {"startDate": "2023-01-01", "endDate": "2024-06-30"}}
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, meta, CANARY_PARTY_ID)
    f = result["filing"]

    assert f["period_months"] == 18
    assert f["period_flag"] == "extended"
    assert f["period_label"] == "2024"  # just year — period_flag carries the nuance


def test_shortened_period_flag():
    """Periods <10m flagged as shortened."""
    meta = {**SYNTHETIC_FILING_META, "ExerciseDates": {"startDate": "2024-04-01", "endDate": "2024-12-31"}}
    result = extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, meta, CANARY_PARTY_ID)
    f = result["filing"]

    assert f["period_months"] == 9
    assert f["period_flag"] == "shortened"


def test_missing_period_end_raises():
    """Should raise ValueError if no fiscal year end is parseable."""
    meta = {**SYNTHETIC_FILING_META, "ExerciseDates": {}}
    with pytest.raises(ValueError, match="fiscal year end"):
        extract_from_jsonxbrl(SYNTHETIC_JSONXBRL, meta, CANARY_PARTY_ID)


# ─────────────────────────────────────────────────────────────────────────────
# Multi-code KPI fallback tests (be-gaap-ci namespace coverage)
# ─────────────────────────────────────────────────────────────────────────────

BE_GAAP_CI_JSONXBRL = {
    "EnterpriseName": "BE-GAAP-CI TEST CO",
    "Rubrics": [
        # be-gaap-ci flavored codes — different MAR codes for same concepts
        {"Code": "70",     "Value": 3_000_000, "Period": "N", "DataType": "monetary"},
        {"Code": "649",    "Value": 450_000,   "Period": "N", "DataType": "monetary"},  # EBIT (be-gaap-ci)
        {"Code": "9904",   "Value": 300_000,   "Period": "N", "DataType": "monetary"},
        {"Code": "20/58",  "Value": 5_000_000, "Period": "N", "DataType": "monetary"},
        {"Code": "10/15",  "Value": 2_000_000, "Period": "N", "DataType": "monetary"},
        {"Code": "54/58",  "Value": 400_000,   "Period": "N", "DataType": "monetary"},  # cash (be-gaap-ci)
        {"Code": "9134",   "Value": 150_000,   "Period": "N", "DataType": "monetary"},  # income tax (be-gaap-ci)
    ],
}


def test_be_gaap_ci_codes_fall_back_to_correct_kpis():
    """When filing uses be-gaap-ci codes (649/54/58/9134), KPIs still populate via fallback."""
    result = extract_from_jsonxbrl(BE_GAAP_CI_JSONXBRL, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    e = result["evidence"]

    # KPIs computed from be-gaap-ci codes
    assert e["revenue_eur_m"] == 3.0
    assert e["ebit_eur_m"] == 0.45         # from 649 (be-gaap-ci) — falls back since 9901 missing
    assert e["net_income_eur_m"] == 0.3
    assert e["total_assets_eur_m"] == 5.0
    assert e["total_equity_eur_m"] == 2.0
    assert e["cash_eur_m"] == 0.4          # from 54/58 (be-gaap-ci) — falls back since 50/53 missing


def test_bas_takes_priority_when_both_present():
    """If both bas (50/53) and be-gaap-ci (54/58) present, bas wins per priority order."""
    mixed = {
        "EnterpriseName": "MIXED",
        "Rubrics": [
            {"Code": "50/53",  "Value": 900_000,  "Period": "N", "DataType": "monetary"},
            {"Code": "54/58",  "Value": 700_000,  "Period": "N", "DataType": "monetary"},
        ],
    }
    result = extract_from_jsonxbrl(mixed, SYNTHETIC_FILING_META, CANARY_PARTY_ID)
    # 50/53 listed first in KPI_CASH, so it wins
    assert result["evidence"]["cash_eur_m"] == 0.9


# ─────────────────────────────────────────────────────────────────────────────
# Tests for extract_filing_and_lines_from_parsed (worker entry point)
# ─────────────────────────────────────────────────────────────────────────────

from src.domain.nbb.extractor import extract_filing_and_lines_from_parsed

SYNTHETIC_PARSED = {
    "year":         "2024",
    "company_name": "AB LENS MOTOR BV",
    "model_type":   "M02",
    "reference":    "2025-00231176",
    "fy_start":     "2024-01-01",
    "fy_end":       "2024-12-31",
    "period_months": 12,
    "filing_date":  "2025-04-15",
    "deposit_type": "regular",
    "amounts": {
        "70":   5_400_000.0,
        "9901":   720_000.0,
        "9904":   540_000.0,
        "9087":      24.5,
        "_count_9087": True,   # internal marker, should be skipped
    },
}


def test_parsed_entry_point_produces_filing_and_lines():
    """extract_filing_and_lines_from_parsed returns (filing_dict, lines_list)."""
    filing, lines = extract_filing_and_lines_from_parsed(
        parsed=SYNTHETIC_PARSED,
        filing_reference="2025-00231176",
        party_id=CANARY_PARTY_ID,
    )
    assert isinstance(filing, dict)
    assert isinstance(lines, list)
    assert filing["filing_reference"] == "2025-00231176"
    assert filing["party_id"] == CANARY_PARTY_ID
    assert filing["period_label"] == "2024"
    assert filing["nbb_model_type"] == "M02"
    assert filing["deposit_date"] == date(2025, 4, 15)


def test_parsed_entry_point_skips_count_markers():
    """Internal _count_* markers in amounts dict should not produce lines."""
    _, lines = extract_filing_and_lines_from_parsed(
        parsed=SYNTHETIC_PARSED,
        filing_reference="2025-00231176",
        party_id=CANARY_PARTY_ID,
    )
    pcmn_codes = [l["pcmn_code"] for l in lines]
    assert "_count_9087" not in pcmn_codes
    assert len(lines) == 4  # 70, 9901, 9904, 9087 (the _count_9087 marker is skipped)


def test_parsed_entry_point_classifies_data_type():
    """Worker counts (9087) get met:dec1, monetary codes get met:am1."""
    _, lines = extract_filing_and_lines_from_parsed(
        parsed=SYNTHETIC_PARSED,
        filing_reference="2025-00231176",
        party_id=CANARY_PARTY_ID,
    )
    by_code = {l["pcmn_code"]: l for l in lines}
    assert by_code["70"]["data_type"] == "met:am1"
    assert by_code["9087"]["data_type"] == "met:dec1"


def test_parsed_entry_point_uses_filing_meta_when_provided():
    """Optional filing_meta enriches filing row with legal_form, language etc."""
    meta = {"LegalForm": "BV", "Language": "nl", "Consolidation": "consolidated"}
    filing, _ = extract_filing_and_lines_from_parsed(
        parsed=SYNTHETIC_PARSED,
        filing_reference="2025-00231176",
        party_id=CANARY_PARTY_ID,
        filing_meta=meta,
    )
    assert filing["legal_form_code"] == "BV"
    assert filing["language"] == "nl"
    assert filing["consolidation"] == "consolidated"


def test_parsed_entry_point_missing_fy_end_raises():
    """Missing fy_end should raise ValueError."""
    bad = {**SYNTHETIC_PARSED, "fy_end": ""}
    with pytest.raises(ValueError, match="fiscal year end"):
        extract_filing_and_lines_from_parsed(
            parsed=bad,
            filing_reference="2025-00231176",
            party_id=CANARY_PARTY_ID,
        )
