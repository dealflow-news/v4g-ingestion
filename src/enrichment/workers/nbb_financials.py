"""NBB financials enrichment worker.

Phase 2.7c contract (Optie A): worker raises on any unrecoverable error.
Runner catches and maps to outcome='failed'. No more {"outcome": "failed"}
return values.

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

    Raises:
        RuntimeError: NBB_API_KEY missing
        NBBApiError: network / auth / data failure during fetch
        Exception:   any other unrecoverable error in fetch/parse/write

    Returns (only on success):
        {"rows_written": int, "years_processed": int, "kbo": str}
    """
    # Step 1: fetch + parse — let exceptions propagate.
    years_data = _fetch_and_parse(kbo, year_limit=year_limit)

    # Step 2: aggregate per year. Per-year aggregation errors are isolated
    # (one bad year shouldn't lose the whole run), but they are LOGGED so
    # ops can spot taxonomy gaps.
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
            fact = FinancialFact(**row)
            facts.append(fact)
        except Exception:
            log.exception(
                "aggregation failed for party=%s year=%s — skipping year",
                party_id, year_info.get("period_label"),
            )
            continue

    # Step 3: upsert. Writer raises on failure; we let it propagate.
    writer = FinancialsWriter(run_id=run_id)
    rows_written = writer.write_facts(party_id=party_id, facts=facts)

    return {
        "rows_written": rows_written,
        "years_processed": len(facts),
        "kbo": kbo,
    }


def _fetch_and_parse(kbo: str, *, year_limit: int) -> list[dict[str, Any]]:
    """Fetch NBB filings and normalize to per-year dicts for the aggregator.

    Each output dict has keys:
      - period_label, period_end, fiscal_year_start, fiscal_year_end
      - codes (dict[str, float] — PCMN code → EUR amount)
      - model_type, filing_date

    Raises RuntimeError if NBB_API_KEY is not set.
    Raises NBBApiError on network / auth failures (propagated from fetcher).
    """
    api_key = os.environ.get("NBB_API_KEY")
    if not api_key:
        raise RuntimeError(
            "NBB_API_KEY env var is required for NBB enrichment. "
            "Set it via Render dashboard or .env file."
        )

    xbrl_results = fetch_all_xbrl(kbo, api_key, use_cache=True)
    recent = xbrl_results[-year_limit:] if year_limit > 0 else xbrl_results

    years: list[dict[str, Any]] = []
    for year_str, parsed_dict, _ref_num in recent:
        fy_end   = _parse_iso_date(parsed_dict.get("fy_end"))
        fy_start = _parse_iso_date(parsed_dict.get("fy_start"))

        # Strip internal count-markers from amounts
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


__all__ = ["run_for_party"]
