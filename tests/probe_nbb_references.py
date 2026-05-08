"""One-off probe: dump the full /references response from NBB CBSO API.

Goal: discover the exact JSON field name that carries filing_date
(depot-datum) per filing, so LB-005 can be a 5-line patch instead of
speculation.

Run from anywhere — the script auto-resolves the repo root on sys.path
and reads NBB_API_KEY from env first, then from .env in the repo root.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ─── Path setup ─────────────────────────────────────────────────────────
# Find repo root (assumes this script lives in tests/ or scripts/)
HERE = Path(__file__).resolve()
REPO_ROOT = HERE.parents[1]              # tests/.. or scripts/..
sys.path.insert(0, str(REPO_ROOT))


# ─── API-key resolution ─────────────────────────────────────────────────
def _read_api_key() -> str:
    """Env first, then .env in repo root, then config.json next to fetcher."""
    key = os.environ.get("NBB_API_KEY")
    if key:
        return key

    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("NBB_API_KEY=") and not line.startswith("#"):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                if val:
                    return val

    cfg_path = REPO_ROOT / "src" / "domain" / "nbb" / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        if cfg.get("api_key"):
            return cfg["api_key"]

    sys.exit(
        "NBB_API_KEY not found. Set the env var, or add it to .env in the "
        "repo root, or to src/domain/nbb/config.json."
    )


# ─── Probe ──────────────────────────────────────────────────────────────
def main() -> None:
    api_key = _read_api_key()

    from src.domain.nbb.fetcher import get_references

    vat = "0401452019"  # AB LENS MOTOR
    print(f"Fetching references for KBO {vat}…\n")
    refs = get_references(vat, api_key)
    print(f"Got {len(refs)} references in total.\n")

    if not refs:
        sys.exit("No references returned — check API key / quota.")

    # First reference, fully expanded
    print("=== First reference (full payload) ===")
    print(json.dumps(refs[0], indent=2, ensure_ascii=False, default=str))

    # Just the keys for fast scanning
    print("\n=== Keys present on a reference object ===")
    print(sorted(refs[0].keys()))

    # Try to spot anything date-like across all refs (helps if first is sparse)
    print("\n=== Date-like values across first 3 references ===")
    for i, r in enumerate(refs[:3]):
        print(f"\n[{i}] referenceNumber = {r.get('referenceNumber')}")
        for k, v in r.items():
            if isinstance(v, str) and len(v) >= 8 and v[:4].isdigit() and "-" in v:
                print(f"    {k:<35} = {v}")
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, str) and len(vv) >= 8 and vv[:4].isdigit() and "-" in vv:
                        print(f"    {k}.{kk:<33} = {vv}")


if __name__ == "__main__":
    main()
