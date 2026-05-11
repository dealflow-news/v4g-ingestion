"""NBB financials enrichment worker.

Phase 2.7c contract (Optie A): worker raises on any unrecoverable error.
Runner catches and maps to outcome='failed'. No more {"outcome": "failed"}
return values.

W8-worker extension (2026-05-11): triple-write per filing.
  Each year processed produces THREE database writes:
    1. fact_filings           (one row per filing; via writer.write_filing)
    2. fact_financials_lines  (one row per PCMN code; via writer.write_lines)
    3. fact_financials        (one row per period — via writer.write_facts;
                               flows through INSTEAD OF trigger to
                               fact_financials_evidence)

  Per-year errors are isolated: a failure in any of the three writes for
  one year is logged and the loop continues with the next year. The bulk
  fact_financials write at the end handles all collected FinancialFact
  rows in one upsert.

The writer still has its own object_log row (single source of audit truth
at the data-write level). Worker raises → no object_log row written →
runner records the failure on the queue (last_error) and run_log
(tasks_failed). Operationally: failed runs leave a run_log entry with
tasks_failed=1 and the queue row's last_error has the exception text.

Entry point: run_for_party(*, party_id, kbo, run_id, year_limit=10)
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Any
from uuid import UUID

from src.canonical.financials import FilingRecord, FinancialFact, FinancialLine
from src.domain.nbb.aggregator import aggregate_year
from src.domain.nbb.extractor import extract_filing_and_lines_from_parsed
from src.domain.nbb.fetcher import fetch_all_xbrl, get_references
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

    Raises:
        RuntimeError: NBB_API_KEY missing
        NBBApiError: network / auth / data failure during fetch
        Exception:   any other unrecoverable error in fetch/parse/write

    Returns (only on success):
        {
            "rows_written":    int,   # fact_financials upserts (facts)
            "years_processed": int,   # successful FinancialFact rows
            "filings_written": int,   # successful fact_filings rows
            "lines_total":     int,   # total fact_financials_lines rows
            "kbo":             str,
        }
    """
    # Step 1: fetch + parse — let exceptions propagate.
    years_data = _fetch_and_parse(kbo, year_limit=year_limit)

    # Step 2: per-year aggregate + extract + write. Per-year errors isolated.
    writer = FinancialsWriter(run_id=run_id)
    facts: list[FinancialFact] = []
    filings_written = 0
    lines_total = 0

    for year_info in years_data:
        try:
            # 2a. Existing: aggregate per-year codes → FinancialFact
            #     (will land in fact_financials_evidence via INSTEAD OF trigger
            #     when writer.write_facts is called at the end of the loop)
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
            fact = FinancialFact(**row)
            facts.append(fact)

            # 2b. W8-worker: extract filing + lines from parsed dict
            filing_dict, lines_dicts = extract_filing_and_lines_from_parsed(
                parsed=year_info["parsed_dict"],
                filing_reference=year_info["ref_num"],
                party_id=party_id,
                filing_meta=year_info.get("filing_meta"),
            )
            filing = FilingRecord(**filing_dict)
            lines = [FinancialLine(**ln) for ln in lines_dicts]

            # 2c. W8-worker: write filing → lines (FK ordering matters)
            filing_id = writer.write_filing(filing)
            line_count = writer.write_lines(filing_id, lines)
            filings_written += 1
            lines_total += line_count

        except Exception:
            log.exception(
                "year processing failed for party=%s year=%s — skipping year",
                party_id, year_info.get("period_label"),
            )
            continue

    # Step 3: bulk upsert collected facts (existing path; the writer's
    # change_summary picks up filings_written + lines_total from earlier).
    rows_written = writer.write_facts(party_id=party_id, facts=facts)

    return {
        "rows_written":    rows_written,
        "years_processed": len(facts),
        "filings_written": filings_written,
        "lines_total":     lines_total,
        "kbo":             kbo,
    }


def _fetch_and_parse(kbo: str, *, year_limit: int) -> list[dict[str, Any]]:
    """Fetch NBB filings and normalize to per-year dicts for aggregator + extractor.

    Each output dict has keys:
      - period_label, period_end, fiscal_year_start, fiscal_year_end
      - codes (dict[str, float] — PCMN code → EUR amount; aggregator input)
      - model_type, filing_date
      - ref_num (NBB filing reference; extractor input)
      - parsed_dict (raw parse_rubrics output; extractor input)
      - filing_meta (entry from get_references; extractor input, may be None)

    Raises:
        RuntimeError: NBB_API_KEY not set
        NBBApiError: network / auth failures (propagated from fetcher)
    """
    api_key = os.environ.get("NBB_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NBB_API_KEY env var is required for NBB enrichment. "
            "Set it via Render dashboard or .env file."
        )

    xbrl_results = fetch_all_xbrl(kbo, api_key, use_cache=True)
    recent = xbrl_results[-year_limit:] if year_limit > 0 else xbrl_results

    # W8-worker: fetch references once to get filing_meta per ref_num.
    # Used by the extractor for legal_form_code, language, raw_address.
    # Best-effort: if get_references fails, proceed with empty meta (those
    # fields land as NULL — non-blocking).
    try:
        ref_list = get_references(kbo, api_key)
        ref_meta_map = {r["referenceNumber"]: r for r in ref_list}
    except Exception:
        log.exception("get_references failed for kbo=%s; proceeding with empty meta", kbo)
        ref_meta_map = {}

    years: list[dict[str, Any]] = []
    for year_str, parsed_dict, ref_num in recent:
        fy_end   = _parse_iso_date(parsed_dict.get("fy_end"))
        fy_start = _parse_iso_date(parsed_dict.get("fy_start"))

        # Strip internal count-markers from amounts (for aggregator)
        codes = {
            k: v
            for k, v in parsed_dict.get("amounts", {}).items()
            if not k.startswith("_count_")
        }

        years.append({
            # aggregator inputs (existing)
            "period_label":      year_str,
            "period_end":        fy_end,
            "fiscal_year_start": fy_start,
            "fiscal_year_end":   fy_end,
            "codes":             codes,
            "model_type":        parsed_dict.get("model_type") or None,
            "filing_date":       _parse_iso_date(parsed_dict.get("filing_date")),
            # extractor inputs (W8-worker)
            "ref_num":           ref_num,
            "parsed_dict":       parsed_dict,
            "filing_meta":       ref_meta_map.get(ref_num),
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


__all__ = ["run_for_party"]
