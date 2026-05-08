"""Unit tests for src.services.financial_export pure helpers.

Database-touching functions (get_financial_history, build_workbook with
real data) are exercised by integration tests / smoke runs against a
canary party (e.g. AB LENS MOTOR). Those run separately and are not
part of the unit-test fast loop.
"""
from __future__ import annotations

from src.services.financial_export import (
    _dedupe_prefer_nbb,
    suggest_filename,
)


# ─── _dedupe_prefer_nbb ───────────────────────────────────────────────────
def test_dedupe_empty_list_returns_empty():
    assert _dedupe_prefer_nbb([]) == []


def test_dedupe_single_row_passes_through():
    rows = [{"period_label": "2024", "period_end": "2024-12-31",
             "source_code": "SRC_NBB", "revenue_eur_m": 10}]
    assert _dedupe_prefer_nbb(rows) == rows


def test_dedupe_distinct_periods_all_kept():
    rows = [
        {"period_label": "2024", "period_end": "2024-12-31", "source_code": "SRC_NBB"},
        {"period_label": "2023", "period_end": "2023-12-31", "source_code": "SRC_PB"},
        {"period_label": "2022", "period_end": "2022-12-31", "source_code": "SRC_NBB"},
    ]
    out = _dedupe_prefer_nbb(rows)
    assert len(out) == 3
    assert {r["period_label"] for r in out} == {"2024", "2023", "2022"}


def test_dedupe_nbb_wins_over_pb_for_same_period():
    rows = [
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_PB", "revenue_eur_m": 1.0},
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_NBB", "revenue_eur_m": 47.9},
    ]
    out = _dedupe_prefer_nbb(rows)
    assert len(out) == 1
    assert out[0]["source_code"] == "SRC_NBB"
    assert out[0]["revenue_eur_m"] == 47.9


def test_dedupe_nbb_wins_regardless_of_input_order():
    """NBB-first or PB-first: NBB always wins."""
    rows_nbb_first = [
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_NBB", "revenue_eur_m": 47.9},
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_PB", "revenue_eur_m": 1.0},
    ]
    rows_pb_first = list(reversed(rows_nbb_first))
    assert _dedupe_prefer_nbb(rows_nbb_first)[0]["source_code"] == "SRC_NBB"
    assert _dedupe_prefer_nbb(rows_pb_first)[0]["source_code"] == "SRC_NBB"


def test_dedupe_idempotent():
    """Running dedupe twice yields the same result."""
    rows = [
        {"period_label": "2024", "period_end": "2024-12-31", "source_code": "SRC_NBB"},
        {"period_label": "2024", "period_end": "2024-12-31", "source_code": "SRC_PB"},
        {"period_label": "2023", "period_end": "2023-12-31", "source_code": "SRC_PB"},
    ]
    once = _dedupe_prefer_nbb(rows)
    twice = _dedupe_prefer_nbb(once)
    assert once == twice


def test_dedupe_falls_back_to_period_end_alone_if_no_label():
    """If period_label missing, period_end alone forms the key."""
    rows = [
        {"period_label": None, "period_end": "2024-12-31", "source_code": "SRC_PB"},
        {"period_label": None, "period_end": "2024-12-31", "source_code": "SRC_NBB"},
    ]
    out = _dedupe_prefer_nbb(rows)
    assert len(out) == 1
    assert out[0]["source_code"] == "SRC_NBB"


def test_dedupe_two_pb_rows_first_one_wins():
    """When neither is NBB, first-seen wins (stable behavior)."""
    rows = [
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_PB", "revenue_eur_m": 1.0},
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_PB", "revenue_eur_m": 2.0},
    ]
    out = _dedupe_prefer_nbb(rows)
    assert len(out) == 1
    assert out[0]["revenue_eur_m"] == 1.0


