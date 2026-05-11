"""ZIP ingester — Lane B upload handler for /tools/upload-zip.

v1 STATUS: stub. The route ``POST /tools/upload-zip`` is feature-flagged
behind ``TOOLS_UPLOAD_ENABLED`` (default off). This module exists so the
route can import cleanly, and so the public function signature is fixed
at v1 time — only the body needs implementation in iteration 2.

When implemented, this will:
  1. Open the uploaded ZIP
  2. Iterate XBRL/JSON-XBRL files (detect format by header bytes)
  3. For each: parse → extract_filing_and_lines_from_parsed
  4. Resolve party_id (auto-create if KBO missing from party_identifiers)
  5. Call FinancialsWriter.write_filing + write_lines + write_facts
  6. Return aggregated counts

For now: returns an error structure indicating "not implemented".
"""
from __future__ import annotations

import logging
from typing import IO, Any

log = logging.getLogger(__name__)


def ingest_uploaded_zip(
    stream: IO[bytes],
    supabase_client: Any,
    *,
    uploaded_filename: str = "upload.zip",
) -> dict[str, Any]:
    """Synchronous Lane B ZIP ingestion.

    Parameters
    ----------
    stream
        Open file-like (e.g. Flask ``request.files['file'].stream``).
    supabase_client
        Service-role Supabase client (writes go through here).
    uploaded_filename
        Original filename, used only for logging/return value.

    Returns
    -------
    dict with the shape:

        {
            "filename":        str,
            "filings_written": int,
            "lines_total":     int,
            "evidence_updated": int,
            "skipped":         int,
            "party_id":        str,
            "kbo":             str,
            "company":         str,
            "warnings":        [str, ...],
        }

    Raises
    ------
    NotImplementedError
        v1 stub — actual implementation lands in iteration 2.
    """
    log.warning(
        "zip_ingester.ingest_uploaded_zip · stub called · filename=%s",
        uploaded_filename,
    )
    raise NotImplementedError(
        "ZIP ingestion is feature-flagged off in v1. "
        "Set TOOLS_UPLOAD_ENABLED=true and implement this function in iteration 2.",
    )
