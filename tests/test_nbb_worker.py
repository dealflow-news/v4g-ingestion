"""NBB worker glue tests.

Verifies that `_fetch_and_parse` correctly transforms the fetcher's output
into the shape that the aggregator expects. The fetcher itself is mocked
— live NBB API calls are NOT made in CI.

These tests complement test_aggregator.py: those test formula correctness
given clean input; these test that worker glue produces clean input from
realistic fetcher output.
"""
from __future__ import annotations

from datetime import date

import pytest

from src.enrichment.workers import nbb_financials as worker


@pytest.fixture
def fake_fetch_output() -> list[tuple[str, dict, str]]:
    """Realistic fetch_all_xbrl output — 3 years of filings.

    Shape mirrors parse_rubrics() return: (year_str, parsed_dict, ref_num).
    parsed_dict has year, company_name, model_type, fy_start, fy_end,
    period_months, amounts (with optional _count_* markers).
    """
    return [
        (
            "2022",
            {
                "year": "2022",
                "company_name": "VELA GROUP",
                "model_type": "m02",
                "reference": "VELA_12345_2022",
                "fy_start": "2022-01-01",
                "fy_end": "2022-12-31",
                "period_months": 12,
                "amounts": {
                    "70": 10_000_000,
                    "9901": 1_500_000,
                    "9087": 12.5,
                    "_count_9087": True,  # internal marker, must be stripped
                },
            },
            "VELA_12345_2022",
        ),
        (
            "2023",
            {
                "year": "2023",
                "company_name": "VELA GROUP",
                "model_type": "m02",
                "reference": "VELA_12345_2023",
                "fy_start": "2023-01-01",
                "fy_end": "2023-12-31",
                "period_months": 12,
                "amounts": {
                    "70": 12_000_000,
                    "9901": 2_000_000,
                    "9087": 15.0,
                },
            },
            "VELA_12345_2023",
        ),
        (
            "2024",
            {
                "year": "2024",
                "company_name": "VELA GROUP",
                "model_type": "m02",
                "reference": "VELA_12345_2024",
                "fy_start": "2024-01-01",
                "fy_end": "2024-12-31",
                "period_months": 12,
                "amounts": {"70": 14_000_000, "9901": 2_500_000},
            },
            "VELA_12345_2024",
        ),
    ]


def test_fetch_and_parse_transforms_to_aggregator_shape(
    monkeypatch,
    fake_fetch_output,
):
    """Worker glue produces the dict shape the aggregator expects."""
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: fake_fetch_output,
    )

    years = worker._fetch_and_parse("0771439317", year_limit=10)

    assert len(years) == 3
    # Ordering preserved (fetcher returns ASC, worker keeps that)
    assert [y["period_label"] for y in years] == ["2022", "2023", "2024"]


def test_fetch_and_parse_populates_period_metadata(
    monkeypatch,
    fake_fetch_output,
):
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: fake_fetch_output,
    )

    years = worker._fetch_and_parse("0771439317", year_limit=10)
    y2023 = years[1]

    assert y2023["period_end"]        == date(2023, 12, 31)
    assert y2023["fiscal_year_start"] == date(2023, 1, 1)
    assert y2023["fiscal_year_end"]   == date(2023, 12, 31)
    assert y2023["model_type"]        == "m02"


def test_fetch_and_parse_strips_count_markers(monkeypatch, fake_fetch_output):
    """Internal _count_XXXX booleans must not leak to aggregator."""
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: fake_fetch_output,
    )

    years = worker._fetch_and_parse("0771439317", year_limit=10)
    y2022 = years[0]

    # Real PCMN codes present
    assert y2022["codes"]["70"]   == 10_000_000
    assert y2022["codes"]["9087"] == 12.5
    # Internal marker is stripped
    assert "_count_9087" not in y2022["codes"]


def test_fetch_and_parse_honors_year_limit(monkeypatch, fake_fetch_output):
    """year_limit=2 should return only the last 2 entries."""
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: fake_fetch_output,
    )

    years = worker._fetch_and_parse("0771439317", year_limit=2)

    assert len(years) == 2
    assert [y["period_label"] for y in years] == ["2023", "2024"]


def test_fetch_and_parse_raises_without_api_key(monkeypatch):
    """Clear error when env var is missing — no cryptic downstream failures."""
    monkeypatch.delenv("NBB_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NBB_API_KEY"):
        worker._fetch_and_parse("0771439317", year_limit=10)


def test_fetch_and_parse_tolerates_missing_fiscal_dates(monkeypatch):
    """If NBB returns a filing without fy_start/fy_end, dates become None
    but the year still processes (defense in depth)."""
    result = [
        (
            "2020",
            {
                "year": "2020",
                "company_name": "OLD_CO",
                "model_type": "m01",
                "reference": "ABC_2020",
                "fy_start": "",  # empty!
                "fy_end": "",    # empty!
                "period_months": 12,
                "amounts": {"70": 500_000},
            },
            "ABC_2020",
        )
    ]
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: result,
    )

    years = worker._fetch_and_parse("0459499688", year_limit=10)

    assert len(years) == 1
    assert years[0]["period_end"]        is None
    assert years[0]["fiscal_year_start"] is None
    assert years[0]["codes"]             == {"70": 500_000}
