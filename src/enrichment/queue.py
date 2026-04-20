"""Queue helpers for gs_enrichment.

Thin wrappers around the SECURITY DEFINER functions defined in
GS-MIGRATE-021. The DB is the source of truth; Python just routes calls.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.persistence.supabase import admin_client


def enqueue(
    party_id: UUID | str,
    enrichment_types: list[str],
    policy_code: str = "manual",
    trigger_payload: dict[str, Any] | None = None,
    priority: int = 100,
) -> list[dict[str, Any]]:
    """Enqueue one or more enrichment tasks for a party.

    Idempotent: already-active (pending/running) tasks are silently skipped.
    Returns the newly-inserted queue rows.
    """
    client = admin_client()
    result = client.rpc(
        "enqueue",
        {
            "p_party_id": str(party_id),
            "p_enrichment_types": enrichment_types,
            "p_policy_code": policy_code,
            "p_trigger_payload": trigger_payload or {},
            "p_priority": priority,
        },
    ).execute()
    # RPC in gs_enrichment schema — need to call with schema prefix via .schema()
    # If this doesn't work, switch to: client.schema("gs_enrichment").rpc("enqueue", ...)
    return result.data or []


def sweep_stale_parties() -> dict[str, Any]:
    """Trigger the cadence-stale sweep. Returns {enqueued_count, policy_code}."""
    client = admin_client()
    result = (
        client.schema("gs_enrichment")
        .rpc("sweep_stale_parties", {})
        .execute()
    )
    return result.data[0] if result.data else {"enqueued_count": 0}


def claim_next_task(
    worker_type: str,
    run_id: UUID,
    batch_size: int = 1,
) -> list[dict[str, Any]]:
    """Claim the next pending task(s) for this worker type.

    Uses FOR UPDATE SKIP LOCKED semantics to allow multiple workers to poll
    safely. Marks claimed tasks as status='running' and attaches run_id.

    Stub — full implementation in Phase 2 when workers land. For now returns
    empty list.
    """
    # TODO Phase 2: implement via SQL function fn_claim_enrichment_task
    return []
