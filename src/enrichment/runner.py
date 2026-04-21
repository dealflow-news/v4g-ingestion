"""Enrichment worker runner — main loop.

Phase 2.7c: lazy-open + Optie A (workers always raise on failure).

Per-batch flow per worker_type:
  1. Generate run_id = uuid4()  (client-side)
  2. Try claim_next_task(worker_type, run_id)
     - None → continue to next worker_type. NO run_log row.
     - dict → proceed.
  3. open_run_log(run_id, ...)
  4. Resolve KBO → if missing, complete_task(skipped), close_run_log(skipped=1)
  5. Dispatch worker:
     - returns normally → complete_task(done), close_run_log(ok=1)
     - raises          → complete_task(failed, err), close_run_log(failed=1)
  6. If anything in 3-5 itself crashes → close_run_log(status=failed, error_summary=...)

Workers always raise on failure (Optie A). They no longer return outcome
dicts that the runner has to inspect — the only signal is exception
propagation. Workers may still return summary data for run_log.summary
aggregation (reserved, not yet wired).

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
from uuid import UUID, uuid4

from src import __version__
from src.enrichment import queue as q
from src.enrichment.workers import nbb_financials
from src.persistence.supabase import admin_client

log = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
)


# Worker contract (Optie A, post-Phase-2.7c):
#   def worker(*, party_id: UUID, kbo: str, run_id: UUID) -> dict
# - MUST raise on any unrecoverable error. Runner maps raise → 'failed'.
# - MUST NOT return {"outcome": "failed"} — that path no longer exists.
# - SHOULD write its own object_log row via the writer (run_id threaded in).
# - MAY return a summary dict for future run_log.summary aggregation.
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
    """Process one batch — at most one task per registered worker_type.

    Returns total number of tasks processed (ok + failed + skipped).
    Empty polls produce zero database writes beyond the claim RPCs themselves.
    """
    if not WORKER_REGISTRY:
        return 0

    host = socket.gethostname() or platform.node() or "unknown"
    total = 0

    for worker_type, worker_fn in WORKER_REGISTRY.items():
        # Step 1: pre-generate run_id (client-side, lazy-open contract)
        run_id = uuid4()

        # Step 2: try to claim. If empty, do nothing — no run_log row.
        try:
            task = q.claim_next_task(worker_type=worker_type, run_id=run_id)
        except Exception:
            log.exception("claim crashed for worker_type=%s — skipping", worker_type)
            continue

        if task is None:
            log.debug("no pending tasks for %s", worker_type)
            continue

        # Step 3+: we have work. Open run_log NOW.
        try:
            q.open_run_log(
                run_id=run_id,
                worker_type=worker_type,
                host=host,
                app_version=__version__,
            )
        except Exception:
            # Open failed but queue row is already attached to run_id and
            # marked running. Best effort: try to mark task failed so it
            # won't stay stuck. run_log itself is unrecoverable here.
            log.exception("open_run_log crashed · run_id=%s queue_id=%s",
                          run_id, task["queue_id"])
            try:
                q.complete_task(
                    task["queue_id"],
                    outcome="failed",
                    error="open_run_log failed; see worker logs",
                )
            except Exception:
                log.exception("complete_task fallback also crashed")
            continue

        log.info("run_log opened · run_id=%s worker_type=%s queue_id=%s",
                 run_id, worker_type, task["queue_id"])

        # Step 4-5: dispatch
        ok, failed, skipped = 0, 0, 0
        error_summary: str | None = None
        try:
            outcome = _dispatch(worker_type, worker_fn, task, run_id)
            if outcome == "done":
                ok = 1
            elif outcome == "failed":
                failed = 1
            elif outcome == "skipped":
                skipped = 1
            total += 1
        except Exception as e:
            # _dispatch itself crashed (not the worker — _dispatch wraps that).
            # This means infrastructure broke between claim and worker.
            error_summary = f"dispatch infra crash: {e!r}"[:500]
            log.exception("unexpected crash in _dispatch · run_id=%s", run_id)

        # Step 6: always close
        try:
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
        except Exception:
            log.exception("close_run_log crashed · run_id=%s", run_id)

    return total


def _dispatch(
    worker_type: str,
    worker_fn: WorkerFn,
    task: dict,
    run_id: UUID,
) -> str:
    """Dispatch a single claimed task. Returns 'done' / 'failed' / 'skipped'.

    Resolves KBO, invokes worker, completes the queue task. Worker
    exceptions are caught here and translated to outcome='failed'.
    """
    queue_id = task["queue_id"]
    party_id = UUID(task["party_id"])

    # Resolve KBO. Missing → skipped (data issue, not worker bug).
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
        log.exception("worker raised · queue_id=%s", queue_id)
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
