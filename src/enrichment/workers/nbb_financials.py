"""NBB financials enrichment worker.

Fetches filings from NBB CBSO, parses them (old or new XBRL format),
aggregates to fact_financials shape, and upserts to Supabase.

Entry point: run_for_party(party_id, kbo)
  - Fetches 10 years of filings for the given KBO
  - For each year, produces an AggregatedYear
  - Converts to FinancialFact models (validated)
  - Writes via FinancialsWriter

Fetcher imports come from src.domain.nbb; they are the verbatim port
of v4g_accounts modules. Old-format XBRL (pre-2021 manual ZIP uploads)
is not yet wired — that path needs a separate upload UI.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any
from uuid import UUID

from src.canonical.financials import FinancialFact
from src.domain.nbb.aggregator import aggregate_year
from src.domain.nbb.fetcher import fetch_all_xbrl
from src.persistence.financials_writer import FinancialsWriter

log = logging.getLogger(__name__)


def run_for_party(
    *,
    party_id: UUID | str,
    kbo: str,
    run_id: UUID | str,
    year_limit: int = 10,
) -> dict[str, Any]:
    """Enrich one party's NBB financials.

    Returns {outcome, rows_written, error, years_processed, kbo}
    """
    try:
        years_data = _fetch_and_parse(kbo, year_limit=year_limit)
    except Exception as e:
        log.exception("fetch_or_parse failed party=%s kbo=%s", party_id, kbo)
        # Write audit row with failure
        writer = FinancialsWriter(run_id=run_id)
        writer.write_facts(party_id=party_id, facts=[])
        return {
            "outcome": "failed",
            "rows_written": 0,
            "error": f"fetch_parse: {type(e).__name__}: {e}",
            "years_processed": 0,
            "kbo": kbo,
        }

    # Aggregate each year → AggregatedYear → FinancialFact
    facts: list[FinancialFact] = []
    for year_info in years_data:
        try:
            agg = aggregate_year(
                year_data=year_info["codes"],
                period_label=year_info["period_label"],
                period_end=year_info.get("period_end"),
                fiscal_year_start=year_info.get("fiscal_year_start"),
                fiscal_year_end=year_info.get("fiscal_year_end"),
                nbb_model_type=year_info.get("model_type"),
                nbb_filing_date=year_info.get("filing_date"),
            )
            row = agg.as_upsert_row(party_id=str(party_id))
            # FinancialFact validates the upsert row shape (types, enums).
            fact = FinancialFact(**row)
            facts.append(fact)
        except Exception:
            log.exception(
                "aggregation failed for party=%s year=%s — skipping year",
                party_id, year_info.get("period_label"),
            )
            continue

    # Upsert
    writer = FinancialsWriter(run_id=run_id)
    result = writer.write_facts(party_id=party_id, facts=facts)

    return {
        "outcome": result["outcome"],
        "rows_written": result["rows_written"],
        "error": result.get("error"),
        "years_processed": len(facts),
        "kbo": kbo,
    }


def _fetch_and_parse(kbo: str, *, year_limit: int) -> list[dict[str, Any]]:
    """Fetch NBB filings and normalize to per-year dicts for the aggregator.

    Each output dict has keys:
      - period_label        (str, e.g. "2024")
      - period_end          (date | None)
      - fiscal_year_start   (date | None)
      - fiscal_year_end     (date | None)
      - codes               (dict[str, float] — PCMN code → EUR amount)
      - model_type          (str | None — "m01"/"m02"/"m03")
      - filing_date         (date | None — not yet populated, reserved)

    Uses only the new-format JSON-XBRL path via fetch_all_xbrl. Old-format
    XBRL (pre-2021 PDF filings parsed from manual ZIP upload) is not wired;
    that's a separate flow with its own UI.

    Raises RuntimeError if NBB_API_KEY is not set. Raises NBBApiError on
    network / auth failures (propagated from fetcher).
    """
    api_key = os.environ.get("NBB_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NBB_API_KEY env var is required for NBB enrichment. "
            "Set it via Render dashboard or .env file."
        )

    xbrl_results = fetch_all_xbrl(kbo, api_key, use_cache=True)

    # xbrl_results is list of (year_str, parsed_dict, ref_num) sorted ASC.
    # Take the most recent `year_limit` entries.
    recent = xbrl_results[-year_limit:] if year_limit > 0 else xbrl_results

    years: list[dict[str, Any]] = []
    for year_str, parsed_dict, _ref_num in recent:
        fy_end   = _parse_iso_date(parsed_dict.get("fy_end"))
        fy_start = _parse_iso_date(parsed_dict.get("fy_start"))

        # Strip internal count-markers from amounts (the `_count_{code}`
        # boolean flags used by staging_builder; aggregator doesn't need them)
        codes = {
            k: v
            for k, v in parsed_dict.get("amounts", {}).items()
            if not k.startswith("_count_")
        }

        years.append({
            "period_label":      year_str,
            "period_end":        fy_end,
            "fiscal_year_start": fy_start,
            "fiscal_year_end":   fy_end,
            "codes":             codes,
            "model_type":        parsed_dict.get("model_type") or None,
            "filing_date":       None,  # future: extract from ref_num/ref_meta
        })

    log.info(
        "fetch_and_parse kbo=%s years=%d (requested_limit=%d)",
        kbo, len(years), year_limit,
    )
    return years


def _parse_iso_date(s: str | None) -> date | None:
    """Parse ISO date string (YYYY-MM-DD...) to date. Null-safe."""
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _date(s: str | None) -> date | None:
    """Backwards-compat alias — kept for any external callers."""
    return _parse_iso_date(s)


__all__ = ["run_for_party"]
