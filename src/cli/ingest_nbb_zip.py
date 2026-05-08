#!/usr/bin/env python3
"""
Lane B β1 + β3 — Stage NBB CBSO bulk-XBRL ZIPs and promote to fact_financials.

Usage:
    python -m src.cli.ingest_nbb_zip <zip_path> [--party-id UUID] [--dry-run]
                                                 [--no-promote]

Two-phase flow:
  β1 staging  → idempotent insert into _stg_nbb_filings (on filing_reference)
  β3 promote  → for each pending row touched in this run: parse raw_xbrl,
                aggregate via the same aggregator Lane A uses, and call
                fn_promote_nbb_filing to UPSERT into fact_financials with
                "latest filing_date wins" conflict resolution per
                (kbo_nr, fiscal_year_end). Older parsed siblings get demoted
                to status='superseded'.

Filing_date sourcing (LB-005): bulk ZIPs don't include filing_date in the
XBRL bytes. We do a one-shot lookup against NBB's /legalEntity/{vat}/
references API per unique KBO and pull DepositDate. fn_promote_nbb_filing
REQUIRES a non-NULL filing_date (raises 23502 otherwise) — this is the
conflict-resolution key. If the API call fails, those filings stage with
filing_date=NULL and β3 will surface them as 'failed' rather than promote
with a wrong winner.

Use --no-promote to run staging-only (β1 behavior).
"""
from __future__ import annotations

import contextlib
import hashlib
import json as _json
import logging
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import click
from dotenv import load_dotenv

from src.domain.nbb.aggregator import aggregate_year
from src.domain.nbb.fetcher import NBBApiError, fetch_jsonxbrl, parse_rubrics
from src.domain.nbb.parser import parse_xbrl
from src.persistence.supabase import admin_client

# Auto-load .env from repo root so admin_client() and NBB_API_KEY work
# without requiring shell-side env-var loading per session.
# override=False keeps any pre-set shell env vars authoritative.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

log = logging.getLogger(__name__)
logging.basicConfig(
    level="INFO",
    format="%(asctime)s  %(levelname)-5s  %(message)s",
)


# ─── _stg_nbb_filings status lifecycle ──────────────────────────────────
# Verified against fn_promote_nbb_filing body (LB-002, 2026-05-08):
#   pending    → β1 inserted, no promotion attempted yet
#   parsed     → β3 promoted, fact_financials row exists, this is the WINNER
#   superseded → β3 lost a conflict to a sibling with later filing_date
STAGING_STATUS_COL = "parse_status"
STATUS_PENDING     = "pending"
STATUS_PARSED      = "parsed"
STATUS_SUPERSEDED  = "superseded"


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
    filing_date: date | None
    fiscal_year_end: date
    fiscal_year_start: date | None
    taxonomy_format: str
    nbb_model_type: str | None
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


# ─── XBRL extraction (stdlib ET to match parser.py convention) ──────────
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


def extract_periods(tree) -> tuple[date, date | None]:
    """Latest endDate/instant = fiscal_year_end; matching startDate by year."""
    end_dates: list[date] = []
    start_dates: list[date] = []
    for elem in tree.iter():
        tag = elem.tag.rsplit("}", 1)[-1] if isinstance(elem.tag, str) else ""
        if tag in ("endDate", "instant"):
            with contextlib.suppress(ValueError, TypeError):
                end_dates.append(date.fromisoformat((elem.text or "").strip()))
        elif tag == "startDate":
            with contextlib.suppress(ValueError, TypeError):
                start_dates.append(date.fromisoformat((elem.text or "").strip()))
    if not end_dates:
        raise ValueError("No period endDate or instant found in XBRL")
    fy_end = max(end_dates)
    fy_start = max(
        (s for s in start_dates if s.year == fy_end.year and s < fy_end),
        default=None,
    )
    return fy_end, fy_start


def detect_model_type(xbrl_bytes: bytes) -> str | None:
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


