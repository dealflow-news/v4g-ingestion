"""Queue helpers for gs_enrichment.

Thin wrappers around the SECURITY DEFINER functions defined in
GS-MIGRATE-021 and GS-MIGRATE-022a. The DB is the source of truth;
Python just routes calls.

Phase 2.7 adds: claim_next_task, complete_task (via RPCs), and
run_log open/close helpers (direct service_role writes).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from src.persistence.supabase import admin_client

# ─── Enqueue (GS-MIGRATE-021) ─────────────────────────────────────────────

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
    result = (
        client.schema("gs_enrichment")
        .rpc(
            "enqueue",
            {
                "p_party_id": str(party_id),
                "p_enrichment_types": enrichment_types,
                "p_policy_code": policy_code,
                "p_trigger_payload": trigger_payload or {},
                "p_priority": priority,
            },
        )
        .execute()
    )
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


# ─── Claim / complete (GS-MIGRATE-022a) ───────────────────────────────────

def claim_next_task(
    worker_type: str,
    run_id: UUID,
) -> dict[str, Any] | None:
    """Claim the oldest pending task for a worker_type.

    Returns the claimed queue row as a dict, or None if the queue is empty
    for this worker_type. Marks the row as status='running' server-side.
    Uses FOR UPDATE SKIP LOCKED for safe parallel polling.
    """
    client = admin_client()
    result = (
        client.schema("gs_enrichment")
        .rpc(
            "fn_claim_enrichment_task",
            {"p_worker_type": worker_type, "p_run_id": str(run_id)},
        )
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None


def complete_task(
    queue_id: UUID | str,
    outcome: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Close a running task. outcome must be 'done'/'failed'/'skipped'."""
    if outcome not in {"done", "failed", "skipped"}:
        raise ValueError(f"invalid outcome: {outcome!r}")
    client = admin_client()
    result = (
        client.schema("gs_enrichment")
        .rpc(
            "fn_complete_task",
            {
                "p_queue_id": str(queue_id),
                "p_outcome": outcome,
                "p_error": error,
            },
        )
        .execute()
    )
    return result.data


# ─── Run-log helpers ──────────────────────────────────────────────────────
# Direct table writes via service_role. Orchestration metadata only the
# runner touches. Future: migrate to SECURITY DEFINER RPCs (fn_open_run_log,
# fn_close_run_log) for doctrine consistency — schema_register follow-up.

def open_run_log(
    worker_type: str,
    host: str,
    app_version: str,
) -> UUID:
    """Insert a run_log row with status='running'. Returns run_id."""
    client = admin_client()
    result = (
        client.schema("gs_enrichment")
        .table("run_log")
        .insert({
            "worker_type": worker_type,
            "status": "running",
            "host": host,
            "app_version": app_version,
        })
        .execute()
    )
    return UUID(result.data[0]["run_id"])


def close_run_log(
    run_id: UUID,
    *,
    status: str,
    tasks_ok: int,
    tasks_failed: int,
    tasks_skipped: int,
    error_summary: str | None = None,
) -> None:
    """Close a run_log row. status in {'completed','failed','aborted'}."""
    if status not in {"completed", "failed", "aborted"}:
        raise ValueError(f"invalid run_log status: {status!r}")
    tasks_total = tasks_ok + tasks_failed + tasks_skipped

    client = admin_client()
    client.schema("gs_enrichment").table("run_log").update({
        "status": status,
        "finished_at": datetime.now(UTC).isoformat(),
        "tasks_total": tasks_total,
        "tasks_ok": tasks_ok,
        "tasks_failed": tasks_failed,
        "tasks_skipped": tasks_skipped,
        "error_summary": error_summary,
    }).eq("run_id", str(run_id)).execute()