# ─── Belgian extended/shortened fiscal year scenarios ────────────────────
def test_dedupe_extended_fy_same_label_different_period_end_both_kept():
    """Verlengd boekjaar: company changes FY-end June→December.

    Two NBB filings legitimately share period_label='2018' but have
    different period_end dates (regular June FY + 6-month transition).
    Both rows must be preserved — they describe different periods.
    """
    rows = [
        {"period_label": "2018", "period_end": "2018-06-30",
         "source_code": "SRC_NBB", "revenue_eur_m": 10.0},
        {"period_label": "2018", "period_end": "2018-12-31",
         "source_code": "SRC_NBB", "revenue_eur_m": 5.5},
    ]
    out = _dedupe_prefer_nbb(rows)
    assert len(out) == 2, "Both extended-FY rows must be kept"


def test_dedupe_cross_source_label_collision_different_period_end_both_kept():
    """Cross-source: NBB has true verlengd FY, PB approximates as calendar.

    These describe genuinely different facts (true 18-month period vs
    calendar-year approximation), so NEITHER should win — both stay.
    """
    rows = [
        {"period_label": "2018", "period_end": "2018-06-30",
         "source_code": "SRC_NBB", "revenue_eur_m": 15.0},
        {"period_label": "2018", "period_end": "2018-12-31",
         "source_code": "SRC_PB", "revenue_eur_m": 12.0},
    ]
    out = _dedupe_prefer_nbb(rows)
    assert len(out) == 2
    sources = sorted(r["source_code"] for r in out)
    assert sources == ["SRC_NBB", "SRC_PB"]


def test_dedupe_same_label_same_end_different_source_collapses_to_nbb():
    """Same exact period (label + end), different sources: NBB wins.

    This is the regular conflict case: both sources report the same
    fiscal period 2024 ending Dec 31. NBB authoritative → drop PB.
    """
    rows = [
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_PB"},
        {"period_label": "2024", "period_end": "2024-12-31",
         "source_code": "SRC_NBB"},
    ]
    out = _dedupe_prefer_nbb(rows)
    assert len(out) == 1
    assert out[0]["source_code"] == "SRC_NBB"


# ─── suggest_filename ─────────────────────────────────────────────────────
def test_suggest_filename_no_meta():
    assert suggest_filename(None) == "financials.xlsx"
    # Empty dict still goes through the fallback chain → "(unknown party)"
    assert suggest_filename({}) == "V4G_unknown_party_financials.xlsx"


def test_suggest_filename_with_display_name_and_kbo():
    meta = {"display_name": "AB Lens Motor", "kbo_nr": "0401452019"}
    assert suggest_filename(meta) == "V4G_AB_Lens_Motor_0401452019_financials.xlsx"


def test_suggest_filename_falls_back_to_legal_name():
    meta = {"legal_name": "Naamloze Vennootschap X", "kbo_nr": "0123456789"}
    out = suggest_filename(meta)
    assert "Naamloze_Vennootschap_X" in out
    assert "0123456789" in out


def test_suggest_filename_strips_special_chars():
    """Bracket, slash, ampersand etc. become underscores; collapsed at edges."""
    meta = {"display_name": "Smith & Co (Holdings) / BVBA", "kbo_nr": "0111222333"}
    out = suggest_filename(meta)
    # No literal '&', '(', ')', '/', or spaces in filename
    for bad in (" ", "&", "(", ")", "/"):
        assert bad not in out, f"{bad!r} leaked into filename: {out}"
    assert out.endswith(".xlsx")


def test_suggest_filename_no_kbo():
    meta = {"display_name": "Mystery Co"}
    assert suggest_filename(meta) == "V4G_Mystery_Co_financials.xlsx"


def test_suggest_filename_long_name_truncated():
    """Display names over 40 chars get truncated to keep filename sane."""
    meta = {"display_name": "X" * 100, "kbo_nr": "0123456789"}
    out = suggest_filename(meta)
    # Body between 'V4G_' and '_<kbo>_financials.xlsx' should be ≤ 40 chars
    body = out.removeprefix("V4G_").split("_0123456789")[0]
    assert len(body) <= 40
