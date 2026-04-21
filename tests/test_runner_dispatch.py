"""Phase 2.7c runner tests: lazy-open + raise-only outcome semantics.

Mocks the queue helpers and worker function; verifies queue + run_log
lifecycle under success, worker-exception, empty-queue, missing-KBO,
and infrastructure-crash scenarios.

Critical Phase 2.7c invariants verified:
- Empty poll → ZERO run_log writes (no open, no close)
- Successful claim → exactly ONE open + ONE close
- run_id is generated client-side, used identically across claim/open/close
"""
from __future__ import annotations

from unittest.mock import MagicMock
from uuid import UUID

import pytest

from src.enrichment import runner as r

FAKE_RUN_ID = UUID("11111111-1111-1111-1111-111111111111")
FAKE_QUEUE_ID = UUID("22222222-2222-2222-2222-222222222222")
FAKE_PARTY_ID = UUID("87f123ef-64e0-463a-b79c-ad4c0bef2855")  # Vela Group


@pytest.fixture
def fake_task() -> dict:
    return {
        "queue_id": str(FAKE_QUEUE_ID),
        "party_id": str(FAKE_PARTY_ID),
        "enrichment_type": "nbb_financials",
        "triggered_by_policy_code": "manual",
        "trigger_payload": {},
        "priority": 100,
        "attempts": 1,
        "enqueued_at": "2026-04-20T19:03:08+00:00",
    }


