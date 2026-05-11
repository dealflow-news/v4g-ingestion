"""Upload-and-ingest one ZIP of NBB Consult XBRL exports.

Workflow:
  1. Open the ZIP (in-memory; from BytesIO or Flask FileStorage stream).
  2. For each .xbrl member:
     a. Detect format (pfs:ci vs cbso) by namespace sniff in first 4KB.
     b. If pfs:ci -> parse with parser_pfs.
     c. If cbso -> skip with reason "use Fetch flow; NBB API covers 2022+".
     d. From the FIRST successful parse, derive the party (KBO -> party_id,
        auto-create stub if missing).
     e. For each subsequent file, ensure KBO matches the first; reject
        mixed-company ZIPs (the NBB Consult export is per-company by design).
  3. Per pfs file: aggregate_year -> FinancialFact, extract_filing_and_lines
     -> FilingRecord + FinancialLines, writer.write_filing -> write_lines.
  4. After all files: writer.write_facts bulk upsert.

Returns an IngestResult dict with per-file outcomes for the UI to render.

Idempotency: relies on the same UNIQUE constraints as the W8-worker:
  - fact_filings        UNIQUE (source_code, filing_reference)
  - fact_financials_evidence UNIQUE (party_id, period_label, source_code)
  - fact_financials_lines DELETE-then-INSERT per filing_id (in writer)
Re-uploading the same ZIP converges to the same final state.

Pre-2022 (pfs:ci) files cover the NBB Authentic-Data-API gap (the API only
exposes 2021+). The cbso 2022+ files in a Consult export are redundant and
can be skipped without losing data — Fetch flow ingests them via NBB API.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZipFile

from src.canonical.financials import FilingRecord, FinancialFact, FinancialLine
from src.domain.nbb.aggregator import aggregate_year
from src.domain.nbb.extractor import extract_filing_and_lines_from_parsed
from src.domain.nbb.parser_pfs import parse_pfs_xbrl
from src.persistence.financials_writer import FinancialsWriter
from src.services.party_auto_create import resolve_or_create_party

log = logging.getLogger(__name__)


@dataclass
class FileOutcome:
    filename: str
    status: str  # "ingested" | "skipped" | "failed"
    reason: str = ""
    period_label: str | None = None
    pcmn_count: int = 0


@dataclass
class IngestResult:
    run_id: str
    party_id: str | None = None
    party_was_created: bool = False
    kbo: str | None = None
    entity_name: str | None = None
    files_total: int = 0
    files_ingested: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    filings_written: int = 0
    lines_total: int = 0
    facts_written: int = 0
    outcomes: list[FileOutcome] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":            self.run_id,
            "party_id":          self.party_id,
            "party_was_created": self.party_was_created,
            "kbo":               self.kbo,
            "entity_name":       self.entity_name,
            "files_total":       self.files_total,
            "files_ingested":    self.files_ingested,
            "files_skipped":     self.files_skipped,
            "files_failed":      self.files_failed,
            "filings_written":   self.filings_written,
            "lines_total":       self.lines_total,
            "facts_written":     self.facts_written,
            "outcomes": [
                {
                    "filename":     o.filename,
                    "status":       o.status,
                    "reason":       o.reason,
                    "period_label": o.period_label,
                    "pcmn_count":   o.pcmn_count,
                }
                for o in self.outcomes
            ],
        }


def ingest_uploaded_zip(
    stream,
    supabase_client,
    *,
    uploaded_filename: str = "(upload)",
    run_id: UUID | str | None = None,
) -> dict[str, Any]:
    """Ingest one ZIP of NBB XBRL filings.

    Args:
        stream:           File-like with .read() (Flask FileStorage works).
        supabase_client:  Service-role Supabase client. Used for party lookup
                          and create; the writer internally calls admin_client()
                          for its own writes.
        uploaded_filename: Original filename for logging only.
        run_id:           Optional run UUID for audit log; auto-generated
                          if None.

    Returns:
        IngestResult.to_dict()

    Raises:
        BadZipFile: stream is not a valid ZIP archive.
        ValueError: ZIP contains files for multiple KBOs, or first
                    parseable file has no extractable KBO.
    """
    run_id = run_id or uuid4()
    result = IngestResult(run_id=str(run_id))

    # Read bytes into memory (NBB ZIPs are small; this avoids stream-pos
    # issues with Flask FileStorage)
    zip_bytes = stream.read() if hasattr(stream, "read") else bytes(stream)

    log.info(
        "zip_ingest.start filename=%s size=%d run_id=%s",
        uploaded_filename, len(zip_bytes), run_id,
    )

    try:
        zf = ZipFile(io.BytesIO(zip_bytes))
    except BadZipFile:
        raise

    # First pass: collect .xbrl members
    members = [
        info for info in zf.infolist()
        if info.filename.lower().endswith(".xbrl") and not info.is_dir()
    ]
    result.files_total = len(members)

    if not members:
        log.warning("zip_ingest.empty no .xbrl files in archive")
        return result.to_dict()

    # Stage 1: parse all pfs files; skip cbso/unknown; collect parsed_dicts
    parsed_pfs: list[tuple[str, dict[str, Any]]] = []

    for info in members:
        fname = info.filename
        try:
            content = zf.read(info)
        except Exception as e:
            log.exception("zip_ingest.read_failed file=%s", fname)
            result.outcomes.append(FileOutcome(
                filename=fname, status="failed", reason=f"read error: {e}",
            ))
            result.files_failed += 1
            continue

        # Format detection: peek namespaces in the first 4KB
        head = content[:4096].decode("utf-8", errors="ignore")

        if 'xmlns:pfs="http://www.nbb.be/be/fr/pfs/' in head:
            try:
                parsed = parse_pfs_xbrl(content, source_filename=fname)
            except Exception as e:
                log.exception("zip_ingest.parse_failed file=%s", fname)
                result.outcomes.append(FileOutcome(
                    filename=fname, status="failed",
                    reason=f"parse error: {e}",
                ))
                result.files_failed += 1
                continue
            parsed_pfs.append((fname, parsed))

        elif "cbso/dict" in head or "be/fr/cbso/" in head:
            # 2022+ format -- NBB API covers it via Fetch flow
            result.outcomes.append(FileOutcome(
                filename=fname,
                status="skipped",
                reason="cbso format (2022+) - use Fetch tab; NBB API covers this range",
            ))
            result.files_skipped += 1

        else:
            result.outcomes.append(FileOutcome(
                filename=fname,
                status="skipped",
                reason="unrecognized XBRL namespace",
            ))
            result.files_skipped += 1

    if not parsed_pfs:
        log.warning("zip_ingest.no_pfs_files no parseable pfs files in archive")
        return result.to_dict()

    # Stage 2: resolve party from the FIRST parsed file's KBO
    first_fname, first_parsed = parsed_pfs[0]
    kbo = first_parsed.get("kbo")
    if not kbo:
        raise ValueError(
            f"first parseable file {first_fname!r} has no extractable KBO; "
            "cannot resolve party"
        )

    entity_name = first_parsed.get("entity_name")
    legal_form_code = first_parsed.get("legal_form_code")

    party_id, was_created = resolve_or_create_party(
        supabase_client,
        kbo,
        display_name=entity_name,
        legal_form_code=legal_form_code,
    )
    result.party_id = str(party_id)
    result.party_was_created = was_created
    result.kbo = kbo
    result.entity_name = entity_name

    log.info(
        "zip_ingest.party_resolved kbo=%s party_id=%s created=%s",
        kbo, party_id, was_created,
    )

    # Stage 3: verify all subsequent files have matching KBO
    for fname, parsed in parsed_pfs[1:]:
        other_kbo = parsed.get("kbo")
        if other_kbo and other_kbo != kbo:
            raise ValueError(
                f"mixed-company ZIP: {fname!r} has KBO {other_kbo}, "
                f"expected {kbo} (from {first_fname!r})"
            )

    # Stage 4: per-file aggregate -> extract -> write_filing -> write_lines
    # The writer keeps a running tally of filings/lines for the eventual
    # facts-write object_log entry (same pattern as W8-worker).
    writer = FinancialsWriter(run_id=run_id)
    facts: list[FinancialFact] = []

    for fname, parsed in parsed_pfs:
        try:
            period_label, pcmn_count = _ingest_one_file(
                parsed, party_id, writer, facts,
            )
            result.outcomes.append(FileOutcome(
                filename=fname, status="ingested",
                period_label=period_label, pcmn_count=pcmn_count,
            ))
            result.files_ingested += 1
            result.filings_written += 1
        except Exception as e:
            log.exception("zip_ingest.write_failed file=%s", fname)
            result.outcomes.append(FileOutcome(
                filename=fname, status="failed",
                reason=f"write error: {e}",
            ))
            result.files_failed += 1

    # Stage 5: bulk write facts
    try:
        result.facts_written = writer.write_facts(party_id=party_id, facts=facts)
    except Exception as e:
        log.exception("zip_ingest.facts_bulk_write_failed")
        # Per-file outcomes are already recorded; tag the bulk-write failure
        # on every ingested row so the UI can flag the partial state.
        for o in result.outcomes:
            if o.status == "ingested":
                o.reason = f"facts bulk write failed: {e}"

    # Lines total: sum of pcmn_count is a parser-side count; actual writes
    # may differ if the extractor filters. For now we report the parser
    # count; the writer's logs hold the authoritative line counts.
    result.lines_total = sum(
        o.pcmn_count for o in result.outcomes if o.status == "ingested"
    )

    log.info(
        "zip_ingest.complete run_id=%s total=%d ingested=%d skipped=%d failed=%d",
        run_id, result.files_total, result.files_ingested,
        result.files_skipped, result.files_failed,
    )
    return result.to_dict()


def _ingest_one_file(
    parsed: dict[str, Any],
    party_id: UUID,
    writer: FinancialsWriter,
    facts: list[FinancialFact],
) -> tuple[str | None, int]:
    """Process one parsed XBRL file: aggregate, extract, write filing+lines.

    Mirrors the per-year loop body in nbb_financials.run_for_party. Reuses
    the same aggregator + extractor + writer (no duplication of business
    logic across the Fetch and Upload flows).

    Returns (period_label, pcmn_count_in_parsed).
    """
    fy_end_str = parsed.get("fy_end")
    fy_start_str = parsed.get("fy_start")
    period_label = fy_end_str[:4] if fy_end_str else None

    fy_end = _str_to_date(fy_end_str)
    fy_start = _str_to_date(fy_start_str)
    filing_date = _str_to_date(parsed.get("filing_date"))

    codes = {
        k: v for k, v in parsed.get("amounts", {}).items()
        if not k.startswith("_count_")
    }

    # Step 1: aggregate -> FinancialFact (appended; writer.write_facts at end)
    agg = aggregate_year(
        year_data=codes,
        period_label=period_label,
        period_end=fy_end,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        nbb_model_type=parsed.get("model_type"),
        nbb_filing_date=filing_date,
    )
    row = agg.as_upsert_row(party_id=str(party_id))
    facts.append(FinancialFact(**row))

    # Step 2: extract filing + lines (same call signature as W8-worker)
    ref_num = parsed.get("ref_num")
    if not ref_num:
        raise ValueError("missing ref_num (could not derive from filename)")

    filing_dict, lines_dicts = extract_filing_and_lines_from_parsed(
        parsed=parsed,
        filing_reference=ref_num,
        party_id=party_id,
        filing_meta=None,
    )
    filing = FilingRecord(**filing_dict)
    lines = [FinancialLine(**ln) for ln in lines_dicts]

    # Step 3: write filing -> lines (FK ordering matters)
    filing_id = writer.write_filing(filing)
    writer.write_lines(filing_id, lines)

    return period_label, len(codes)


def _str_to_date(s: str | None) -> date | None:
    """Parse ISO date string (YYYY-MM-DD); return None on failure."""
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None


__all__ = ["ingest_uploaded_zip", "IngestResult", "FileOutcome"]
