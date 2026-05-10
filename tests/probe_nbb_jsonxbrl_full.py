"""Probe: dump the full NBB JSON-XBRL response shape for one filing.

Goal: discover what NBB returns *beyond* the PCMN-coded financial lines,
so we can design fact_financials_lines + future fact_audit_engagement /
fact_board_snapshot tables based on real data, not guesses.

Picks a known cbso-new filing from AB LENS MOTOR (canary) and dumps:
  • top-level keys
  • per-rubric keys with one example value
  • any sections that aren't pure PCMN-numeric (likely metadata/header)
  • a focused look at fields that might carry auditor/director/address info

Run from repo root:
    python -m tests.probe_nbb_jsonxbrl_full

Reads .env so SUPABASE_URL / NBB_API_KEY / etc. work.
Writes a JSON dump to /tmp/nbb_jsonxbrl_dump_<ref>.json for further analysis.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

from src.domain.nbb.fetcher import fetch_jsonxbrl

# Load env
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

logging.basicConfig(
    level="INFO",
    format="%(asctime)s  %(levelname)-5s  %(message)s",
)
log = logging.getLogger(__name__)

# AB LENS MOTOR cbso-new filings (from earlier LB-007 fix work)
# Pick the 2024 filing — most recent, full schema, real data we already verified
CANARY_REF = "2025-00231176"


def _load_api_key() -> str:
    """Resolve NBB API key from common env var names.

    Tries the most likely names in order; first hit wins. The actual var
    name in your .env may differ — adapt below if needed.
    """
    candidates = (
        "NBB_API_KEY_AUTHENTIC",
        "NBB_AUTHENTIC_API_KEY",
        "NBB_API_KEY",
        "NBB_KEY",
        "CBSO_API_KEY",
    )
    for name in candidates:
        val = os.environ.get(name)
        if val:
            log.info("Using NBB API key from env var: %s", name)
            return val
    raise RuntimeError(
        f"No NBB API key found in env. Tried: {', '.join(candidates)}. "
        f"Set the appropriate env var in .env (same one Lane A worker uses)."
    )


def summarize(obj, depth: int = 0, max_depth: int = 4, path: str = "") -> None:
    """Walk a nested dict/list and print the structure with one example per leaf."""
    indent = "  " * depth
    if depth > max_depth:
        print(f"{indent}…")
        return

    if isinstance(obj, dict):
        for key, val in obj.items():
            full_path = f"{path}.{key}" if path else key
            if isinstance(val, dict):
                print(f"{indent}{key}: dict[{len(val)} keys]")
                summarize(val, depth + 1, max_depth, full_path)
            elif isinstance(val, list):
                if val and isinstance(val[0], dict):
                    print(f"{indent}{key}: list[{len(val)} dicts] · sample keys: "
                          f"{sorted(val[0].keys())[:8]}")
                    if len(val) >= 1:
                        summarize(val[0], depth + 1, max_depth, full_path + "[0]")
                else:
                    sample = val[:3] if val else []
                    print(f"{indent}{key}: list[{len(val)}] · sample: {sample}")
            else:
                # Truncate long string values
                val_str = str(val)
                if len(val_str) > 80:
                    val_str = val_str[:77] + "..."
                print(f"{indent}{key}: {type(val).__name__} = {val_str}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:2]):
            print(f"{indent}[{i}]")
            summarize(item, depth + 1, max_depth, f"{path}[{i}]")
    else:
        val_str = str(obj)
        if len(val_str) > 80:
            val_str = val_str[:77] + "..."
        print(f"{indent}{type(obj).__name__} = {val_str}")


def find_non_numeric_sections(obj, path: str = "", results: list | None = None) -> list:
    """Find leaf values that are NOT numeric — likely metadata."""
    if results is None:
        results = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            new_path = f"{path}.{key}" if path else key
            find_non_numeric_sections(val, new_path, results)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            find_non_numeric_sections(item, f"{path}[{i}]", results)
    elif isinstance(obj, str):
        # Skip purely-numeric strings (PCMN codes are numeric/slash patterns)
        if not obj.replace(".", "").replace(",", "").replace("/", "").replace("-", "").isdigit():
            if len(obj) >= 4 and len(obj) <= 200:
                results.append((path, obj))
    return results


def main() -> None:
    api_key = _load_api_key()
    log.info("Fetching JSON-XBRL for filing %s", CANARY_REF)
    data = fetch_jsonxbrl(CANARY_REF, api_key)
    if data is None:
        log.error("fetch_jsonxbrl returned None — filing not available as JSON-XBRL "
                  "(likely a pfs-old filing). Pick a cbso-new reference instead.")
        return

    # Save raw dump for further inspection — cross-platform temp path
    dump_path = Path(tempfile.gettempdir()) / f"nbb_jsonxbrl_dump_{CANARY_REF}.json"
    try:
        dump_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        log.info("Raw dump saved to %s · %d bytes", dump_path, dump_path.stat().st_size)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not save raw dump: %s · continuing with stdout-only output", e)

    print("\n" + "=" * 70)
    print(f"  NBB JSON-XBRL · filing {CANARY_REF} · structural dump")
    print("=" * 70 + "\n")

    print("━━━ TOP-LEVEL STRUCTURE ━━━")
    if isinstance(data, dict):
        for key in data:
            val = data[key]
            if isinstance(val, dict):
                kind = f"dict[{len(val)} keys]"
            elif isinstance(val, list):
                kind = f"list[{len(val)}]"
            else:
                kind = f"{type(val).__name__} = {str(val)[:60]}"
            print(f"  {key}: {kind}")

    print("\n━━━ DEEP STRUCTURE (4 levels) ━━━")
    summarize(data, max_depth=4)

    print("\n━━━ NON-NUMERIC LEAF VALUES (metadata candidates) ━━━")
    print("  These are paths with string values — auditor names, addresses, etc.")
    print("  Filtered to skip pure-numeric strings (PCMN codes, percentages).\n")
    non_numeric = find_non_numeric_sections(data)
    # Group by path-prefix to highlight sections
    seen_paths = set()
    for path, val in non_numeric[:80]:
        # Truncate index suffixes for grouping
        prefix = path.split("[")[0] if "[" in path else path
        if prefix not in seen_paths:
            print(f"  {path:50}  →  {val[:80]}")
            seen_paths.add(prefix)

    print("\n━━━ KEY PATTERN ANALYSIS ━━━")
    # Look for fields that might be auditor/director related
    keywords = ["audit", "commissaire", "réviseur", "reviseur", "bestuur",
                "director", "manager", "address", "adres", "phone", "email",
                "person", "contact", "shareholder", "kapital", "capital_held"]
    found = []
    def walk(obj, path=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                k_lower = k.lower()
                if any(kw in k_lower for kw in keywords):
                    found.append((f"{path}.{k}" if path else k, type(v).__name__, str(v)[:60]))
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:2]):
                walk(item, f"{path}[{i}]")
    walk(data)

    if found:
        print("  Fields matching auditor/director/address/contact keywords:")
        for path, kind, val in found:
            print(f"    {path:60}  ({kind})  →  {val}")
    else:
        print("  No obvious auditor/director/contact fields found in JSON-XBRL.")
        print("  → likely lives in a SEPARATE NBB endpoint, not in /accountingData.")
        print("  → check NBB API: /authentic/deposit/{ref}/legalRepresentatives, etc.")


if __name__ == "__main__":
    main()