@pytest.fixture(autouse=True)
def patch_q(monkeypatch):
    """Replace all queue helpers with MagicMocks; no live DB calls."""
    mocks = {
        "open_run_log":    MagicMock(),
        "close_run_log":   MagicMock(),
        "claim_next_task": MagicMock(),
        "complete_task":   MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(r.q, name, mock)
    return mocks


@pytest.fixture(autouse=True)
def patch_uuid(monkeypatch):
    """Pin uuid4() to FAKE_RUN_ID so we can assert ID flow end-to-end."""
    monkeypatch.setattr(r, "uuid4", lambda: FAKE_RUN_ID)


@pytest.fixture
def patch_resolve_kbo(monkeypatch):
    """Default: return Vela's KBO. Override per-test if needed."""
    mock = MagicMock(return_value="0771439317")
    monkeypatch.setattr(r, "_resolve_kbo", mock)
    return mock


# ─── Lazy-open invariants ────────────────────────────────────────────────────

def test_empty_queue_writes_no_run_log(patch_q):
    """Lazy-open: empty claim → zero run_log writes (no open, no close).

    This is the central Phase 2.7c invariant. A worker polling an empty
    queue should produce no DB noise beyond the claim RPC itself.
    """
    patch_q["claim_next_task"].return_value = None

    result = r.process_batch()

    assert result == 0
    patch_q["claim_next_task"].assert_called_once_with(
        worker_type="nbb_financials", run_id=FAKE_RUN_ID
    )
    patch_q["open_run_log"].assert_not_called()
    patch_q["close_run_log"].assert_not_called()
    patch_q["complete_task"].assert_not_called()


def test_run_id_flows_consistently(patch_q, patch_resolve_kbo, fake_task, monkeypatch):
    """Same run_id is used for claim, open, dispatch, and close."""
    patch_q["claim_next_task"].return_value = fake_task
    fake_worker = MagicMock(return_value={"rows_written": 3})
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    r.process_batch()

    # claim called with FAKE_RUN_ID
    assert patch_q["claim_next_task"].call_args.kwargs["run_id"] == FAKE_RUN_ID
    # open called with same FAKE_RUN_ID
    assert patch_q["open_run_log"].call_args.kwargs["run_id"] == FAKE_RUN_ID
    # worker received same run_id
    assert fake_worker.call_args.kwargs["run_id"] == FAKE_RUN_ID
    # close called positionally with same run_id
    assert patch_q["close_run_log"].call_args.args[0] == FAKE_RUN_ID


# ─── Outcome paths ───────────────────────────────────────────────────────────

def test_success_path(patch_q, patch_resolve_kbo, fake_task, monkeypatch):
    """Worker returns normally → done, run_log completed with ok=1."""
    patch_q["claim_next_task"].return_value = fake_task
    fake_worker = MagicMock(return_value={"rows_written": 3, "kbo": "0771439317"})
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    result = r.process_batch()

    assert result == 1
    fake_worker.assert_called_once_with(
        party_id=FAKE_PARTY_ID, kbo="0771439317", run_id=FAKE_RUN_ID
    )
    patch_q["complete_task"].assert_called_once_with(
        str(FAKE_QUEUE_ID), outcome="done"
    )
    close_kwargs = patch_q["close_run_log"].call_args.kwargs
    assert close_kwargs == {
        "status": "completed",
        "tasks_ok": 1,
        "tasks_failed": 0,
        "tasks_skipped": 0,
        "error_summary": None,
    }


def test_worker_exception_marks_failed(patch_q, patch_resolve_kbo, fake_task, monkeypatch):
    """Worker raises → task failed, error in last_error, run completes."""
    patch_q["claim_next_task"].return_value = fake_task
    fake_worker = MagicMock(side_effect=RuntimeError("NBB API down"))
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    r.process_batch()

    complete_kwargs = patch_q["complete_task"].call_args.kwargs
    assert complete_kwargs["outcome"] == "failed"
    assert "NBB API down" in complete_kwargs["error"]
    # run_log closes 'completed' — the runner orchestration ran clean,
    # only the task itself failed
    close_kwargs = patch_q["close_run_log"].call_args.kwargs
    assert close_kwargs["status"] == "completed"
    assert close_kwargs["tasks_failed"] == 1
    assert close_kwargs["tasks_ok"] == 0


def test_missing_kbo_skips_task(patch_q, fake_task, monkeypatch):
    """Party without KBO identifier → skipped (data issue, not bug)."""
    patch_q["claim_next_task"].return_value = fake_task
    monkeypatch.setattr(r, "_resolve_kbo", MagicMock(return_value=None))
    fake_worker = MagicMock()
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    r.process_batch()

    fake_worker.assert_not_called()
    complete_kwargs = patch_q["complete_task"].call_args.kwargs
    assert complete_kwargs["outcome"] == "skipped"
    assert "no KBO identifier" in complete_kwargs["error"]
    close_kwargs = patch_q["close_run_log"].call_args.kwargs
    assert close_kwargs["tasks_skipped"] == 1


# ─── Infrastructure crashes ──────────────────────────────────────────────────

def test_claim_crash_skips_worker_and_writes_nothing(patch_q):
    """If claim itself raises, runner moves on without touching run_log."""
    patch_q["claim_next_task"].side_effect = ConnectionError("DB unreachable")

    r.process_batch()  # must not raise

    patch_q["open_run_log"].assert_not_called()
    patch_q["close_run_log"].assert_not_called()
    patch_q["complete_task"].assert_not_called()


def test_open_crash_marks_task_failed(patch_q, patch_resolve_kbo, fake_task, monkeypatch):
    """If open_run_log crashes after a successful claim, the queue task
    is marked failed (so it doesn't get stuck running). run_log itself
    is not closeable since it never opened."""
    patch_q["claim_next_task"].return_value = fake_task
    patch_q["open_run_log"].side_effect = RuntimeError("postgrest down")
    fake_worker = MagicMock()
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    r.process_batch()

    fake_worker.assert_not_called()  # never dispatched
    complete_kwargs = patch_q["complete_task"].call_args.kwargs
    assert complete_kwargs["outcome"] == "failed"
    assert "open_run_log" in complete_kwargs["error"]
    patch_q["close_run_log"].assert_not_called()  # never opened, can't close


def test_dispatch_infra_crash_marks_run_failed(patch_q, patch_resolve_kbo, fake_task, monkeypatch):
    """Crash in dispatch infra (after open) → run_log closes status=failed."""
    patch_q["claim_next_task"].return_value = fake_task
    fake_worker = MagicMock()
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)
    # Make _resolve_kbo crash to simulate infra failure inside _dispatch
    monkeypatch.setattr(
        r, "_resolve_kbo",
        MagicMock(side_effect=ConnectionError("party_identifiers unreachable")),
    )

    r.process_batch()  # must not raise

    close_kwargs = patch_q["close_run_log"].call_args.kwargs
    assert close_kwargs["status"] == "failed"
    assert "party_identifiers unreachable" in close_kwargs["error_summary"]


def test_no_workers_registered_does_nothing(patch_q, monkeypatch):
    """Empty registry → process_batch returns 0, no DB calls."""
    monkeypatch.setattr(r, "WORKER_REGISTRY", {})

    result = r.process_batch()

    assert result == 0
    patch_q["claim_next_task"].assert_not_called()
    patch_q["open_run_log"].assert_not_called()
