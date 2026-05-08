#!/usr/bin/env python3
"""
Lane B β1 — Stage NBB CBSO bulk-XBRL ZIPs into _stg_nbb_filings.

Usage:
    python -m src.cli.ingest_nbb_zip <zip_path> [--party-id UUID] [--dry-run]

Idempotent on filing_reference: re-running the same ZIP is a no-op.
Staging only — no canonical writes. Promotion to fact_financials happens
later (β3 sync-promote, or the optional queue+worker fallback in β4).

Filing_date sourcing (LB-005): bulk ZIPs don't include filing_date in
the XBRL bytes themselves. We do a one-shot lookup against NBB's
/legalEntity/{vat}/references API per unique KBO in the ZIP and pull
DepositDate from the response. Requires NBB_API_KEY in env (or .env);
without it we still stage but with filing_date=NULL.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import click

from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)
logging.basicConfig(
    level="INFO",
    format="%(asctime)s  %(levelname)-5s  %(message)s",
)


# ─── Format detection ───────────────────────────────────────────────────
NS_PFS_OLD  = "nbb.be/be/fr/pfs"
NS_CBSO_NEW = "nbb.be/be/fr/cbso"

# Filename pattern: <filing_year>-<reference>.xbrl  (filing_year ≠ fiscal_year)
FILENAME_RE = re.compile(r"^(\d{4})-(\d+)$")
KBO_BE_RE   = re.compile(rb">BE(\d{10})<")
KBO_BARE_RE = re.compile(rb"<identifier[^>]*>(\d{10})</identifier>")


@dataclass
class StagingRow:
    """One row destined for public._stg_nbb_filings."""
    party_id: str
    kbo_nr: str
    filing_reference: str
    filing_year: int
    filing_date: Optional[date]
    fiscal_year_end: date
    fiscal_year_start: Optional[date]
    taxonomy_format: str
    nbb_model_type: Optional[str]
    raw_xbrl: str
    raw_xbrl_sha256: str
    source_filename: str
    source_zip_name: str

    def to_payload(self) -> dict:
        d = asdict(self)
        for k in ("filing_date", "fiscal_year_end", "fiscal_year_start"):
            v = d[k]
            d[k] = v.isoformat() if isinstance(v, date) else None
        return d


# ─── Domain — XBRL extraction (stdlib ET to match parser.py convention) ─
def detect_format(head: str) -> str:
    if NS_CBSO_NEW in head:
        return "cbso-new"
    if NS_PFS_OLD in head:
        return "pfs-old"
    raise ValueError("Unknown XBRL format — neither pfs-old nor cbso-new namespace")


def extract_kbo(xbrl_bytes: bytes) -> str:
    """Pull enterprise number from XBRL identifier (handles both formats)."""
    m = KBO_BE_RE.search(xbrl_bytes[:8000])
    if m:
        return m.group(1).decode()
    m = KBO_BARE_RE.search(xbrl_bytes[:8000])
    if m:
        return m.group(1).decode()
    raise ValueError("Could not extract KBO from XBRL identifier")


def extract_periods(tree) -> tuple[date, Optional[date]]:
    """Latest endDate/instant = fiscal_year_end; matching startDate by year."""
    end_dates: list[date] = []
    start_dates: list[date] = []
    for elem in tree.iter():
        tag = elem.tag.rsplit("}", 1)[-1] if isinstance(elem.tag, str) else ""
        if tag in ("endDate", "instant"):
            try:
                end_dates.append(date.fromisoformat((elem.text or "").strip()))
            except (ValueError, TypeError):
                pass
        elif tag == "startDate":
            try:
                start_dates.append(date.fromisoformat((elem.text or "").strip()))
            except (ValueError, TypeError):
                pass
    if not end_dates:
        raise ValueError("No period endDate or instant found in XBRL")
    fy_end = max(end_dates)
    fy_start = max(
        (s for s in start_dates if s.year == fy_end.year and s < fy_end),
        default=None,
    )
    return fy_end, fy_start


def detect_model_type(xbrl_bytes: bytes) -> Optional[str]:
    """Best-effort detection from schemaRef URLs (m01/m02/m03)."""
    head = xbrl_bytes[:4000].decode("utf-8", errors="ignore")
    for m in ("m01", "m02", "m03"):
        if f"/{m}/" in head or f"-{m}-" in head:
            return m
    return None


def parse_filing_reference(filename: str) -> tuple[str, int]:
    """'2025-00231176.xbrl' → ('2025-00231176', 2025)."""
    stem = Path(filename).stem
    m = FILENAME_RE.match(stem)
    if not m:
        raise ValueError(f"Unexpected filename pattern: {filename}")
    return stem, int(m.group(1))


# ─── Persistence — uses repo's admin_client (Stage 2 lockdown) ──────────
def lookup_party_id_by_kbo(kbo_nr: str) -> str:
    """Resolve party_id via party_identifiers (id_type='KBO').

    Matches the convention in src/enrichment/runner.py (_resolve_kbo).
    """
    client = admin_client()
    result = (
        client.table("party_identifiers")
        .select("party_id")
        .eq("id_type", "KBO")
        .eq("id_value", kbo_nr)
        .limit(1)
        .execute()
    )
    if not result.data:
        raise LookupError(
            f"No party found for KBO {kbo_nr} in party_identifiers (id_type=KBO)"
        )
    return result.data[0]["party_id"]


def fetch_filing_dates(kbo_nr: str) -> dict[str, dict]:
    """LB-005 — pull DepositDate per filing_reference from NBB references API.

    Returns {referenceNumber: {filing_date, deposit_type, model_type}}.
    Empty dict on failure (NBB_API_KEY missing, network error, etc.) — we
    still proceed with filing_date=NULL in staging.
    """
    api_key = os.environ.get("NBB_API_KEY")
    if not api_key:
        log.warning("NBB_API_KEY not set — staging with filing_date=NULL")
        return {}

    try:
        from src.domain.nbb.fetcher import get_references
    except ImportError:
        log.exception("could not import fetcher.get_references")
        return {}

    try:
        refs = get_references(kbo_nr, api_key)
    except Exception as e:
        log.warning("references fetch failed (%s) — staging with filing_date=NULL", e)
        return {}

    out: dict[str, dict] = {}
    for r in refs:
        ref = r.get("referenceNumber") or r.get("ReferenceNumber")
        if ref:
            out[str(ref)] = {
                "filing_date":  r.get("DepositDate")  or r.get("depositDate"),
                "deposit_type": r.get("DepositType")  or r.get("depositType"),
                "model_type":   r.get("ModelType")    or r.get("modelType"),
            }
    log.info("fetched %d filing-date entries for KBO %s", len(out), kbo_nr)
    return out


def insert_staging_row(row: StagingRow) -> str:
    """Insert one row. Returns 'inserted' or 'duplicate'.

    Idempotency: filing_reference has a UNIQUE constraint, so we use
    upsert with ignore_duplicates so re-runs are no-ops.
    """
    client = admin_client()
    payload = row.to_payload()

    # Try modern supabase-py upsert first; fall back to insert+catch on older
    try:
        result = (
            client.table("_stg_nbb_filings")
            .upsert(
                payload,
                on_conflict="filing_reference",
                ignore_duplicates=True,
            )
            .execute()
        )
        return "inserted" if (result.data or []) else "duplicate"
    except TypeError:
        # Older supabase-py without ignore_duplicates kwarg — try insert
        pass

    try:
        result = client.table("_stg_nbb_filings").insert(payload).execute()
        return "inserted" if (result.data or []) else "duplicate"
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            return "duplicate"
        raise


# ─── Pipeline ──────────────────────────────────────────────────────────
def process_zip(
    zip_path: Path,
    party_id_override: Optional[str],
    dry_run: bool,
) -> dict:
    inserted = duplicates = errors = 0
    party_id = party_id_override
    filing_dates: dict[str, dict] = {}  # populated lazily after first KBO seen

    with zipfile.ZipFile(zip_path) as zf:
        xbrl_files = sorted(n for n in zf.namelist() if n.endswith(".xbrl"))
        click.echo(f"\n{zip_path.name} — {len(xbrl_files)} XBRL files\n")

        for fname in xbrl_files:
            try:
                raw = zf.read(fname)
                head = raw[:3000].decode("utf-8", errors="ignore")
                ref, fyear = parse_filing_reference(fname)
                fmt = detect_format(head)
                kbo = extract_kbo(raw)

                tree = ET.fromstring(raw)
                fy_end, fy_start = extract_periods(tree)
                model_type = detect_model_type(raw)

                # Resolve party once on first filing
                if party_id is None:
                    party_id = lookup_party_id_by_kbo(kbo)
                    click.echo(f"  → resolved party_id={party_id} via KBO {kbo}")

                # Lazy-fetch filing_dates once per KBO (skip in dry-run)
                if not filing_dates and not dry_run:
                    filing_dates = fetch_filing_dates(kbo)

                # Apply LB-005 metadata if available
                meta = filing_dates.get(ref, {})
                filing_date: Optional[date] = None
                if meta.get("filing_date"):
                    try:
                        filing_date = date.fromisoformat(meta["filing_date"][:10])
                    except (ValueError, TypeError):
                        pass
                # NBB references API gives more granular ModelType (e.g. m02-f) —
                # prefer it over the namespace-derived guess.
                if meta.get("model_type"):
                    model_type = meta["model_type"]

                row = StagingRow(
                    party_id=party_id,
                    kbo_nr=kbo,
                    filing_reference=ref,
                    filing_year=fyear,
                    filing_date=filing_date,
                    fiscal_year_end=fy_end,
                    fiscal_year_start=fy_start,
                    taxonomy_format=fmt,
                    nbb_model_type=model_type,
                    raw_xbrl=raw.decode("utf-8"),
                    raw_xbrl_sha256=hashlib.sha256(raw).hexdigest(),
                    source_filename=fname,
                    source_zip_name=zip_path.name,
                )

                fd_str = filing_date.isoformat() if filing_date else "—"
                tag = (
                    f"{ref:<18}  fmt={fmt:<8}  fy_end={fy_end}  "
                    f"depot={fd_str}  model={model_type or '—'}"
                )

                if dry_run:
                    click.echo(f"  [DRY] {tag}")
                    continue

                outcome = insert_staging_row(row)
                if outcome == "inserted":
                    inserted += 1
                    click.echo(f"  [✓]   {tag}")
                else:
                    duplicates += 1
                    click.echo(f"  [=]   {tag}  (already staged)")

            except Exception as e:
                errors += 1
                click.echo(f"  [✗]   {fname}  ERROR: {e}", err=True)

    return {"inserted": inserted, "duplicates": duplicates, "errors": errors}


@click.command()
@click.argument("zip_path", type=click.Path(exists=True, path_type=Path))
@click.option("--party-id", default=None, help="Skip KBO→party_identifiers lookup")
@click.option("--dry-run", is_flag=True, help="Parse without inserting")
def ingest(zip_path: Path, party_id: Optional[str], dry_run: bool) -> None:
    """Stage a NBB CBSO bulk-XBRL ZIP into _stg_nbb_filings.

    Idempotent on filing_reference. Re-running the same ZIP is a no-op.
    Promotion to fact_financials happens via β3 sync-promote (next deliverable).
    """
    summary = process_zip(zip_path, party_id, dry_run)
    click.echo(
        f"\nSummary: {summary['inserted']} inserted, "
        f"{summary['duplicates']} duplicates, {summary['errors']} errors"
    )
    if not dry_run and summary["inserted"]:
        click.echo(
            f"         → {summary['inserted']} rows in _stg_nbb_filings.\n"
            f"         Promotion to fact_financials happens via β3 sync-promote\n"
            f"         (next deliverable). LB-004 trigger is OPTIONAL with the\n"
            f"         sync-in-CLI architecture (β4 fallback only)."
        )


if __name__ == "__main__":
    ingest()
