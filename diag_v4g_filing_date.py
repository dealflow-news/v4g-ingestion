"""Diagnostic — call patched fetch_all_xbrl with V4G's KBO and show what
filing_date / model_type / deposit_type comes back per filing.

Goal: distinguish between
  - code bug (Patch 1 didn't land properly)  -> filing_date stays None
  - Render-deploy issue                       -> code works locally, fails on Render

Run from repo root:  python tests/diag_v4g_filing_date.py
"""
from __future__ import annotations

import os
from pathlib import Path

# Ensure repo root on sys.path AND .env loaded
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

if not os.environ.get("NBB_API_KEY"):
    sys.exit("NBB_API_KEY not set — check .env")

from src.domain.nbb.fetcher import fetch_all_xbrl

KBO = "0459499688"  # Ventures4Growth (V4G)

print(f"Fetching all XBRL for KBO {KBO} (no cache)...\n")
results = fetch_all_xbrl(KBO, os.environ["NBB_API_KEY"], use_cache=False)
print(f"Got {len(results)} results.\n")

# Header
print(f"{'year':<6} {'ref':<22} {'filing_date':<14} {'deposit_type':<12} {'model_type':<10}")
print("-" * 70)
for year, parsed, ref in results:
    fd = parsed.get("filing_date") or "(None)"
    dt = parsed.get("deposit_type") or "(None)"
    mt = parsed.get("model_type") or "(None)"
    print(f"{year:<6} {ref:<22} {fd:<14} {dt:<12} {mt:<10}")

# Quick verdict
all_have_fd = all(parsed.get("filing_date") for _, parsed, _ in results)
print()
if all_have_fd:
    print("VERDICT: parse_rubrics correctly surfaces filing_date for all results.")
    print("  -> Patch 1 works locally. If Render still writes NULL, deploy/code-on-Render is suspect.")
else:
    nulls = [ref for _, parsed, ref in results if not parsed.get("filing_date")]
    print(f"VERDICT: {len(nulls)}/{len(results)} results have filing_date=None.")
    print(f"  Affected refs: {nulls}")
    print("  -> Patch 1 missing or filing_meta doesn't carry DepositDate for these refs.")
