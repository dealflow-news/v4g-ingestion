"""Party lookup & search service.

Read-only. Wraps `src.persistence.supabase.admin_client()`. Used by:
  • `src.cli.export_financials_xlsx`  (existing — refactored to thin wrapper)
  • `src.web.routes.parties`           (Web-α — sprint 1)

Returns plain dicts (the supabase-py native shape). Service-layer dataclasses
deferred to a later sprint if/when type-pressure emerges.
"""
from __future__ import annotations

import logging
from uuid import UUID

from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)

# Party-registry statuses surfaced in lists/searches. Per Sprint-1 decision
# we hide Dormant/Liquidated/Defunct/Deprecated/Acquired/Merged. Detail
# pages (get_party_meta) bypass this — direct UUID lookup always works.
ACTIVE_STATUSES: tuple[str, ...] = ("Active",)

# Columns we surface in PartyMatch list/search responses.
# (kbo_nr is NOT included here — would force N+1 on party_identifiers.
# Use get_party_meta() for the detail page enrichment.)
_LIST_COLS = (
    "party_id, legal_name, display_name, country_iso2, party_type, "
    "status, actor_type, updated_at"
)

# Columns we surface in PartyDetail (one party, one fetch).
_DETAIL_COLS = (
    "party_id, legal_name, display_name, normalized_name, "
    "country_iso2, city, postal_code, website_domain, "
    "party_type, party_subtype, status, "
    "actor_type, actor_type_source, actor_type_confirmed, "
    "founded_year, enrichment_status, capital_tier, capital_tier_label, "
    "created_at, updated_at"
)


# ─── Internal helpers ─────────────────────────────────────────────────────
def _attach_kbo(party: dict) -> dict:
    """Enrich one party dict with `kbo_nr` from party_identifiers."""
    if not party:
        return party
    client = admin_client()
    resp = (
        client.table("party_identifiers")
        .select("id_value")
        .eq("party_id", party["party_id"])
        .eq("id_type", "KBO")
        .limit(1)
        .execute()
    )
    party["kbo_nr"] = resp.data[0]["id_value"] if resp.data else None
    return party


def _normalize_kbo(kbo: str) -> str:
    """Strip dots, spaces, BE-prefix from KBO input. Keep digits + leading 0s.

    Accepts: '0459.499.688', '0459 499 688', 'BE 0459499688', '0459499688'.
    """
    cleaned = (
        kbo.replace(".", "")
        .replace(" ", "")
        .replace("-", "")
        .upper()
    )
    if cleaned.startswith("BE"):
        cleaned = cleaned[2:]
    return cleaned


# ─── Public API ───────────────────────────────────────────────────────────
def get_party_meta(party_id: str | UUID) -> dict | None:
    """Fetch one party's full metadata + KBO. Returns None if not found.

    No status filter — direct UUID lookup is always allowed (analyst may
    legitimately need to inspect a Dormant/Liquidated party).
    """
    client = admin_client()
    resp = (
        client.table("party_registry")
        .select(_DETAIL_COLS)
        .eq("party_id", str(party_id))
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return _attach_kbo(resp.data[0])


def search_by_kbo(kbo: str) -> dict | None:
    """Exact-match KBO lookup. Returns party_meta dict or None.

    Input is normalized (dots/spaces/BE-prefix stripped) before lookup.
    """
    normalized = _normalize_kbo(kbo)
    if not normalized:
        return None

    client = admin_client()
    resp = (
        client.table("party_identifiers")
        .select("party_id")
        .eq("id_type", "KBO")
        .eq("id_value", normalized)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None
    return get_party_meta(resp.data[0]["party_id"])


def search_by_name(query: str, limit: int = 20) -> list[dict]:
    """Fuzzy match by display_name or legal_name (ILIKE).

    Filters to status='Active'. Returns up to `limit` matches.
    Caller is responsible for KBO enrichment if needed (avoid N+1).
    """
    q = query.strip()
    if not q:
        return []

    client = admin_client()
    pattern = f"%{q}%"

    resp = (
        client.table("party_registry")
        .select(_LIST_COLS)
        .or_(f"display_name.ilike.{pattern},legal_name.ilike.{pattern}")
        .in_("status", list(ACTIVE_STATUSES))
        .order("display_name", desc=False)
        .limit(limit)
        .execute()
    )
    return resp.data or []


def list_recent(limit: int = 50) -> list[dict]:
    """Recently-updated active parties. For dashboard / empty search state."""
    client = admin_client()
    resp = (
        client.table("party_registry")
        .select(_LIST_COLS)
        .in_("status", list(ACTIVE_STATUSES))
        .order("updated_at", desc=True)
        .limit(limit)
        .execute()
    )
    return resp.data or []
