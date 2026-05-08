#!/usr/bin/env python3
"""Lane B β0 — Export financials for one party from Supabase to Excel.

Refactored as a thin wrapper around `src.services.financial_export` and
`src.services.party_query`. The same services power the Web-α route
`/api/party/<uuid>/export.xlsx` — output is byte-identical between CLI
and web download.

Usage:
    python -m src.cli.export_financials_xlsx --party-id <uuid> [--out PATH]

Reads `public.vw_target_financials` (analytical layer with YoY deltas + ratios).
Read-only — no DB writes. Safe to run locally with .env service_role key.

Output: 3-sheet .xlsx
    • Pivot       — metrics × fiscal years (analyst-friendly)
    • Raw         — one row per fiscal year, all columns (filterable)
    • Provenance  — filing_date, source_code, confidence per period (audit trail)
"""
from __future__ import annotations

import logging
from pathlib import Path
from uuid import UUID

import click
from dotenv import load_dotenv

from src.services import financial_export, party_query

# Auto-load .env from repo root so admin_client() finds SUPABASE_URL etc.
# override=False keeps any pre-set shell env vars authoritative.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

log = logging.getLogger(__name__)
logging.basicConfig(
    level="INFO",
    format="%(asctime)s  %(levelname)-5s  %(message)s",
)


@click.command()
@click.option("--party-id", required=True, help="UUID of the party to export")
@click.option(
    "--out",
    default=None,
    type=click.Path(path_type=Path),
    help="Output path (default: ./financials_<party-short>.xlsx)",
)
def export(party_id: str, out: Path | None) -> None:
    """Export financials for one party to a 3-sheet Excel workbook."""
    try:
        UUID(party_id)
    except ValueError as err:
        raise click.BadParameter(f"Invalid UUID: {party_id}") from err

    log.info("fetching financials party_id=%s", party_id)
    party_meta = party_query.get_party_meta(party_id)
    rows = financial_export.get_financial_history(party_id)

    if not rows:
        click.echo(f"\nNo fact_financials rows for party {party_id}.")
        click.echo(
            "Either: party has no SRC_NBB / SRC_PB data yet, or the UUID is wrong."
        )
        raise click.Abort()

    display = (
        (party_meta or {}).get("display_name")
        or (party_meta or {}).get("legal_name", "?")
    )
    log.info(
        "found %d rows · party=%s · KBO=%s",
        len(rows), display, (party_meta or {}).get("kbo_nr", "?"),
    )

    wb = financial_export.build_workbook(rows, party_meta)

    if out is None:
        out = Path(f"financials_{party_id[:8]}.xlsx")
    wb.save(out)

    log.info("wrote %s · %d years · 3 sheets", out.resolve(), len(rows))
    click.echo(f"\n✓ {out}  ({len(rows)} years, 3 sheets)")


if __name__ == "__main__":
    export()
