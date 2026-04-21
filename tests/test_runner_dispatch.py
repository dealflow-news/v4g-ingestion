"""Phase 2.7 runner tests: claim-and-dispatch loop state transitions.

Mocks the claim RPC and worker function; verifies queue + run_log
lifecycle under success, worker-exception, empty-queue, missing-KBO,
and batch-level-crash scenarios.
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
        "open_run_log":    MagicMock(return_value=FAKE_RUN_ID),
        "close_run_log":   MagicMock(),
        "claim_next_task": MagicMock(),
        "complete_task":   MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(r.q, name, mock)
    return mocks


@pytest.fixture
def patch_resolve_kbo(monkeypatch):
    """Default: return Vela's KBO. Override per-test if needed."""
    mock = MagicMock(return_value="0771439317")
    monkeypatch.setattr(r, "_resolve_kbo", mock)
    return mock


def test_success_path(patch_q, patch_resolve_kbo, fake_task, monkeypatch):
    """Happy path: worker returns, task done, run_log completed with ok=1."""
    patch_q["claim_next_task"].return_value = fake_task
    fake_worker = MagicMock(return_value={"rows_written": 10})
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    result = r.process_batch()

    assert result == 1
    fake_worker.assert_called_once_with(
        party_id=FAKE_PARTY_ID, kbo="0771439317", run_id=FAKE_RUN_ID
    )
    patch_q["complete_task"].assert_called_once_with(
        str(FAKE_QUEUE_ID), outcome="done"
    )
    patch_q["close_run_log"].assert_called_once()
    kwargs = patch_q["close_run_log"].call_args.kwargs
    assert kwargs == {
        "status": "completed",
        "tasks_ok": 1,
        "tasks_failed": 0,
        "tasks_skipped": 0,
        "error_summary": None,
    }


def test_worker_exception_marks_failed(patch_q, patch_resolve_kbo, fake_task, monkeypatch):
    """Worker crash → task failed, error in queue.last_error, run still completes."""
    patch_q["claim_next_task"].return_value = fake_task
    fake_worker = MagicMock(side_effect=RuntimeError("NBB API down"))
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    r.process_batch()

    patch_q["complete_task"].assert_called_once()
    kwargs = patch_q["complete_task"].call_args.kwargs
    assert kwargs["outcome"] == "failed"
    assert "NBB API down" in kwargs["error"]
    # run_log closes 'completed' — the batch itself ran clean
    close_kwargs = patch_q["close_run_log"].call_args.kwargs
    assert close_kwargs["status"] == "completed"
    assert close_kwargs["tasks_failed"] == 1


def test_empty_queue(patch_q, patch_resolve_kbo):
    """No pending tasks → nothing dispatched, run_log still opens+closes."""
    patch_q["claim_next_task"].return_value = None

    result = r.process_batch()

    assert result == 0
    patch_q["complete_task"].assert_not_called()
    kwargs = patch_q["close_run_log"].call_args.kwargs
    assert kwargs["tasks_ok"] == 0
    assert kwargs["tasks_failed"] == 0
    assert kwargs["tasks_skipped"] == 0


def test_missing_kbo_skips_task(patch_q, fake_task, monkeypatch):
    """Party without KBO identifier → skipped (data issue, not worker bug)."""
    patch_q["claim_next_task"].return_value = fake_task
    monkeypatch.setattr(r, "_resolve_kbo", MagicMock(return_value=None))
    fake_worker = MagicMock()
    monkeypatch.setitem(r.WORKER_REGISTRY, "nbb_financials", fake_worker)

    r.process_batch()

    fake_worker.assert_not_called()
    kwargs = patch_q["complete_task"].call_args.kwargs
    assert kwargs["outcome"] == "skipped"
    assert "no KBO identifier" in kwargs["error"]
    close_kwargs = patch_q["close_run_log"].call_args.kwargs
    assert close_kwargs["tasks_skipped"] == 1


def test_run_log_always_closes_on_batch_crash(patch_q, patch_resolve_kbo):
    """Unexpected crash outside worker → run_log still closes with status=failed."""
    patch_q["claim_next_task"].side_effect = ConnectionError("DB unreachable")

    r.process_batch()  # should not raise

    patch_q["close_run_log"].assert_called_once()
    kwargs = patch_q["close_run_log"].call_args.kwargs
    assert kwargs["status"] == "failed"
    assert "DB unreachable" in kwargs["error_summary"]
