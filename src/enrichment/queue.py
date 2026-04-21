"""Queue helpers for gs_enrichment.

Thin wrappers around SECURITY DEFINER functions in GS-MIGRATE-021/022a/022c.
The DB is the source of truth; Python just routes calls.

All writes to gs_enrichment go through SECURITY DEFINER RPCs:
- enqueue, sweep_stale_parties (021)
- claim_next_task, complete_task (022a)
- open_run_log, close_run_log (022c)

Lazy-open pattern (Phase 2.7c): run_id is generated client-side by the
runner and passed to both claim_next_task and open_run_log. Runner opens
run_log only AFTER a successful claim — empty polls produce no run_log rows.
"""
from __future__ import annotations

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
    """Claim the oldest pending task and link it to a pre-generated run_id.

    The run_id MUST be generated client-side (uuid4) before this call.
    Lazy-open contract: if this returns None, do NOT call open_run_log —
    no work was claimed, so no run_log row should exist.

    Returns the claimed queue row as dict, or None if queue is empty for
    this worker_type. Server-side: FOR UPDATE SKIP LOCKED for safe
    parallel polling, transitions pending → running, attaches run_id,
    increments attempts.
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


# ─── Run-log RPCs (GS-MIGRATE-022c) ───────────────────────────────────────

def open_run_log(
    run_id: UUID,
    worker_type: str,
    host: str | None = None,
    app_version: str | None = None,
) -> None:
    """Open a run_log row via fn_open_run_log RPC.

    run_id is the SAME UUID passed to claim_next_task — generated
    client-side by the runner so queue row and run_log are atomically
    linked. Call only AFTER claim returned a task (lazy-open).
    """
    client = admin_client()
    client.schema("gs_enrichment").rpc(
        "fn_open_run_log",
        {
            "p_run_id":       str(run_id),
            "p_worker_type":  worker_type,
            "p_host":         host,
            "p_app_version":  app_version,
        },
    ).execute()


def close_run_log(
    run_id: UUID,
    *,
    status: str,
    tasks_ok: int,
    tasks_failed: int,
    tasks_skipped: int,
    error_summary: str | None = None,
) -> None:
    """Close a run_log row via fn_close_run_log RPC."""
    if status not in {"completed", "failed", "aborted"}:
        raise ValueError(f"invalid run_log status: {status!r}")

    client = admin_client()
    client.schema("gs_enrichment").rpc(
        "fn_close_run_log",
        {
            "p_run_id":         str(run_id),
            "p_status":         status,
            "p_tasks_ok":       tasks_ok,
            "p_tasks_failed":   tasks_failed,
            "p_tasks_skipped":  tasks_skipped,
            "p_error_summary":  error_summary,
        },
    ).execute()