# ─── Persistence ────────────────────────────────────────────────────────
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
    Empty dict on failure — staging proceeds with filing_date=NULL.
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

    Idempotency: filing_reference has UNIQUE constraint, so re-runs no-op.
    """
    client = admin_client()
    payload = row.to_payload()

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
        # Older supabase-py without ignore_duplicates kwarg
        pass

    try:
        result = client.table("_stg_nbb_filings").insert(payload).execute()
        return "inserted" if (result.data or []) else "duplicate"
    except Exception as e:
        msg = str(e).lower()
        if "duplicate" in msg or "unique" in msg or "23505" in msg:
            return "duplicate"
        raise


# ─── β1 Pipeline (staging) ──────────────────────────────────────────────
def process_zip(
    zip_path: Path,
    party_id_override: str | None,
    dry_run: bool,
) -> tuple[dict, list[str], str | None]:
    """Stage every XBRL in the ZIP.

    Returns (counts, touched_filing_references, party_id):
      - counts: {inserted, duplicates, errors}
      - touched_refs: every filing_reference parsed (inserted OR duplicate),
        used by β3 to scope promotion to "things from this run".
      - party_id: resolved KBO→party_id lookup (None if all entries failed
        before resolution — defensive, shouldn't happen in practice).
    """
    inserted = duplicates = errors = 0
    party_id = party_id_override
    filing_dates: dict[str, dict] = {}
    touched_refs: list[str] = []

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

                if party_id is None:
                    party_id = lookup_party_id_by_kbo(kbo)
                    click.echo(f"  → resolved party_id={party_id} via KBO {kbo}")

                if not filing_dates and not dry_run:
                    filing_dates = fetch_filing_dates(kbo)

                meta = filing_dates.get(ref, {})
                filing_date: date | None = None
                if meta.get("filing_date"):
                    with contextlib.suppress(ValueError, TypeError):
                        filing_date = date.fromisoformat(meta["filing_date"][:10])
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
                touched_refs.append(ref)
                if outcome == "inserted":
                    inserted += 1
                    click.echo(f"  [✓]   {tag}")
                else:
                    duplicates += 1
                    click.echo(f"  [=]   {tag}  (already staged)")

            except Exception as e:
                errors += 1
                click.echo(f"  [✗]   {fname}  ERROR: {e}", err=True)

    counts = {"inserted": inserted, "duplicates": duplicates, "errors": errors}
    return counts, touched_refs, party_id


# ─── β3 Pipeline (sync-promote) ─────────────────────────────────────────
def _extract_year_data(staging_row: dict, api_key: str | None) -> dict:
    """Extract flat {pcmn_code: amount} from a staging row.

    Branches on taxonomy_format because the bulk-XBRL parser is
    incompatible with cbso-new's dimensional XBRL (LB-007 finding,
    2026-05-08): one bas:mXX code can have many dimensional contexts
    (part, bkd, ntr, ...) and parse_xbrl picks the wrong one.

    Strategy:
      cbso-new → fetch JSON-XBRL via NBB Authentic Data API. NBB
                 normalizes all dimensional complexity server-side and
                 returns canonical PCMN codes. Authoritative source,
                 used by Lane A and historical Power Query connector.
      pfs-old  → keep current parse_xbrl on raw_xbrl. Pre-2021 filings
                 don't have JSON-XBRL available; bulk-XBRL parsing
                 works fine for them (proven by 19/19 promote success
                 on AB LENS MOTOR's pfs-old years).

    Raises:
        NBBApiError on API failure for cbso-new
        RuntimeError on missing api_key when cbso-new filing requested
    """
    fmt = staging_row.get("taxonomy_format", "")

    if fmt == "cbso-new":
        if not api_key:
            raise RuntimeError(
                "NBB_API_KEY env var required to promote cbso-new filings. "
                "Lane A uses the same key — check .env."
            )
        ref = staging_row["filing_reference"]
        json_data = fetch_jsonxbrl(ref, api_key)
        if json_data is None:
            # 404/406/415 — JSON-XBRL not available (rare for FY≥2021 cbso-new).
            # No safe fallback: bulk-XBRL parser produces wrong values for v25+.
            raise NBBApiError(
                f"NBB returned no JSON-XBRL for {ref}. "
                f"cbso-new filings should have JSON-XBRL available — "
                f"check filing-reference validity."
            )
        # parse_rubrics returns {amounts: {code: float, _count_xxx: True, ...}, ...}
        # We only need the amounts dict — aggregator ignores unknown keys.
        parsed = parse_rubrics(json_data, {"referenceNumber": ref})
        return parsed["amounts"]

    # pfs-old (default fallback): bulk-XBRL parsing
    parsed = parse_xbrl(staging_row["raw_xbrl"])
    # parser.parse_xbrl returns data with tuple keys
    # (bas_or_pfs_key, pcmn_code, label, section). aggregator.aggregate_year
    # wants a flat {pcmn_code: amount} dict — extract index [1].
    return {key[1]: value for key, value in parsed["data"].items()}


def _build_canonical_jsonb(staging_row: dict, api_key: str | None) -> dict:
    """Parse staging row → AggregatedYear → canonical dict.

    Output is the same shape Lane A's writer uses (AggregatedYear.as_upsert_row),
    which is a superset of what fn_promote_nbb_filing reads. Extra keys are
    silently ignored by the function.

    The function's INSERT block reads these keys from the JSONB:
      period_label, period_end, period_type, revenue_eur_m, ebitda_eur_m,
      ebit_eur_m, net_income_eur_m, total_assets_eur_m, total_equity_eur_m,
      cash_eur_m, total_debt_eur_m, net_debt_eur_m, working_capital_eur_m,
      employees, amount_currency, fx_rate_to_eur, fx_date, nbb_model_type,
      confidence, notes
    All others (party_id, source_code, fiscal_year_*, nbb_filing_date) come
    from the staging row directly inside the function.
    """
    year_data = _extract_year_data(staging_row, api_key)

    # Dates come out of Postgres as ISO strings via supabase-py
    fy_end = date.fromisoformat(staging_row["fiscal_year_end"])
    fy_start_raw = staging_row.get("fiscal_year_start")
    fy_start = date.fromisoformat(fy_start_raw) if fy_start_raw else None
    fd_raw = staging_row.get("filing_date")
    filing_date = date.fromisoformat(fd_raw) if fd_raw else None

    # period_label convention: bare year of fiscal_year_end (matches
    # existing SRC_NBB rows from Lane A, e.g. "2024" not "FY2024").
    period_label = str(fy_end.year)

    agg = aggregate_year(
        year_data,
        period_label=period_label,
        period_end=fy_end,
        fiscal_year_start=fy_start,
        fiscal_year_end=fy_end,
        nbb_model_type=staging_row.get("nbb_model_type"),
        nbb_filing_date=filing_date,
    )

    return agg.as_upsert_row(staging_row["party_id"])


def _unwrap_rpc_response(data) -> dict:
    """Normalize supabase-py RPC return to a dict.

    Depending on client version, .data may be a dict, a single-element list,
    or (rarely) a JSON string. Be defensive.
    """
    if isinstance(data, list):
        data = data[0] if data else {}
    if isinstance(data, str):
        try:
            data = _json.loads(data)
        except (ValueError, TypeError):
            return {}
    return data if isinstance(data, dict) else {}


def promote_filings(filing_ids: list[str], api_key: str | None) -> dict:
    """Call fn_promote_nbb_filing per filing_id. Returns counts dict.

    Outcomes (from the function's RETURN jsonb):
      - 'parsed'     → wrote/updated a fact_financials row (winner)
      - 'superseded' → existing parsed sibling has later filing_date (loser)
      - exception   → counted as 'failed' (e.g. NULL filing_date,
                       already-parsed status, missing party_id, NBB API error)

    api_key: NBB_API_KEY for fetching JSON-XBRL on cbso-new filings.
             Required if any pending filing is taxonomy_format='cbso-new'.
             pfs-old filings don't use the API and tolerate api_key=None.
    """
    promoted = superseded = failed = 0
    client = admin_client()

    for fid in filing_ids:
        ref = "?"
        try:
            staging = (
                client.table("_stg_nbb_filings")
                .select("*")
                .eq("filing_id", fid)
                .single()
                .execute()
            )
            row = staging.data
            if not row:
                raise LookupError(f"staging row not found for {fid}")
            ref = row["filing_reference"]

            canonical = _build_canonical_jsonb(row, api_key)

            # Atomic call: function decides parsed vs superseded based on
            # filing_date vs siblings for (kbo_nr, fiscal_year_end), and
            # demotes losers in the same transaction.
            rpc_result = client.rpc(
                "fn_promote_nbb_filing",
                {
                    "p_filing_id": fid,
                    "p_canonical": canonical,
                },
            ).execute()

            payload = _unwrap_rpc_response(rpc_result.data)
            outcome = payload.get("outcome")

            if outcome == "parsed":
                promoted += 1
                demoted = payload.get("demoted_older_filings", 0)
                suffix = f" (demoted {demoted} older)" if demoted else ""
                click.echo(f"  [→]   {ref:<18}  promoted{suffix}")
            elif outcome == "superseded":
                superseded += 1
                reason = payload.get("reason", "")
                click.echo(f"  [↘]   {ref:<18}  superseded  {reason}")
            else:
                failed += 1
                click.echo(
                    f"  [?]   {ref:<18}  unexpected RPC payload: {payload!r}",
                    err=True,
                )

        except Exception as e:
            failed += 1
            click.echo(f"  [✗]   {ref:<18}  ERROR: {e}", err=True)

    return {"promoted": promoted, "superseded": superseded, "failed": failed}


def collect_pending_filing_ids(touched_refs: list[str]) -> list[str]:
    """Find _stg_nbb_filings rows for touched refs that are still pending.

    Re-runs are safe: rows already in 'parsed' or 'superseded' state are
    excluded, so β3 only acts on new work.
    """
    if not touched_refs:
        return []
    client = admin_client()
    resp = (
        client.table("_stg_nbb_filings")
        .select(f"filing_id, {STAGING_STATUS_COL}")
        .in_("filing_reference", touched_refs)
        .eq(STAGING_STATUS_COL, STATUS_PENDING)
        .execute()
    )
    return [r["filing_id"] for r in (resp.data or [])]


# ─── CLI ────────────────────────────────────────────────────────────────
@click.command()
@click.argument("zip_path", type=click.Path(exists=True, path_type=Path))
@click.option("--party-id", default=None, help="Skip KBO→party_identifiers lookup")
@click.option("--dry-run", is_flag=True, help="Parse without staging or promoting")
@click.option(
    "--promote/--no-promote",
    default=True,
    help="Run β3 sync-promote after staging (default: yes)",
)
def ingest(zip_path: Path, party_id: str | None, dry_run: bool, promote: bool) -> None:
    """Stage a NBB CBSO bulk-XBRL ZIP and promote to fact_financials.

    β1: idempotent insert into _stg_nbb_filings (on filing_reference).
    β3: parse each pending row, aggregate, call fn_promote_nbb_filing.

    Use --no-promote for staging-only (β1) behavior.
    """
    # ── β1 staging ──
    counts, touched_refs, party_id = process_zip(zip_path, party_id, dry_run)
    click.echo(
        f"\nStaging: {counts['inserted']} inserted, "
        f"{counts['duplicates']} duplicates, {counts['errors']} errors"
    )

    if dry_run:
        return

    if not promote:
        if counts["inserted"]:
            click.echo(
                f"         → {counts['inserted']} rows in _stg_nbb_filings.\n"
                f"         Promotion skipped (--no-promote). Re-run without\n"
                f"         the flag to promote pending rows."
            )
        return

    # ── β3 promote ──
    pending_ids = collect_pending_filing_ids(touched_refs)
    if not pending_ids:
        click.echo("\nPromotion: nothing pending — all touched filings already resolved.")
        return

    # NBB_API_KEY needed for cbso-new filings (JSON-XBRL via Authentic API).
    # Same env var Lane A's enrichment worker reads. Empty string → None so
    # _extract_year_data raises a clear RuntimeError if a cbso-new filing
    # is encountered without a key, instead of a confusing 401 from NBB.
    api_key = os.environ.get("NBB_API_KEY") or None
    if not api_key:
        click.echo(
            "  [!]   NBB_API_KEY not set — pfs-old filings will promote, "
            "cbso-new filings will fail with clear error.",
            err=True,
        )

    click.echo(f"\nPromoting {len(pending_ids)} pending filings via fn_promote_nbb_filing…\n")
    promo = promote_filings(pending_ids, api_key)
    click.echo(
        f"\nPromotion: {promo['promoted']} promoted, "
        f"{promo['superseded']} superseded, {promo['failed']} failed"
    )

    if promo["promoted"] and party_id:
        click.echo(
            f"         → {promo['promoted']} active SRC_NBB rows in fact_financials\n"
            f"         for party_id={party_id}"
        )


if __name__ == "__main__":
    ingest()
