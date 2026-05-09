"""Tests for src.web.auth — magic-link auth via Supabase.

Strategy: tests run with AUTH_ENABLED unset (default false), so all routes
work without supabase-py being installed. Auth-flow tests explicitly set
AUTH_ENABLED=true and mock `_public_client` so no real network calls happen.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.web import auth as auth_module
from src.web.app import create_app


# ─── Fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def auth_enabled_env(monkeypatch):
    """Force AUTH_ENABLED=true for the test."""
    monkeypatch.setenv("AUTH_ENABLED", "true")
    yield


# ─── Pure helpers ─────────────────────────────────────────────────────────
def test_is_auth_enabled_default_false(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    assert auth_module.is_auth_enabled() is False


@pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes"])
def test_is_auth_enabled_truthy(monkeypatch, value):
    monkeypatch.setenv("AUTH_ENABLED", value)
    assert auth_module.is_auth_enabled() is True


@pytest.mark.parametrize("value", ["false", "0", "no", "", "maybe"])
def test_is_auth_enabled_falsy(monkeypatch, value):
    monkeypatch.setenv("AUTH_ENABLED", value)
    assert auth_module.is_auth_enabled() is False


def test_allowed_emails_empty_when_unset(monkeypatch):
    monkeypatch.delenv("AUTH_ALLOWED_EMAILS", raising=False)
    assert auth_module._allowed_emails() == set()


def test_allowed_emails_parses_comma_list(monkeypatch):
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "a@b.com, B@C.com ,c@d.com")
    assert auth_module._allowed_emails() == {"a@b.com", "b@c.com", "c@d.com"}


def test_is_public_paths():
    assert auth_module._is_public("/healthz") is True
    assert auth_module._is_public("/login") is True
    assert auth_module._is_public("/auth/callback") is True
    assert auth_module._is_public("/auth/magic-link") is True
    assert auth_module._is_public("/static/style.css") is True
    assert auth_module._is_public("/parties") is False
    assert auth_module._is_public("/party/abc") is False
    assert auth_module._is_public("/api/party/abc/export.xlsx") is False


# ─── Auth disabled (default) — all routes open + dev placeholder ─────────
def test_disabled_parties_route_is_open(client):
    with patch("src.web.routes.parties.party_query.list_recent", return_value=[]):
        resp = client.get("/parties")
    assert resp.status_code == 200


def test_disabled_login_page_shows_dev_notice(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert b"Auth is uitgeschakeld" in resp.data


def test_disabled_topbar_shows_dev_user(client):
    """Templates must render without crashing when auth is off."""
    with patch("src.web.routes.parties.party_query.list_recent", return_value=[]):
        resp = client.get("/parties")
    assert b"dev@local" in resp.data
    assert b"<em>(dev)</em>" in resp.data


# ─── Auth enabled — gated routes redirect to /login ──────────────────────
def test_enabled_protected_route_redirects_to_login(client, auth_enabled_env):
    resp = client.get("/parties")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    assert "next=" in resp.headers["Location"]


def test_enabled_healthz_remains_open(client, auth_enabled_env):
    """Liveness probe must work even when auth is enabled."""
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_enabled_login_remains_open(client, auth_enabled_env):
    resp = client.get("/login")
    assert resp.status_code == 200


def test_enabled_static_files_remain_open(client, auth_enabled_env):
    resp = client.get("/static/style.css")
    assert resp.status_code == 200


def test_enabled_invalid_token_clears_cookie_and_redirects(client, auth_enabled_env):
    """Bad cookie → clear it + redirect to login (not 401/500 loop)."""
    client.set_cookie("sb-access-token", "not-a-real-token", domain="localhost")
    with patch.object(auth_module, "_public_client") as mock_factory:
        mock_factory.return_value.auth.get_user.side_effect = Exception("bad token")
        resp = client.get("/parties")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    # Cookie cleared in response
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "sb-access-token=" in set_cookie


def test_enabled_valid_token_allows_through(client, auth_enabled_env):
    fake_user = SimpleNamespace(
        email="chris@v4g.be",
        id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    fake_user_resp = SimpleNamespace(user=fake_user)

    client.set_cookie("sb-access-token", "valid-token-stub", domain="localhost")
    with (
        patch.object(auth_module, "_public_client") as mock_factory,
        patch("src.web.routes.parties.party_query.list_recent", return_value=[]),
    ):
        mock_factory.return_value.auth.get_user.return_value = fake_user_resp
        resp = client.get("/parties")
    assert resp.status_code == 200
    assert b"chris@v4g.be" in resp.data        # email rendered in topbar


# ─── /auth/magic-link ────────────────────────────────────────────────────
def test_magic_link_invalid_email_redirects_with_error(client, auth_enabled_env):
    resp = client.post("/auth/magic-link", data={"email": "not-an-email"})
    assert resp.status_code == 302
    assert "error=invalid_email" in resp.headers["Location"]


def test_magic_link_calls_supabase_sign_in_with_otp(client, auth_enabled_env):
    with patch.object(auth_module, "_public_client") as mock_factory:
        mock_client = mock_factory.return_value
        resp = client.post(
            "/auth/magic-link",
            data={"email": "chris@v4g.be", "next": "/parties"},
        )
    mock_client.auth.sign_in_with_otp.assert_called_once()
    args = mock_client.auth.sign_in_with_otp.call_args[0][0]
    assert args["email"] == "chris@v4g.be"
    assert "email_redirect_to" in args["options"]
    assert resp.status_code == 302
    assert "sent=chris" in resp.headers["Location"]
    assert "v4g.be" in resp.headers["Location"]


def test_magic_link_allowlist_blocks_silently(client, auth_enabled_env, monkeypatch):
    """Non-allowlisted email gets 'sent' UX without actually sending email."""
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "chris@v4g.be")
    with patch.object(auth_module, "_public_client") as mock_factory:
        resp = client.post("/auth/magic-link", data={"email": "outsider@evil.com"})
    # Did NOT call supabase
    mock_factory.assert_not_called()
    # But user sees same "sent" UX (no info leak)
    assert resp.status_code == 302
    assert "sent=outsider" in resp.headers["Location"]


def test_magic_link_allowlist_allows_listed(client, auth_enabled_env, monkeypatch):
    monkeypatch.setenv("AUTH_ALLOWED_EMAILS", "chris@v4g.be, other@v4g.be")
    with patch.object(auth_module, "_public_client") as mock_factory:
        client.post("/auth/magic-link", data={"email": "OTHER@V4G.BE"})
    mock_factory.return_value.auth.sign_in_with_otp.assert_called_once()


def test_magic_link_supabase_failure_redirects_with_error(client, auth_enabled_env):
    with patch.object(auth_module, "_public_client") as mock_factory:
        mock_factory.return_value.auth.sign_in_with_otp.side_effect = Exception("boom")
        resp = client.post("/auth/magic-link", data={"email": "chris@v4g.be"})
    assert "error=send_failed" in resp.headers["Location"]


# ─── /auth/callback ──────────────────────────────────────────────────────
def test_callback_missing_token_redirects_with_error(client, auth_enabled_env):
    resp = client.get("/auth/callback")
    assert "error=missing_token" in resp.headers["Location"]


def test_callback_verify_failure_redirects_with_error(client, auth_enabled_env):
    with patch.object(auth_module, "_public_client") as mock_factory:
        mock_factory.return_value.auth.verify_otp.side_effect = Exception("expired")
        resp = client.get("/auth/callback?token_hash=abc&type=magiclink")
    assert "error=verify_failed" in resp.headers["Location"]


def test_callback_success_sets_cookie_and_redirects_to_next(client, auth_enabled_env):
    fake_session = SimpleNamespace(access_token="fresh-jwt-token")
    fake_result = SimpleNamespace(session=fake_session)
    with patch.object(auth_module, "_public_client") as mock_factory:
        mock_factory.return_value.auth.verify_otp.return_value = fake_result
        resp = client.get("/auth/callback?token_hash=abc&type=magiclink&next=/parties")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/parties"
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "sb-access-token=fresh-jwt-token" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie


# ─── /auth/logout ────────────────────────────────────────────────────────
def test_logout_clears_cookie_and_redirects_to_login(client):
    """Logout works even when AUTH_ENABLED is off (no harm done)."""
    client.set_cookie("sb-access-token", "anything", domain="localhost")
    resp = client.get("/auth/logout")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    set_cookie = resp.headers.get("Set-Cookie", "")
    assert "sb-access-token=" in set_cookie    # being cleared


# ─── _public_client config validation ────────────────────────────────────
def test_public_client_raises_without_env(monkeypatch):
    """Clear, actionable error message when env vars missing."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        auth_module._public_client()
