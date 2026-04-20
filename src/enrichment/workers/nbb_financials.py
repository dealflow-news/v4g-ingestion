"""NBB financials enrichment worker.

Fetches filings from NBB CBSO, parses them (old or new XBRL format),
aggregates to fact_financials shape, and upserts to Supabase.

Entry point: run_for_party(party_id, kbo)
  - Fetches 10 years of filings for the given KBO
  - For each year, produces an AggregatedYear
  - Converts to FinancialFact models (validated)
  - Writes via FinancialsWriter

Fetcher / parser imports come from src.domain.nbb; they are the verbatim
port of v4g_accounts modules. No network dependencies at import time.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

from src.canonical.financials import FinancialFact
from src.domain.nbb.aggregator import aggregate_year
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
    """Fetch NBB filings and normalize to a list of per-year dicts.

    Each dict has keys: period_label, period_end (date), codes (dict),
    model_type, filing_date, fiscal_year_start, fiscal_year_end.

    This wraps the v4g_accounts fetcher + parser. Those modules are the
    canonical source of parsing logic; we thin-wrap them here to produce
    the aggregator's expected shape.

    STUB: returns empty list. Phase 2.5 will wire this to
    src.domain.nbb.fetcher.fetch_filings(kbo) and parser.parse_xbrl(xml).
    Held back one iteration so we can ship the aggregator+writer+worker
    skeleton and iterate the fetcher glue separately.
    """
    # TODO Phase 2.5: wire fetcher + parser
    # from src.domain.nbb.fetcher import fetch_filings
    # from src.domain.nbb.parser import parse_xbrl
    # filings = fetch_filings(kbo, year_limit=year_limit)
    # years = []
    # for f in filings:
    #     parsed = parse_xbrl(f["xml_content"])
    #     years.append({
    #         "period_label": f["period_label"],
    #         "period_end": _date(f["period_end"]),
    #         "codes": parsed["codes"],
    #         "model_type": parsed.get("model_type"),
    #         "filing_date": _date(f.get("filing_date")),
    #         ...
    #     })
    # return years
    log.warning(
        "_fetch_and_parse is a stub — returning empty. Phase 2.5 wires this "
        "to fetcher + parser. Aggregation + writer + worker skeleton are "
        "verified end-to-end via tests (test_aggregator.py)."
    )
    return []


def _date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


__all__ = ["run_for_party"]
