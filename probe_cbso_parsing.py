#!/usr/bin/env python3
"""Diagnostic probe — what does parse_xbrl produce for cbso-new vs pfs-old?

Pulls one staged XBRL of each format from _stg_nbb_filings, runs the parser,
and prints what the aggregator would see. Reveals where revenue=1.47 comes
from for cbso-new years.

Run from repo root with venv activated:
    python -m tests.probe_cbso_parsing
"""
from __future__ import annotations

import sys
from pathlib import Path

# Path setup must precede src.* imports — ruff E402 noqa below
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env", override=False)

from src.domain.nbb.aggregator import EUR_SCALE  # noqa: E402
from src.domain.nbb.parser import parse_xbrl  # noqa: E402
from src.persistence.supabase import admin_client  # noqa: E402

# AB LENS MOTOR canary — has both formats staged
CBSO_NEW_REF = "2025-00231176"  # fy 2024
PFS_OLD_REF  = "2021-35600553"  # fy 2020

# What the aggregator looks for (from DIRECT_MAP + composites for EBITDA + employees)
WANTED_PCMN_CODES = {
    "70":    "revenue",
    "9901":  "ebit",
    "9904":  "net_income",
    "20/58": "total_assets",
    "10/15": "total_equity",
    "50/53": "cash",
    "630":   "_depreciation (for EBITDA)",
    "631/4": "_exc_depreciation (for EBITDA)",
    "635/8": "_provisions (for EBITDA)",
    "170/4": "_lt_financial_debt",
    "42/43": "_st_financial_debt",
    "29/58": "_current_assets",
    "42/48": "_current_liabilities",
    "9087":  "employees",
}


def fetch_staging_row(ref: str) -> dict:
    client = admin_client()
    resp = (
        client.table("_stg_nbb_filings")
        .select("filing_reference, kbo_nr, fiscal_year_end, taxonomy_format, raw_xbrl")
        .eq("filing_reference", ref)
        .single()
        .execute()
    )
    return resp.data


def probe(ref: str, header: str) -> None:
    print(f"\n{'═' * 78}")
    print(f"  {header}  —  filing_reference={ref}")
    print(f"{'═' * 78}")

    row = fetch_staging_row(ref)
    print(f"  KBO:             {row['kbo_nr']}")
    print(f"  fy_end:          {row['fiscal_year_end']}")
    print(f"  taxonomy_format: {row['taxonomy_format']}")
    print(f"  XBRL length:     {len(row['raw_xbrl'])} chars")

    parsed = parse_xbrl(row["raw_xbrl"])
    print(f"\n  Schema info: {parsed['schema']}")
    print(f"  Total data entries: {len(parsed['data'])}")

    # Build the year_data exactly as the CLI does
    year_data = {key[1]: value for key, value in parsed["data"].items()}

    # ── 1. What the aggregator wants vs what's present ────────────────────
    print("\n  ── Aggregator lookup table ────────────────────────────────────")
    print(f"  {'PCMN code':<10}  {'Maps to':<35}  {'Found?':<10}  {'Raw value':>15}")
    print(f"  {'-' * 10}  {'-' * 35}  {'-' * 10}  {'-' * 15}")
    for code, what in WANTED_PCMN_CODES.items():
        v = year_data.get(code)
        found = "yes" if v is not None else "MISSING"
        v_str = f"{v:,.2f}" if isinstance(v, (int, float)) else "—"
        print(f"  {code:<10}  {what:<35}  {found:<10}  {v_str:>15}")

    # ── 2. Show what aggregator would compute ────────────────────────────
    print("\n  ── After aggregator scaling (raw / 1,000,000 = EUR millions) ──")
    for code in ["70", "9901", "9904", "20/58", "10/15", "50/53"]:
        v = year_data.get(code)
        if v is not None:
            scaled = float(v) / float(EUR_SCALE)
            print(f"    pcmn={code:<8}  {scaled:>12.3f} EUR M")
        else:
            print(f"    pcmn={code:<8}  None (will be NULL in fact_financials)")

    # ── 3. What pcmn codes ARE in the XBRL (everything that exists) ──────
    print("\n  ── All PCMN codes present in this XBRL ───────────────────────")
    sorted_codes = sorted(year_data.keys())
    print(f"  {len(sorted_codes)} codes: {sorted_codes}")

    # ── 4. Search for anything with value near 30M (real revenue) ────────
    # AB LENS MOTOR's revenue is roughly 25-37M EUR per year
    print("\n  ── Values in range 20M–50M (looking for hidden revenue) ──────")
    for key, value in parsed["data"].items():
        if isinstance(value, (int, float)) and 20_000_000 <= float(value) <= 50_000_000:
            bas, pcmn, lbl, section = key
            print(f"    {bas:<14}  pcmn={pcmn:<8}  {value:>15,.0f}  {section}  {lbl}")

    # ── 5. Search for revenue-like labels (fallback if codes don't match) ─
    print("\n  ── Entries with 'omzet' or 'turnover' in label ─────────────────")
    for key, value in parsed["data"].items():
        bas, pcmn, lbl, section = key
        if "omzet" in lbl.lower() or "turnover" in lbl.lower() or "revenue" in lbl.lower():
            print(f"    {bas:<14}  pcmn={pcmn:<8}  {value:>15,.2f}  {lbl}")


if __name__ == "__main__":
    probe(PFS_OLD_REF, "PFS-OLD (works correctly — control)")
    probe(CBSO_NEW_REF, "CBSO-NEW (broken — yields 1.47 placeholder)")
    print(f"\n{'═' * 78}")
    print("  Done. Compare the two outputs to find the disconnect.")
    print(f"{'═' * 78}\n")
