"""Supabase client factories.

The platform uses two distinct clients:
- `admin_client()` — authenticated with SERVICE_ROLE key. Bypasses RLS.
  Server-side only. Used by writers (fact_financials, person_registry, etc.).
- `public_client()` — authenticated with ANON key. Honors RLS.
  Used for read-only browser-shareable queries.

Separation enforced at call-site, not at runtime. Keep service-role out of
any code path that could be exposed to a browser.
"""
from __future__ import annotations

import os
from functools import lru_cache

from supabase import Client, create_client


def _require_env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise RuntimeError(
            f"Required env var {key} not set. See .env.example."
        )
    return val


@lru_cache(maxsize=1)
def admin_client() -> Client:
    """Supabase client with service_role — bypasses RLS.

    Use for writers. Do NOT expose in routes that return data to browsers.
    """
    return create_client(
        _require_env("SUPABASE_URL"),
        _require_env("SUPABASE_SERVICE_ROLE_KEY"),
    )


@lru_cache(maxsize=1)
def public_client() -> Client:
    """Supabase client with anon key — honors RLS.

    Use for reads where RLS policies should apply.
    """
    return create_client(
        _require_env("SUPABASE_URL"),
        _require_env("SUPABASE_ANON_KEY"),
    )
