#!/usr/bin/env python3
"""Diagnostic probe v4 — complete autopsy of cbso-new XBRL parsing.

255 am1 elements found, only 37 survive filtering. This probe shows
exactly what happens to each one. Also saves raw XBRL to disk for
offline inspection.

Run:
    python -m tests.probe_autopsy
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

_here = Path(__file__).resolve()
REPO_ROOT = next(
    (p for p in (_here.parent, *_here.parents) if (p / "src").is_dir()),
    _here.parent,
)
sys.path.insert(0, str(REPO_ROOT))

from dotenv import find_dotenv, load_dotenv  # noqa: E402

_envfile = find_dotenv(usecwd=True) or str(REPO_ROOT / ".env")
load_dotenv(_envfile, override=False)

from src.persistence.supabase import admin_client  # noqa: E402

NS_MET = "http://www.nbb.be/be/fr/cbso/dict/met"
CBSO_NEW_REF = "2025-00231176"  # AB LENS MOTOR fy 2024


def fetch_raw(ref: str) -> str:
    client = admin_client()
    resp = (
        client.table("_stg_nbb_filings")
        .select("raw_xbrl")
        .eq("filing_reference", ref)
        .single()
        .execute()
    )
    return resp.data["raw_xbrl"]


def build_ctx_map(root) -> dict:
    ctx_map = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag != "context":
            continue
        cid = el.get("id", "")
        period = {}
        dims = {}
        for child in el.iter():
            ct = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ct in ("instant", "startDate", "endDate"):
                period[ct] = child.text
            elif ct == "explicitMember":
                dim_name = child.get("dimension", "").split(":")[-1]
                dims[dim_name] = child.text or ""
        ctx_map[cid] = {"period": period, "dims": dims}
    return ctx_map


def get_period_date(ctx: dict) -> str:
    p = ctx.get("period", {})
    return p.get("instant") or p.get("endDate") or ""


def main() -> None:
    print(f"Loading cbso-new XBRL for {CBSO_NEW_REF}…")
    raw = fetch_raw(CBSO_NEW_REF)
    print(f"XBRL length: {len(raw):,} chars")

    # ── Save to disk for offline inspection ──────────────────────────
    out_dir = REPO_ROOT / "exports" / "diagnostic"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{CBSO_NEW_REF}.xbrl"
    out_file.write_text(raw, encoding="utf-8")
    print(f"Saved raw XBRL to: {out_file}")
    print()

    root = ET.fromstring(raw)
    ctx_map = build_ctx_map(root)

    # Find canonical end
    dates = [get_period_date(c) for c in ctx_map.values()]
    canonical_end = max(d for d in dates if d) if dates else ""
    print(f"Canonical end date: {canonical_end}")
    print(f"Total contexts: {len(ctx_map)}")
    print()

    # ── Walk all am1 elements with full diagnostic info ──────────────
    am1_elements = root.findall(f"{{{NS_MET}}}am1")
    print(f"Total met:am1 elements: {len(am1_elements)}")
    print()

    # Categorize each element by why it was kept or dropped
    stages: Counter[str] = Counter()
    bas_to_entries: dict[str, list[tuple]] = defaultdict(list)

    for el in am1_elements:
        ctx_ref = el.get("contextRef", "")
        ctx = ctx_map.get(ctx_ref, {})
        dims = ctx.get("dims", {})

        prd = dims.get("prd", "")
        bas = dims.get("bas", "")
        date = get_period_date(ctx)

        # Track all extra dimensions besides prd and bas
        other_dims = {k: v for k, v in dims.items() if k not in ("prd", "bas")}

        # Apply parser's filter logic step by step
        if prd and prd != "prd:m1":
            stages["dropped:prd_not_m1"] += 1
            continue
        if date and canonical_end and date != canonical_end:
            stages["dropped:wrong_date"] += 1
            continue
        if not bas:
            stages["dropped:empty_bas"] += 1
            continue
        if not el.text:
            stages["dropped:empty_text"] += 1
            continue
        try:
            val = float(el.text)
        except (ValueError, TypeError):
            stages["dropped:non_numeric"] += 1
            continue

        stages["kept"] += 1
        bas_to_entries[bas].append((val, ctx_ref, prd, other_dims))

    print("── am1 filter stage breakdown ──────────────────────────────")
    total = sum(stages.values())
    for stage, count in sorted(stages.items(), key=lambda x: -x[1]):
        pct = 100 * count / total if total else 0
        print(f"  {stage:<30}  {count:>5}  ({pct:5.1f}%)")
    print()

    # ── bas codes with multiple kept entries (collision risk) ────────
    print("── bas codes with MULTIPLE kept entries (only first wins!) ──")
    multi = {b: e for b, e in bas_to_entries.items() if len(e) > 1}
    if not multi:
        print("  (none — every bas has exactly one kept entry)")
    else:
        for bas, entries in sorted(multi.items(), key=lambda x: -len(x[1])):
            print(f"\n  {bas}  ({len(entries)} entries — parser keeps first):")
            for i, (val, ctx_ref, _prd, other_dims) in enumerate(entries):
                marker = "← KEPT" if i == 0 else "  dropped (dedup)"
                dims_str = ", ".join(f"{k}={v}" for k, v in other_dims.items()) or "(no extra dims)"
                print(f"    [{i}]  val={val:>15,.2f}  ctx={ctx_ref:<25}  {dims_str}  {marker}")
    print()

    # ── Specific bas:m70 deep-dive (revenue debug) ───────────────────
    print("── DEEP DIVE: bas:m70 (Omzet) ──────────────────────────────")
    m70_in_kept = bas_to_entries.get("bas:m70", [])
    print(f"  Kept entries for bas:m70: {len(m70_in_kept)}")
    for i, (val, ctx_ref, _prd, other_dims) in enumerate(m70_in_kept):
        dims_str = ", ".join(f"{k}={v}" for k, v in other_dims.items()) or "(no extra dims)"
        print(f"    [{i}]  val={val:>15,.2f}  ctx={ctx_ref:<25}  {dims_str}")

    # Also show ALL bas:m70 in entire XBRL (including dropped) — full audit
    print("\n  ALL bas:m70 in XBRL (incl. dropped) ──────────────────────")
    for el in am1_elements:
        ctx_ref = el.get("contextRef", "")
        ctx = ctx_map.get(ctx_ref, {})
        dims = ctx.get("dims", {})
        if dims.get("bas") != "bas:m70":
            continue
        prd = dims.get("prd", "")
        date = get_period_date(ctx)
        try:
            val = float(el.text or "0")
        except ValueError:
            val = 0
        other_dims = {k: v for k, v in dims.items() if k not in ("prd", "bas")}
        dims_str = ", ".join(f"{k}={v}" for k, v in other_dims.items()) or "(no extra dims)"
        print(f"    val={val:>15,.2f}  prd={prd:<8}  date={date}  ctx={ctx_ref}  {dims_str}")
    print()

    # ── Sorted dump of all kept bas codes by max value ──────────────
    print("── All kept bas codes sorted by max value (descending) ─────")
    print(f"  {'bas':<14}  {'count':>5}  {'first kept':>18}  {'sum':>18}")
    sorted_bas = sorted(
        bas_to_entries.items(),
        key=lambda x: -max(abs(e[0]) for e in x[1])
    )
    for bas, entries in sorted_bas:
        first_val = entries[0][0]
        sum_val = sum(e[0] for e in entries)
        print(f"  {bas:<14}  {len(entries):>5}  {first_val:>18,.0f}  {sum_val:>18,.0f}")


if __name__ == "__main__":
    main()
