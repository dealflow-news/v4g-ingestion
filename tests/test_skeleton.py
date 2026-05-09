"""Smoke tests — keep CI green from day one.

Phase 2+ adds real tests for domain logic (taxonomy coverage, aggregator
formulas, writers against a test Supabase project).
"""
from __future__ import annotations


def test_package_importable() -> None:
    """Package itself imports without side-effects."""
    import src

    assert src.__version__ == "0.1.0"


def test_flask_app_builds() -> None:
    """Flask app factory produces a working app with /healthz."""
    # NOTE: no env vars set, no Supabase connection attempted — the Flask
    # app must not need them at import time. Writers do need them at call time.
    from src.web.app import app

    client = app.test_client()
    resp = client.get("/healthz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert "version" in data


def test_dashboard_renders() -> None:
    """Dashboard root renders without throwing."""
    from src.web.app import app

    client = app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"V4G Ingestion" in resp.data
    assert b"Phase status" in resp.data


def test_cli_import() -> None:
    """CLI module imports (click command registered, no env required)."""
    from src.cli import enrich  # noqa: F401


def test_runner_import() -> None:
    """Worker runner module imports (registry is empty but module loads)."""
    from src.enrichment import runner

    assert isinstance(runner.WORKER_REGISTRY, dict)
