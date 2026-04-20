"""fact_financials writer + audit trail to gs_enrichment.object_log.

Writes are idempotent: the (party_id, period_label, source_code) unique
constraint ensures re-runs update in place. Each run records an object_log
row with the change_summary for traceability.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID

from src.canonical.financials import FinancialFact
from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)


class FinancialsWriter:
    """Upsert fact_financials rows + log to gs_enrichment.object_log."""

    def __init__(self, run_id: UUID | str) -> None:
        self.run_id = str(run_id)
        self._client = admin_client()

    def write_facts(
        self,
        party_id: UUID | str,
        facts: list[FinancialFact],
    ) -> dict[str, Any]:
        """Upsert a list of financial facts for one party.

        Returns a summary dict with rows_upserted, rows_skipped, and any
        errors. Always writes an object_log row, even on zero facts (so
        audit trail is complete).
        """
        t0 = time.perf_counter()
        party_str = str(party_id)
        rows = [f.model_dump(mode="json") for f in facts]

        # Normalize party_id to UUIDs as strings (Pydantic serializes UUID
        # to string already via mode='json', but double-check)
        for r in rows:
            r["party_id"] = party_str

        outcome: str = "ok"
        error: str | None = None
        rows_written: int = 0

        if not rows:
            outcome = "no_change"
            log.info("writer.no_facts party=%s", party_str)
        else:
            try:
                # Upsert on unique (party_id, period_label, source_code).
                # PostgREST upsert uses the primary key by default; for our
                # UK we must pass on_conflict param.
                result = (
                    self._client.table("fact_financials")
                    .upsert(rows, on_conflict="party_id,period_label,source_code")
                    .execute()
                )
                rows_written = len(result.data or [])
                log.info(
                    "writer.upsert party=%s rows=%d",
                    party_str, rows_written,
                )
            except Exception as e:
                outcome = "failed"
                error = f"{type(e).__name__}: {e}"
                log.exception("writer.failed party=%s", party_str)

        duration_ms = int((time.perf_counter() - t0) * 1000)

        # Audit: always write one object_log row
        self._log_object(
            party_id=party_str,
            enrichment_type="nbb_financials",
            outcome=outcome,
            rows_written=rows_written,
            change_summary={"fact_financials": rows_written},
            error=error,
            duration_ms=duration_ms,
        )

        return {
            "outcome": outcome,
            "rows_written": rows_written,
            "error": error,
            "duration_ms": duration_ms,
        }

    def _log_object(
        self,
        *,
        party_id: str,
        enrichment_type: str,
        outcome: str,
        rows_written: int,
        change_summary: dict[str, int],
        error: str | None,
        duration_ms: int,
    ) -> None:
        """Insert one row into gs_enrichment.object_log."""
        try:
            (
                self._client.schema("gs_enrichment")
                .table("object_log")
                .insert(
                    {
                        "run_id":          self.run_id,
                        "party_id":        party_id,
                        "enrichment_type": enrichment_type,
                        "outcome":         outcome,
                        "rows_written":    rows_written,
                        "change_summary":  change_summary,
                        "error":           error,
                        "duration_ms":     duration_ms,
                    }
                )
                .execute()
            )
        except Exception:
            # Audit logging failure should never break the main write path.
            # Log it loudly and continue.
            log.exception("object_log.insert_failed party=%s", party_id)


__all__ = ["FinancialsWriter"]
