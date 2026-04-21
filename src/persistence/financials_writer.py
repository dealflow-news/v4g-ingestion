"""fact_financials writer + audit trail to gs_enrichment.object_log.

Phase 2.7c contract (Optie A): writer raises on database failure. Returns
rows_written count on success. The runner (via worker exception) records
the failure on queue.last_error and run_log.tasks_failed; no silent
failure swallowing here.

Audit trail policy:
- Successful upserts (including no-op of empty list): one object_log row,
  outcome = 'ok' (rows_written > 0) or 'no_change' (rows_written = 0).
- Failed upserts: NO object_log row written (we can't reliably write one
  if the connection or schema is broken). The failure is captured on the
  queue row and run_log.

Writes are idempotent: the (party_id, period_label, source_code) unique
constraint ensures re-runs update in place.
"""
from __future__ import annotations

import logging
import time
from uuid import UUID

from src.canonical.financials import FinancialFact
from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)


class FinancialsWriter:
    """Upsert fact_financials rows + log to gs_enrichment.object_log.

    Phase 2.7c: write_facts() returns rows_written (int) on success and
    raises on failure. No more outcome dicts.
    """

    def __init__(self, run_id: UUID | str) -> None:
        self.run_id = str(run_id)
        self._client = admin_client()

    def write_facts(
        self,
        party_id: UUID | str,
        facts: list[FinancialFact],
    ) -> int:
        """Upsert a list of financial facts for one party.

        Returns the number of rows written (0 if facts was empty —
        produces a 'no_change' object_log row).

        Raises any exception from the Supabase upsert. On exception, no
        object_log row is written (the runner records failure elsewhere).
        """
        t0 = time.perf_counter()
        party_str = str(party_id)
        rows = [f.model_dump(mode="json") for f in facts]

        # Pydantic mode='json' should serialize UUID → str already, but
        # be defensive in case future model changes break that.
        for r in rows:
            r["party_id"] = party_str

        if not rows:
            log.info("writer.no_facts party=%s", party_str)
            self._log_object(
                party_id=party_str,
                outcome="no_change",
                rows_written=0,
                change_summary={"fact_financials": 0},
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
            return 0

        # Upsert. Any DB exception propagates.
        result = (
            self._client.table("fact_financials")
            .upsert(rows, on_conflict="party_id,period_label,source_code")
            .execute()
        )
        rows_written = len(result.data or [])
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log.info("writer.upsert party=%s rows=%d", party_str, rows_written)

        # Audit row only on success.
        self._log_object(
            party_id=party_str,
            outcome="ok",
            rows_written=rows_written,
            change_summary={"fact_financials": rows_written},
            duration_ms=duration_ms,
        )
        return rows_written

    def _log_object(
        self,
        *,
        party_id: str,
        outcome: str,
        rows_written: int,
        change_summary: dict[str, int],
        duration_ms: int,
    ) -> None:
        """Insert one row into gs_enrichment.object_log.

        Audit-logging failure must not break the main write path: the
        upsert already succeeded. Log loudly and continue.
        """
        try:
            (
                self._client.schema("gs_enrichment")
                .table("object_log")
                .insert({
                    "run_id":          self.run_id,
                    "party_id":        party_id,
                    "enrichment_type": "nbb_financials",
                    "outcome":         outcome,
                    "rows_written":    rows_written,
                    "change_summary":  change_summary,
                    "duration_ms":     duration_ms,
                })
                .execute()
            )
        except Exception:
            log.exception("object_log.insert_failed party=%s", party_id)


__all__ = ["FinancialsWriter"]
