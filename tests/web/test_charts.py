"""Tests for src.web.charts — pure SVG-rendering functions."""
from __future__ import annotations

import re

from src.web.charts import revenue_ebitda_svg


# ─── Empty / fallback states ─────────────────────────────────────────────
def test_empty_rows_returns_placeholder_svg():
    svg = revenue_ebitda_svg([])
    assert svg.startswith("<svg")
    assert "Geen financial data" in svg


def test_rows_without_period_end_returns_placeholder():
    rows = [{"revenue_eur_m": 10}, {"revenue_eur_m": 20}]
    svg = revenue_ebitda_svg(rows)
    assert "Geen gedateerde periodes" in svg


def test_rows_with_only_null_metrics_returns_placeholder():
    rows = [
        {"period_end": "2024-12-31", "revenue_eur_m": None, "ebitda_eur_m": None},
        {"period_end": "2023-12-31", "revenue_eur_m": None, "ebitda_eur_m": None},
    ]
    svg = revenue_ebitda_svg(rows)
    assert "Geen revenue of EBITDA data" in svg


# ─── Single data point ────────────────────────────────────────────────────
def test_single_year_renders_centered_point():
    rows = [{
        "period_end": "2024-12-31", "period_label": "2024",
        "revenue_eur_m": 47.9, "ebitda_eur_m": 2.0,
    }]
    svg = revenue_ebitda_svg(rows)
    assert "<polyline" in svg
    assert "2024" in svg


# ─── Multi-year baseline (matches AB LENS MOTOR shape) ───────────────────
def _make_canary_rows():
    """19-year shape mirroring AB LENS MOTOR's actual data."""
    rows = []
    revenues = [11.5, 12.1, 16.1, 14.6, 16.9, 26.7, 16.0, 15.7, 17.3, 20.1,
                20.2, 25.3, 28.9, 37.7, 29.4, 36.1, 40.5, 45.4, 47.9]
    ebitdas = [0.29, 0.35, 0.02, 0.38, 0.45, 0.31, -0.78, 0.11, -0.80, -0.17,
               0.27, 0.66, 0.65, 1.39, 1.03, 1.97, 1.35, 1.53, 2.06]
    for i, year in enumerate(range(2006, 2025)):
        rows.append({
            "period_end": f"{year}-12-31",
            "period_label": str(year),
            "revenue_eur_m": revenues[i],
            "ebitda_eur_m": ebitdas[i],
        })
    return rows


def test_canary_rows_render_19_revenue_dots():
    rows = _make_canary_rows()
    svg = revenue_ebitda_svg(rows)
    revenue_dots = re.findall(r'<circle[^>]*r="3"[^>]*fill="#E8A020"', svg)
    assert len(revenue_dots) == 19


def test_canary_rows_render_19_ebitda_dots():
    rows = _make_canary_rows()
    svg = revenue_ebitda_svg(rows)
    ebitda_dots = re.findall(r'<circle[^>]*r="3"[^>]*fill="#1E2D45"', svg)
    assert len(ebitda_dots) == 19


def test_canary_rows_show_first_and_last_year_labels():
    rows = _make_canary_rows()
    svg = revenue_ebitda_svg(rows)
    assert ">2006</text>" in svg
    assert ">2024</text>" in svg


def test_canary_rows_negative_ebitda_triggers_zero_line():
    """AB LENS MOTOR has negative EBITDA in 2012/2014/2015 → zero line shown."""
    rows = _make_canary_rows()
    svg = revenue_ebitda_svg(rows)
    assert "stroke-dasharray=\"3,3\"" in svg


def test_canary_rows_have_legend():
    rows = _make_canary_rows()
    svg = revenue_ebitda_svg(rows)
    assert ">Revenue</text>" in svg
    assert ">EBITDA</text>" in svg


# ─── NULL handling ────────────────────────────────────────────────────────
def test_null_revenue_creates_gap_in_revenue_line_only():
    rows = [
        {"period_end": "2022-12-31", "period_label": "2022",
         "revenue_eur_m": 40.0, "ebitda_eur_m": 1.5},
        {"period_end": "2023-12-31", "period_label": "2023",
         "revenue_eur_m": None, "ebitda_eur_m": 1.8},
        {"period_end": "2024-12-31", "period_label": "2024",
         "revenue_eur_m": 48.0, "ebitda_eur_m": 2.0},
    ]
    svg = revenue_ebitda_svg(rows)
    revenue_dots = re.findall(r'<circle[^>]*r="3"[^>]*fill="#E8A020"', svg)
    ebitda_dots = re.findall(r'<circle[^>]*r="3"[^>]*fill="#1E2D45"', svg)
    assert len(revenue_dots) == 2, "NULL revenue → 1 fewer revenue dot"
    assert len(ebitda_dots) == 3, "EBITDA unaffected by NULL revenue"


# ─── Negative EBITDA without negative revenue ─────────────────────────────
def test_negative_ebitda_only_triggers_zero_line():
    rows = [
        {"period_end": "2024-12-31", "period_label": "2024",
         "revenue_eur_m": 10.0, "ebitda_eur_m": -2.0},
    ]
    svg = revenue_ebitda_svg(rows)
    assert "stroke-dasharray=\"3,3\"" in svg


def test_all_positive_no_zero_line():
    rows = [
        {"period_end": "2023-12-31", "period_label": "2023",
         "revenue_eur_m": 30.0, "ebitda_eur_m": 1.5},
        {"period_end": "2024-12-31", "period_label": "2024",
         "revenue_eur_m": 47.9, "ebitda_eur_m": 2.0},
    ]
    svg = revenue_ebitda_svg(rows)
    # Zero line is the only dashed line
    assert "stroke-dasharray=\"3,3\"" not in svg


# ─── Output validity ──────────────────────────────────────────────────────
def test_svg_is_well_formed_root_tag():
    rows = _make_canary_rows()
    svg = revenue_ebitda_svg(rows)
    assert svg.lstrip().startswith("<svg ")
    assert svg.rstrip().endswith("</svg>")


def test_custom_dimensions_passed_through():
    rows = _make_canary_rows()
    svg = revenue_ebitda_svg(rows, width=1000, height=400)
    assert 'viewBox="0 0 1000 400"' in svg
