"""fact_financials writer + audit trail to gs_enrichment.object_log.

Phase 2.7c contract (Optie A): writer raises on database failure. Returns
rows_written count on success. The runner (via worker exception) records
the failure on queue.last_error and run_log.tasks_failed; no silent
failure swallowing here.

W8-worker extension (2026-05-11):
  - write_filing(filing) → UUID  — upsert one row to fact_filings, return id
  - write_lines(filing_id, lines) → int — DELETE-then-INSERT for line set

W8-core fix (2026-05-11): write_facts now targets fact_financials_evidence
directly (the underlying truth table). The fact_financials view from W8-core
has INSTEAD OF triggers for read-time blending, but PostgreSQL does NOT
support INSERT ... ON CONFLICT on views — the trigger never fires for upsert.
Writing to the evidence table directly is both correct and faster.

Audit trail policy:
- Successful write_facts: one object_log row, outcome = 'ok' (rows_written > 0)
  or 'no_change' (rows_written = 0). change_summary aggregates all writes
  done in the parent run, including filings_written + lines_written when
  set by the worker.
- Failed upserts: NO object_log row written (we can't reliably write one
  if the connection or schema is broken). The failure is captured on the
  queue row and run_log.

Idempotency:
- fact_financials_evidence: (party_id, period_label, source_code) unique
  → re-runs upsert in place.
- fact_filings: (source_code, filing_reference) unique
  → re-runs upsert in place. NBB references are globally unique by design.
- fact_financials_lines: DELETE-then-INSERT per filing_id → re-runs produce
  a clean line-set. NOT atomic — a re-run is the recovery mechanism.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from src.canonical.financials import FinancialFact, FilingRecord, FinancialLine
from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)


class FinancialsWriter:
    """Upsert fact_financials rows + log to gs_enrichment.object_log.

    Phase 2.7c: write_facts() returns rows_written (int) on success and
    raises on failure. No more outcome dicts.

    W8-worker: write_filing() returns filing_id (UUID); write_lines()
    returns rows_written. Both raise on failure.
    """

    def __init__(self, run_id: UUID | str) -> None:
        self.run_id = str(run_id)
        self._client = admin_client()
        # Track per-run write summary; flushed to object_log on each write_facts
        self._filings_written = 0
        self._lines_written = 0

    # ── fact_financials_evidence (W8-core truth table for SRC_NBB) ─────────

    def write_facts(
        self,
        party_id: UUID | str,
        facts: list[FinancialFact],
    ) -> int:
        """Upsert a list of financial facts for one party.

        Returns the number of rows written (0 if facts was empty —
        produces a 'no_change' object_log row).

        Writes directly to fact_financials_evidence (the underlying truth
        table). The fact_financials view is read-only for upsert patterns
        because PostgreSQL does not allow ON CONFLICT on views.

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
                change_summary={
                    "fact_financials_evidence": 0,
                    "fact_filings":              self._filings_written,
                    "fact_financials_lines":     self._lines_written,
                },
                duration_ms=int((time.perf_counter() - t0) * 1000),
            )
            return 0

        # Upsert directly to evidence table. Any DB exception propagates.
        result = (
            self._client.table("fact_financials_evidence")
            .upsert(rows, on_conflict="party_id,period_label,source_code")
            .execute()
        )
        rows_written = len(result.data or [])
        duration_ms = int((time.perf_counter() - t0) * 1000)
        log.info("writer.upsert party=%s rows=%d", party_str, rows_written)

        # Audit row on success — include filings/lines counts accumulated
        # earlier in the same run so the object_log reflects the full triple.
        self._log_object(
            party_id=party_str,
            outcome="ok",
            rows_written=rows_written,
            change_summary={
                "fact_financials_evidence": rows_written,
                "fact_filings":              self._filings_written,
                "fact_financials_lines":     self._lines_written,
            },
            duration_ms=duration_ms,
        )
        return rows_written

    # ── fact_filings (W8-core primary NBB truth) ───────────────────────────

    def write_filing(self, filing: FilingRecord) -> UUID:
        """Upsert one row into fact_filings, return generated filing_id.

        On unique conflict (source_code, filing_reference) the existing row
        is updated in place; filing_id is preserved across re-fetches of the
        same reference. NBB references are globally unique by design (year +
        sequential id), so party_id is not part of the UNIQUE — the DB
        constraint is `UNIQUE (source_code, filing_reference)`.

        Raises:
            RuntimeError: Supabase returned no data (unexpected).
            Any DB exception from the underlying upsert (propagated).
        """
        row = filing.model_dump(mode="json")

        result = (
            self._client.table("fact_filings")
            .upsert(row, on_conflict="source_code,filing_reference")
            .execute()
        )
        if not result.data:
            raise RuntimeError(
                f"write_filing returned no data for ref={filing.filing_reference}"
            )

        filing_id_str = result.data[0]["filing_id"]
        log.info(
            "writer.filing_upsert party=%s ref=%s filing_id=%s",
            filing.party_id, filing.filing_reference, filing_id_str,
        )
        self._filings_written += 1
        return UUID(filing_id_str)

    # ── fact_financials_lines (W8-core line-granular) ──────────────────────

    def write_lines(
        self,
        filing_id: UUID,
        lines: list[FinancialLine],
    ) -> int:
        """Bulk-insert fact_financials_lines for one filing.

        Strategy: DELETE WHERE filing_id=X, then INSERT — ensures re-runs
        of the same filing produce a clean line-set without orphans from
        codes that disappeared between runs.

        NOT atomic. If INSERT fails after DELETE succeeds, the filing has
        no lines until the next successful re-run. Acceptable for an
        enrichment worker (re-runs are the recovery mechanism). The runner
        records the failure on queue + run_log.

        Returns rows written. Raises on DB error.
        """
        filing_id_str = str(filing_id)

        # Clean slate for this filing
        (
            self._client.table("fact_financials_lines")
            .delete()
            .eq("filing_id", filing_id_str)
            .execute()
        )

        if not lines:
            log.info("writer.no_lines filing_id=%s", filing_id_str)
            return 0

        # Set filing_id on each line during serialization
        rows = []
        for line in lines:
            line_dict = line.model_dump(mode="json")
            line_dict["filing_id"] = filing_id_str
            rows.append(line_dict)

        result = (
            self._client.table("fact_financials_lines")
            .insert(rows)
            .execute()
        )
        rows_written = len(result.data or [])
        log.info("writer.lines_insert filing_id=%s rows=%d", filing_id_str, rows_written)
        self._lines_written += rows_written
        return rows_written

    # ── internal: audit log helper ─────────────────────────────────────────

    def _log_object(
        self,
        *,
        party_id: str,
        outcome: str,
        rows_written: int,
        change_summary: dict[str, Any],
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
