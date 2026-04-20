"""Enrichment worker runner — main loop.

Phase 1 skeleton: polls the queue, prints status, doesn't actually process
anything yet. Workers registered in WORKER_REGISTRY dict. In Phase 2 we add
the NBB financials worker as the first real entry.

Usage:
    python -m src.enrichment.runner                       # continuous mode
    python -m src.enrichment.runner --oneshot             # process one batch
    python -m src.enrichment.runner --type nbb_financials # filter by type
"""
from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable

log = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
)


# Worker registry — enrichment_type → worker function
# Populated as workers land in Phase 2+
WORKER_REGISTRY: dict[str, Callable[[dict], dict]] = {
    # 'nbb_financials': nbb_financials_worker,   # Phase 2
    # 'kbo_directors':  kbo_directors_worker,    # Phase 3
    # 'actor_classification': actor_classification_worker,  # Phase 4
}


def run_forever(poll_interval: int = 30) -> None:
    """Continuous polling loop — used by Render worker service."""
    log.info(
        "enrichment runner starting · registered workers: %s",
        list(WORKER_REGISTRY.keys()) or "(none yet — Phase 1 skeleton)",
    )
    if not WORKER_REGISTRY:
        log.warning(
            "no workers registered. Runner idle. "
            "Workers arrive in Phase 2 (NBB) / Phase 3 (KBO) / Phase 4 (classify)."
        )

    while True:
        try:
            process_batch()
        except Exception:
            log.exception("batch failed, continuing")
        time.sleep(poll_interval)


def process_batch() -> int:
    """Process one batch of pending tasks. Returns number processed."""
    if not WORKER_REGISTRY:
        return 0

    # Phase 2: claim N tasks, dispatch to registered workers, log outcomes
    log.debug("batch tick — nothing to do yet")
    return 0


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
