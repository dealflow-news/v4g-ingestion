#!/usr/bin/env python3
"""Re-promote specific filings via fn_promote_nbb_filing.

Used after the LB-007 fix (cbso-new uses JSON-XBRL API instead of bulk-XBRL
parser) to fix already-'parsed' rows that hold incorrect data from the old
buggy parser.

Workflow:
    1. Reset target staging rows: parse_status='pending', fact_financial_id=NULL
    2. Re-call fn_promote_nbb_filing with the new (correct) canonical JSONB
    3. Existing fact_financials row gets UPSERTed with correct values

Usage (re-promote AB LENS MOTOR's 4 cbso-new filings):
    python -m tests.repromote_filings \\
        2022-20056571 2023-00114002 2024-00091742 2025-00231176

Or via filing_id list (UUIDs).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

_here = Path(__file__).resolve()
REPO_ROOT = next(
    (p for p in (_here.parent, *_here.parents) if (p / "src").is_dir()),
    _here.parent,
)
sys.path.insert(0, str(REPO_ROOT))

import click  # noqa: E402
from dotenv import find_dotenv, load_dotenv  # noqa: E402

_envfile = find_dotenv(usecwd=True) or str(REPO_ROOT / ".env")
load_dotenv(_envfile, override=False)

from src.cli.ingest_nbb_zip import promote_filings  # noqa: E402
from src.persistence.supabase import admin_client  # noqa: E402


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
        return True
    except ValueError:
        return False


def _resolve_filing_ids(refs_or_ids: list[str]) -> list[tuple[str, str]]:
    """Return list of (filing_id, filing_reference) tuples."""
    client = admin_client()
    out: list[tuple[str, str]] = []
    for token in refs_or_ids:
        col = "filing_id" if _is_uuid(token) else "filing_reference"
        resp = (
            client.table("_stg_nbb_filings")
            .select("filing_id, filing_reference, taxonomy_format, parse_status")
            .eq(col, token)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            click.echo(f"  [!]  no staging row found for {token}", err=True)
            continue
        for r in rows:
            out.append((r["filing_id"], r["filing_reference"]))
            click.echo(
                f"  [✓]  resolved {token:<22} → "
                f"filing_id={r['filing_id'][:8]}…  "
                f"fmt={r['taxonomy_format']}  "
                f"current_status={r['parse_status']}"
            )
    return out


def reset_to_pending(filing_ids: list[str]) -> int:
    """Reset staging rows to pending so β3 will pick them up.

    Note: leaves fact_financials rows in place. fn_promote_nbb_filing will
    UPSERT (replace) them with correct values when called next.
    Sets fact_financial_id=NULL on staging row so the FK relationship is
    clean during the brief window before re-promotion.
    """
    client = admin_client()
    n = 0
    for fid in filing_ids:
        client.table("_stg_nbb_filings").update(
            {"parse_status": "pending", "fact_financial_id": None}
        ).eq("filing_id", fid).execute()
        n += 1
    return n


@click.command()
@click.argument("refs_or_ids", nargs=-1, required=True)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Resolve filing_ids and show the plan without making changes",
)
def main(refs_or_ids: tuple[str, ...], dry_run: bool) -> None:
    """Reset given filings to pending and re-promote via NBB JSON-XBRL API."""
    click.echo("\nResolving target filings…")
    pairs = _resolve_filing_ids(list(refs_or_ids))
    if not pairs:
        click.echo("No filings resolved — abort.", err=True)
        sys.exit(1)

    filing_ids = [p[0] for p in pairs]

    click.echo(f"\nFound {len(pairs)} filings to re-promote.")
    if dry_run:
        click.echo("Dry-run: no DB writes. Drop --dry-run to proceed.")
        return

    api_key = os.environ.get("NBB_API_KEY") or None
    if not api_key:
        click.echo(
            "  [✗]  NBB_API_KEY env var required for cbso-new re-promotion.",
            err=True,
        )
        sys.exit(1)

    click.echo(f"\nResetting {len(filing_ids)} rows to pending…")
    n_reset = reset_to_pending(filing_ids)
    click.echo(f"  → {n_reset} rows reset")

    click.echo("\nRe-promoting via fn_promote_nbb_filing…\n")
    counts = promote_filings(filing_ids, api_key)
    click.echo(
        f"\nRe-promotion: {counts['promoted']} promoted, "
        f"{counts['superseded']} superseded, {counts['failed']} failed"
    )


if __name__ == "__main__":
    main()
