"""Flask test-client tests for the parties blueprint.

All tests mock `src.services.party_query` — no DB calls. Pure routing /
template / heuristic verification. DB integration is exercised separately
via smoke runs against the AB LENS MOTOR canary.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.web.app import create_app
from src.web.routes.parties import _looks_like_kbo


# ─── Fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


# ─── _looks_like_kbo (pure heuristic) ─────────────────────────────────────
def test_looks_like_kbo_plain_10_digits():
    assert _looks_like_kbo("0459499688") is True


def test_looks_like_kbo_with_dots():
    assert _looks_like_kbo("0459.499.688") is True


def test_looks_like_kbo_with_be_prefix_and_spaces():
    assert _looks_like_kbo("BE 0459499688") is True
    assert _looks_like_kbo("BE0459499688") is True


def test_looks_like_kbo_with_dashes():
    assert _looks_like_kbo("0459-499-688") is True


def test_looks_like_kbo_rejects_short_input():
    """9 digits (missing leading zero) — strict heuristic rejects."""
    assert _looks_like_kbo("459499688") is False


def test_looks_like_kbo_rejects_too_long():
    assert _looks_like_kbo("12345678901") is False


def test_looks_like_kbo_rejects_plain_text():
    assert _looks_like_kbo("Acme Holdings") is False
    assert _looks_like_kbo("") is False


def test_looks_like_kbo_rejects_mixed_with_few_digits():
    """Mostly text with a year — name search territory."""
    assert _looks_like_kbo("Acme 2024") is False


# ─── /parties — index route ─────────────────────────────────────────────
def test_parties_index_no_query_calls_list_recent(client):
    fake = [{
        "party_id": "abc-123", "display_name": "Test Co", "legal_name": "Test Co BV",
        "country_iso2": "BE", "party_type": "company", "status": "Active",
        "actor_type": "MID_MARKET", "updated_at": "2026-05-01T00:00:00Z",
    }]
    with patch("src.web.routes.parties.party_query.list_recent", return_value=fake) as mock:
        resp = client.get("/parties")
    mock.assert_called_once_with(limit=50)
    assert resp.status_code == 200
    assert b"Test Co" in resp.data
    assert b"Recent" in resp.data    # header copy


def test_parties_index_name_query_calls_search_by_name(client):
    fake = [{
        "party_id": "x", "display_name": "Acme Corp", "legal_name": "Acme NV",
        "country_iso2": "BE", "party_type": "company", "status": "Active",
        "actor_type": None, "updated_at": "2026-05-01T00:00:00Z",
    }]
    with patch("src.web.routes.parties.party_query.search_by_name", return_value=fake) as mock:
        resp = client.get("/parties?q=Acme")
    mock.assert_called_once_with("Acme", limit=50)
    assert resp.status_code == 200
    assert b"Acme Corp" in resp.data
    assert b"resultaten" in resp.data    # search-result copy
    assert b"Acme" in resp.data          # query echoed back


def test_parties_index_kbo_query_calls_search_by_kbo(client):
    fake_match = {
        "party_id": "x", "display_name": "Lens Motor Garage",
        "legal_name": "AB LENS MOTOR", "country_iso2": "BE",
        "party_type": "company", "status": "Active",
        "actor_type": None, "kbo_nr": "0401452019",
    }
    with (
        patch("src.web.routes.parties.party_query.search_by_kbo",
              return_value=fake_match) as mock_kbo,
        patch("src.web.routes.parties.party_query.search_by_name") as mock_name,
    ):
        resp = client.get("/parties?q=0401452019")
    mock_kbo.assert_called_once_with("0401452019")
    mock_name.assert_not_called()
    assert resp.status_code == 200
    assert b"Lens Motor Garage" in resp.data


def test_parties_index_kbo_no_match_returns_empty(client):
    with patch("src.web.routes.parties.party_query.search_by_kbo", return_value=None):
        resp = client.get("/parties?q=9999999999")
    assert resp.status_code == 200
    assert b"0 resultaten" in resp.data


def test_parties_index_empty_query_after_strip_falls_back_to_recent(client):
    """Whitespace-only query must NOT trigger a name search."""
    with patch("src.web.routes.parties.party_query.list_recent", return_value=[]) as mock:
        resp = client.get("/parties?q=%20%20%20")
    mock.assert_called_once_with(limit=50)
    assert resp.status_code == 200


# ─── /parties/search — JSON route ────────────────────────────────────────
def test_search_json_empty_query_returns_empty_results(client):
    resp = client.get("/parties/search")
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.get_json() == {"query": "", "count": 0, "results": []}


def test_search_json_name_query_returns_results(client):
    fake = [
        {"party_id": "a", "display_name": "Acme", "legal_name": "Acme NV",
         "country_iso2": "BE"},
        {"party_id": "b", "display_name": "Acme Holdings", "legal_name": "Acme Holdings BV",
         "country_iso2": "NL"},
    ]
    with patch("src.web.routes.parties.party_query.search_by_name", return_value=fake):
        resp = client.get("/parties/search?q=Acme&limit=10")
    body = resp.get_json()
    assert body["query"] == "Acme"
    assert body["count"] == 2
    assert len(body["results"]) == 2
    assert body["results"][0]["display_name"] == "Acme"


def test_search_json_kbo_query_routes_to_kbo_lookup(client):
    fake_match = {"party_id": "x", "display_name": "X", "kbo_nr": "0401452019"}
    with patch("src.web.routes.parties.party_query.search_by_kbo",
               return_value=fake_match) as mock_kbo:
        resp = client.get("/parties/search?q=0401452019")
    mock_kbo.assert_called_once_with("0401452019")
    body = resp.get_json()
    assert body["count"] == 1


def test_search_json_caps_limit_at_max(client):
    """limit=999 must be capped to MAX_LIMIT=50."""
    with patch("src.web.routes.parties.party_query.search_by_name",
               return_value=[]) as mock:
        client.get("/parties/search?q=test&limit=999")
    # Verify the service was called with limit=50, not 999
    assert mock.call_args.kwargs["limit"] == 50


def test_search_json_handles_invalid_limit_param(client):
    """limit=foo must default to 20, not 500."""
    with patch("src.web.routes.parties.party_query.search_by_name",
               return_value=[]) as mock:
        client.get("/parties/search?q=test&limit=foo")
    assert mock.call_args.kwargs["limit"] == 20


# ─── /healthz still works (regression check) ─────────────────────────────
def test_healthz_unchanged(client):
    """Ensure the parties blueprint registration didn't break /healthz."""
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"
