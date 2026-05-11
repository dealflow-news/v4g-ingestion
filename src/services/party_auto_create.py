"""Auto-create a party stub when a KBO is encountered but no party exists.

Used by zip_ingester when uploaded XBRL files reference KBOs not yet in
party_registry. Creates a minimal P3 stub so the rest of the ingestion
pipeline (filings, lines, evidence) can attach.

Idempotent: re-running for the same KBO returns the existing party_id.
"""
from __future__ import annotations

import logging
import re
from typing import Optional
from uuid import UUID

log = logging.getLogger(__name__)


def normalize_kbo(kbo: str) -> str:
    """Strip BE prefix, dots, dashes, spaces. Pad to 10 digits."""
    if not kbo:
        return ""
    s = re.sub(r"[^0-9]", "", kbo)
    return s.zfill(10) if 9 <= len(s) <= 10 else s


def resolve_or_create_party(
    supabase,
    kbo: str,
    *,
    display_name: Optional[str] = None,
    legal_form_code: Optional[str] = None,
) -> tuple[UUID, bool]:
    """Look up party by KBO; create a stub if missing.

    Returns (party_id, was_created). On creation:
      - party_registry: party_type='company', country_iso2='BE',
                        status='Active', enrichment_tier='P3'
      - party_identifiers: id_type='KBO', is_primary=true
      - party_profile: auto-created via existing DB trigger

    party_identifiers has UNIQUE(id_type, id_value) so concurrent runs
    will either return the existing party_id (lookup hit) or hit the
    unique-constraint on the identifier insert; we currently treat the
    second as an error since concurrent ZIP uploads for the same KBO are
    not expected. If they become expected later, wrap insert in a
    retry-on-conflict that re-runs the lookup.

    Args:
        supabase:        Supabase client (admin/service-role).
        kbo:             KBO number; will be normalized to 10 digits.
        display_name:    Display name; falls back to "Unknown (KBO <n>)".
        legal_form_code: Optional 3-digit NBB code (e.g., "014" SA, "016" SPRL).
                         Stored in legal_form_detail when creating.

    Raises:
        ValueError:   kbo invalid (not 10 digits after normalization).
        RuntimeError: insert returned no data (DB misconfiguration).
        Exception:    other DB errors (propagated).
    """
    norm = normalize_kbo(kbo)
    if len(norm) != 10:
        raise ValueError(f"invalid KBO: {kbo!r} (normalized={norm!r})")

    # Step 1: lookup
    res = (
        supabase.table("party_identifiers")
        .select("party_id")
        .eq("id_type", "KBO")
        .eq("id_value", norm)
        .limit(1)
        .execute()
    )
    if res.data:
        party_id = UUID(res.data[0]["party_id"])
        log.info("party_resolve.hit kbo=%s party_id=%s", norm, party_id)
        return party_id, False

    # Step 2: create stub
    name = display_name or f"Unknown (KBO {norm})"
    party_row = {
        "display_name":    name,
        "legal_name":      name,
        "party_type":      "company",
        "country_iso2":    "BE",
        "status":          "Active",
        "enrichment_tier": "P3",
    }
    if legal_form_code:
        party_row["legal_form_detail"] = legal_form_code

    insert = supabase.table("party_registry").insert(party_row).execute()
    if not insert.data:
        raise RuntimeError(f"party_registry insert returned no data for kbo={norm}")
    party_id = UUID(insert.data[0]["party_id"])

    # Step 3: link KBO identifier
    supabase.table("party_identifiers").insert({
        "party_id":        str(party_id),
        "id_type":         "KBO",
        "id_value":        norm,
        "issuing_country": "BE",
        "is_primary":      True,
    }).execute()

    log.info("party_create.ok kbo=%s party_id=%s name=%s", norm, party_id, name)
    return party_id, True


__all__ = ["resolve_or_create_party", "normalize_kbo"]
