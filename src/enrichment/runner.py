"""Enrichment worker runner — main loop.

Phase 2.7: claim-and-dispatch loop closed. Runner polls the queue, claims
tasks via fn_claim_enrichment_task, dispatches to WORKER_REGISTRY, and
closes tasks via fn_complete_task. run_log lifecycle owned here; writers
own object_log via the run_id threaded through.

Usage:
    python -m src.enrichment.runner             # continuous mode (Render)
    python -m src.enrichment.runner --oneshot   # process one batch and exit
"""
from __future__ import annotations

import logging
import os
import platform
import socket
import sys
import time
from collections.abc import Callable
from uuid import UUID

from src import __version__
from src.enrichment import queue as q
from src.enrichment.workers import nbb_financials
from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
)


# Worker signature: (*, party_id: UUID, kbo: str, run_id: UUID) -> dict
# Workers are expected to:
#   - raise on unrecoverable infrastructure errors (caught here → 'failed')
#   - write their own object_log row via the writer (run_id threaded in)
#   - return a summary dict (reserved for richer run_log.summary aggregation)
# NOTE: workers MAY also encode fail-soft outcomes in the returned dict (e.g.
# {"outcome": "failed"} without raising). Current runner treats those as 'done'.
# Tracked in governance as post-Phase-2.7 hardening follow-up.
WorkerFn = Callable[..., dict]

WORKER_REGISTRY: dict[str, WorkerFn] = {
    "nbb_financials": nbb_financials.run_for_party,
}


def run_forever(poll_interval: int = 30) -> None:
    """Continuous polling loop — used by Render worker service."""
    log.info(
        "enrichment runner starting · registered workers: %s",
        list(WORKER_REGISTRY.keys()) or "(none)",
    )
    while True:
        try:
            process_batch()
        except Exception:
            log.exception("batch failed, continuing")
        time.sleep(poll_interval)


def process_batch() -> int:
    """Process one batch — one task per registered worker_type.

    Returns total number of tasks processed (ok + failed + skipped).
    """
    if not WORKER_REGISTRY:
        return 0

    host = socket.gethostname() or platform.node() or "unknown"
    total = 0

    for worker_type, worker_fn in WORKER_REGISTRY.items():
        run_id = q.open_run_log(
            worker_type=worker_type,
            host=host,
            app_version=__version__,
        )
        log.info("run_log opened · run_id=%s worker_type=%s", run_id, worker_type)

        ok, failed, skipped = 0, 0, 0
        error_summary: str | None = None

        try:
            task = q.claim_next_task(worker_type=worker_type, run_id=run_id)
            if task is None:
                log.debug("no pending tasks for %s", worker_type)
            else:
                outcome = _dispatch(worker_type, worker_fn, task, run_id)
                if outcome == "done":
                    ok = 1
                elif outcome == "failed":
                    failed = 1
                elif outcome == "skipped":
                    skipped = 1
                total += 1
        except Exception as e:
            error_summary = f"batch-level crash: {e!r}"[:500]
            log.exception("unexpected crash in process_batch for %s", worker_type)

        q.close_run_log(
            run_id,
            status="completed" if error_summary is None else "failed",
            tasks_ok=ok,
            tasks_failed=failed,
            tasks_skipped=skipped,
            error_summary=error_summary,
        )
        log.info(
            "run_log closed · run_id=%s ok=%d failed=%d skipped=%d",
            run_id, ok, failed, skipped,
        )

    return total


def _dispatch(
    worker_type: str,
    worker_fn: WorkerFn,
    task: dict,
    run_id: UUID,
) -> str:
    """Dispatch a single task. Returns 'done' / 'failed' / 'skipped'.

    Handles KBO resolution, worker invocation, and queue completion. Any
    exception in the worker becomes a 'failed' outcome with the error
    stored in queue.last_error.
    """
    queue_id = task["queue_id"]
    party_id = UUID(task["party_id"])

    # Resolve KBO from party_identifiers. If missing → skipped, not failed
    # (no data source available is a data issue, not a worker bug).
    kbo = _resolve_kbo(party_id)
    if kbo is None:
        msg = f"no KBO identifier found for party_id {party_id}"
        log.warning(msg)
        q.complete_task(queue_id, outcome="skipped", error=msg)
        return "skipped"

    log.info(
        "dispatching · queue_id=%s party_id=%s kbo=%s worker=%s",
        queue_id, party_id, kbo, worker_type,
    )
    try:
        worker_fn(party_id=party_id, kbo=kbo, run_id=run_id)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:1000]
        log.exception("worker failed · queue_id=%s", queue_id)
        q.complete_task(queue_id, outcome="failed", error=err)
        return "failed"

    q.complete_task(queue_id, outcome="done")
    log.info("task done · queue_id=%s", queue_id)
    return "done"


def _resolve_kbo(party_id: UUID) -> str | None:
    """Look up the KBO identifier for a party. Returns None if not found."""
    client = admin_client()
    result = (
        client.table("party_identifiers")
        .select("id_value")
        .eq("party_id", str(party_id))
        .eq("id_type", "KBO")
        .limit(1)
        .execute()
    )
    if not result.data:
        return None
    return result.data[0]["id_value"]


def main() -> None:
    args = sys.argv[1:]
    if "--oneshot" in args:
        n = process_batch()
        log.info("oneshot complete · processed %d tasks", n)
        return
    poll_interval = int(os.environ.get("POLL_INTERVAL_SECONDS", "30"))
    run_forever(poll_interval=poll_interval)


if __name__ == "__main__":
    main()
