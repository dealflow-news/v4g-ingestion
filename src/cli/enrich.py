"""V4G enrichment CLI.

Usage:
    python -m src.cli.enrich <vat> [--types nbb_financials,kbo_directors]
    python -m src.cli.enrich --sweep-stale
    python -m src.cli.enrich --party-id <uuid> [--types ...]
"""
from __future__ import annotations

import logging
import sys

import click

from src.enrichment import queue as q
from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)
logging.basicConfig(
    level="INFO",
    format="%(asctime)s  %(levelname)-5s  %(message)s",
)


@click.command()
@click.argument("vat", required=False)
@click.option("--party-id", default=None, help="Direct party_id (bypasses VAT lookup)")
@click.option(
    "--types",
    default="nbb_financials",
    help="Comma-separated enrichment types",
)
@click.option(
    "--sweep-stale",
    is_flag=True,
    help="Run cadence sweep — enqueues stale high-priority parties",
)
def enrich(vat: str | None, party_id: str | None, types: str, sweep_stale: bool) -> None:
    if sweep_stale:
        log.info("running cadence_stale sweep...")
        result = q.sweep_stale_parties()
        log.info("sweep done · enqueued=%d", result.get("enqueued_count", 0))
        return

    if not party_id and not vat:
        click.echo("Provide either <vat> or --party-id <uuid>. Use --help for usage.", err=True)
        sys.exit(1)

    # Resolve party_id from VAT/KBO if not given directly
    resolved_pid: str
    if party_id:
        resolved_pid = party_id
    else:
        client = admin_client()
        kbo = (vat or "").replace("BE", "").replace(".", "").replace(" ", "").strip().zfill(10)
        result = (
            client.table("party_identifiers")
            .select("party_id")
            .eq("id_type", "KBO")
            .eq("id_value", kbo)
            .limit(1)
            .execute()
        )
        if not result.data:
            log.error("no party found for KBO %s", kbo)
            sys.exit(2)
        resolved_pid = result.data[0]["party_id"]
        log.info("resolved KBO %s → party_id %s", kbo, resolved_pid)

    type_list = [t.strip() for t in types.split(",") if t.strip()]
    log.info("enqueueing %s for party %s", type_list, resolved_pid)
    rows = q.enqueue(party_id=resolved_pid, enrichment_types=type_list)
    log.info("enqueued %d new task(s)", len(rows))


if __name__ == "__main__":
    enrich()
