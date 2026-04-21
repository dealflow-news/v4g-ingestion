"""NBB worker glue tests.

Verifies that `_fetch_and_parse` correctly transforms the fetcher's output
into the shape that the aggregator expects. The fetcher itself is mocked
— live NBB API calls are NOT made in CI.

These tests complement test_aggregator.py: those test formula correctness
given clean input; these test that worker glue produces clean input from
realistic fetcher output.

Phase 2.7c addition: test_run_for_party_raises_on_fetch_failure verifies
the Optie A contract (workers raise instead of returning failure dicts).
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from src.enrichment.workers import nbb_financials as worker


@pytest.fixture
def fake_fetch_output() -> list[tuple[str, dict, str]]:
    """Realistic fetch_all_xbrl output — 3 years of filings."""
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


# ─── _fetch_and_parse glue ───────────────────────────────────────────────────

def test_fetch_and_parse_transforms_to_aggregator_shape(
    monkeypatch, fake_fetch_output,
):
    """Worker glue produces the dict shape the aggregator expects."""
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: fake_fetch_output,
    )

    years = worker._fetch_and_parse("0771439317", year_limit=10)

    assert len(years) == 3
    assert [y["period_label"] for y in years] == ["2022", "2023", "2024"]


def test_fetch_and_parse_populates_period_metadata(monkeypatch, fake_fetch_output):
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

    assert y2022["codes"]["70"]   == 10_000_000
    assert y2022["codes"]["9087"] == 12.5
    assert "_count_9087" not in y2022["codes"]


def test_fetch_and_parse_honors_year_limit(monkeypatch, fake_fetch_output):
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
    """Missing fy_start/fy_end → dates become None, year still processes."""
    result = [
        (
            "2020",
            {
                "year": "2020",
                "company_name": "OLD_CO",
                "model_type": "m01",
                "reference": "ABC_2020",
                "fy_start": "",
                "fy_end": "",
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


# ─── Phase 2.7c: Optie A (raise on failure) ─────────────────────────────────

FAKE_PARTY_ID = UUID("87f123ef-64e0-463a-b79c-ad4c0bef2855")
FAKE_RUN_ID   = UUID("11111111-1111-1111-1111-111111111111")


def test_run_for_party_raises_on_fetch_failure(monkeypatch):
    """Optie A: fetch failure → exception propagates up to runner.

    The pre-2.7c worker would catch the exception and return
    {"outcome": "failed", ...}. Phase 2.7c removes that path entirely:
    any failure in fetch/parse/write becomes a raised exception.
    """
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")

    def explode(kbo, api_key, use_cache=True):
        raise RuntimeError("NBB API timeout")
    monkeypatch.setattr(worker, "fetch_all_xbrl", explode)

    with pytest.raises(RuntimeError, match="NBB API timeout"):
        worker.run_for_party(
            party_id=FAKE_PARTY_ID,
            kbo="0771439317",
            run_id=FAKE_RUN_ID,
        )


def test_run_for_party_raises_on_writer_failure(monkeypatch, fake_fetch_output):
    """Optie A: writer failure → exception propagates up to runner."""
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: fake_fetch_output,
    )

    fake_writer = MagicMock()
    fake_writer.write_facts.side_effect = ConnectionError("Supabase unreachable")
    monkeypatch.setattr(
        worker, "FinancialsWriter",
        MagicMock(return_value=fake_writer),
    )

    with pytest.raises(ConnectionError, match="Supabase unreachable"):
        worker.run_for_party(
            party_id=FAKE_PARTY_ID,
            kbo="0771439317",
            run_id=FAKE_RUN_ID,
        )


def test_run_for_party_returns_summary_on_success(monkeypatch, fake_fetch_output):
    """Happy path returns {rows_written, years_processed, kbo}."""
    monkeypatch.setenv("NBB_API_KEY", "fake-test-key")
    monkeypatch.setattr(
        worker, "fetch_all_xbrl",
        lambda kbo, api_key, use_cache=True: fake_fetch_output,
    )

    fake_writer = MagicMock()
    fake_writer.write_facts.return_value = 3
    monkeypatch.setattr(
        worker, "FinancialsWriter",
        MagicMock(return_value=fake_writer),
    )

    result = worker.run_for_party(
        party_id=FAKE_PARTY_ID,
        kbo="0771439317",
        run_id=FAKE_RUN_ID,
    )

    assert result == {
        "rows_written": 3,
        "years_processed": 3,
        "kbo": "0771439317",
    }
    fake_writer.write_facts.assert_called_once()
