"""Magic-link auth via Supabase. Feature-flagged on AUTH_ENABLED.

Local dev (`AUTH_ENABLED=false`, default): all routes open, `g.user` is a
placeholder so templates that reference it don't break.

Production (`AUTH_ENABLED=true`): magic-link required for every non-public
path. Public paths: /healthz, /login, /auth/*, /static/*.

Flow (Supabase token_hash, server-side):
  1. GET  /login                   email-input form
  2. POST /auth/magic-link         server calls sign_in_with_otp(email)
                                   → Supabase emails verify-link to user
  3. User clicks email link → 302 to /auth/callback?token_hash=…&type=…
  4. GET  /auth/callback           server verify_otp → session cookie
  5. Redirect to ?next= or /
  6. POST /auth/logout             clears cookie

Required Supabase dashboard config:
  Auth → URL Configuration → Site URL = production URL
  Auth → URL Configuration → Redirect URLs must include:
    https://<your-render>.onrender.com/auth/callback
    http://localhost:5000/auth/callback   (for local AUTH_ENABLED testing)

Required env vars when AUTH_ENABLED=true:
  SUPABASE_URL                (likely already set)
  SUPABASE_ANON_KEY           (public anon key — NOT service_role)
  AUTH_ALLOWED_EMAILS         (optional, comma-separated allowlist)
  SESSION_COOKIE_SECURE=true  (recommended for HTTPS prod)
  FLASK_SECRET                (long random string, already set)
"""
from __future__ import annotations

import logging
import os

from flask import (
    Blueprint,
    current_app,
    g,
    redirect,
    render_template,
    request,
    url_for,
)

log = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)

# Cookie name matches Supabase JS SDK convention so other tooling
# (DevTools, Supabase Studio) recognizes the session.
COOKIE_ACCESS = "sb-access-token"
SESSION_TTL_SECONDS = 60 * 60   # 1 hour — re-login then; OK for internal tool

# Paths that bypass auth entirely.
_PUBLIC_PATHS = {"/healthz", "/login"}
_PUBLIC_PREFIXES = ("/auth/", "/static/")


# ─── Config helpers ───────────────────────────────────────────────────────
def is_auth_enabled() -> bool:
    """Feature flag — default false for local dev convenience."""
    return os.environ.get("AUTH_ENABLED", "false").lower() in ("true", "1", "yes")


def _allowed_emails() -> set[str]:
    """Optional allowlist. Empty set = any email allowed (use cautiously)."""
    raw = os.environ.get("AUTH_ALLOWED_EMAILS", "").strip()
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _public_client():
    """Return a Supabase client using the anon key (NOT service_role).

    Lazy import: avoids requiring supabase-py for non-auth code paths and
    keeps test fixtures simple (mock _public_client, no real import needed).
    """
    url = os.environ.get("SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not anon_key:
        raise RuntimeError(
            "AUTH_ENABLED=true requires SUPABASE_URL + SUPABASE_ANON_KEY env vars"
        )
    from supabase import create_client
    return create_client(url, anon_key)


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES)


# ─── before_request hook ──────────────────────────────────────────────────
def check_auth():
    """Install via `app.before_request(auth.check_auth)`.

    Disabled mode: sets `g.user` to a dev placeholder so templates that
    reference user.email don't crash.

    Enabled mode: validates `sb-access-token` cookie via supabase-py;
    redirects to /login (preserving target via ?next=) on failure.
    """
    if not is_auth_enabled():
        g.user = {
            "email": "dev@local",
            "id": "00000000-0000-0000-0000-000000000000",
            "is_dev_placeholder": True,
        }
        return None

    if _is_public(request.path):
        return None

    token = request.cookies.get(COOKIE_ACCESS)
    if not token:
        return redirect(url_for("auth.login", next=request.full_path))

    try:
        client = _public_client()
        user_resp = client.auth.get_user(token)
        if user_resp and getattr(user_resp, "user", None):
            g.user = {
                "email": user_resp.user.email,
                "id": user_resp.user.id,
                "is_dev_placeholder": False,
            }
            return None
    except Exception as e:  # noqa: BLE001 — we want to log + redirect on any failure
        log.warning("token verification failed: %s", e)

    # Token invalid or verification raised — clear stale cookie + redirect
    response = redirect(url_for("auth.login", next=request.full_path))
    response.delete_cookie(COOKIE_ACCESS)
    return response


# ─── Routes ───────────────────────────────────────────────────────────────
@bp.route("/login")
def login() -> str:
    """Email-input form. Shows a 'check your email' confirmation after POST."""
    return render_template(
        "login.html",
        next=request.args.get("next") or "/",
        sent_to=request.args.get("sent"),
        error=request.args.get("error"),
        auth_enabled=is_auth_enabled(),
    )


@bp.route("/auth/magic-link", methods=["POST"])
def request_magic_link():
    """Send a magic-link email via Supabase auth.sign_in_with_otp."""
    email = (request.form.get("email") or "").strip().lower()
    next_url = request.form.get("next", "/")

    if not email or "@" not in email:
        return redirect(url_for("auth.login", error="invalid_email", next=next_url))

    allowed = _allowed_emails()
    if allowed and email not in allowed:
        # Don't tell them they're not allowed (info leak).
        # Just pretend it worked — internal log captures the actual reject.
        log.warning("magic-link request from non-allowlisted email: %s", email)
        return redirect(url_for("auth.login", sent=email, next=next_url))

    callback_url = url_for("auth.callback", _external=True)
    try:
        client = _public_client()
        client.auth.sign_in_with_otp({
            "email": email,
            "options": {"email_redirect_to": callback_url},
        })
    except Exception as e:  # noqa: BLE001
        log.error("supabase sign_in_with_otp failed: %s", e)
        return redirect(url_for("auth.login", error="send_failed", next=next_url))

    return redirect(url_for("auth.login", sent=email, next=next_url))


@bp.route("/auth/callback")
def callback():
    """Verify the magic-link token and set session cookie.

    Supabase redirects here with `token_hash` and `type=magiclink` (or
    `email`) in the query params after the user clicks the email link.
    """
    token_hash = request.args.get("token_hash")
    auth_type = request.args.get("type", "magiclink")
    next_url = request.args.get("next", "/")

    if not token_hash:
        return redirect(url_for("auth.login", error="missing_token"))

    try:
        client = _public_client()
        result = client.auth.verify_otp({
            "token_hash": token_hash,
            "type": auth_type,
        })
        session = getattr(result, "session", None)
        if not session or not getattr(session, "access_token", None):
            raise RuntimeError("no session returned")
        access_token = session.access_token
    except Exception as e:  # noqa: BLE001
        log.error("supabase verify_otp failed: %s", e)
        return redirect(url_for("auth.login", error="verify_failed"))

    response = redirect(next_url or "/")
    response.set_cookie(
        COOKIE_ACCESS,
        access_token,
        httponly=True,
        secure=current_app.config.get("SESSION_COOKIE_SECURE", False),
        samesite="Lax",
        max_age=SESSION_TTL_SECONDS,
    )
    return response


@bp.route("/auth/logout", methods=["GET", "POST"])
def logout():
    """Clear session cookie. GET allowed for nav-link convenience."""
    response = redirect(url_for("auth.login"))
    response.delete_cookie(COOKIE_ACCESS)
    return response
