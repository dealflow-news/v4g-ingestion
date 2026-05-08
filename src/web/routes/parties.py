"""Parties blueprint — Web-α list + search.

Routes:
  GET /parties              HTML page: search bar + recent list (or filtered)
  GET /parties/search       JSON: autocomplete-friendly result array

Both routes use the same lookup logic (see `_resolve_query`):
  • If the query parses as a 10-digit KBO → exact KBO match (one result max)
  • Otherwise → fuzzy name search via display_name OR legal_name (ILIKE)

Status filter: only `Active` parties surface in lists/searches per Sprint-1
decision. Detail page (`/party/<uuid>`, future commit 3) bypasses this so
analysts can still inspect Dormant/Liquidated entities by direct UUID.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request

from src.services import party_query

log = logging.getLogger(__name__)

bp = Blueprint("parties", __name__)

# Hard cap on result count — defensive, both routes share this.
MAX_LIMIT = 50


def _looks_like_kbo(query: str) -> bool:
    """Strict: exactly 10 digits after stripping non-digit characters.

    Belgian KBO is always 10 digits (with leading zero for old companies).
    Modern enterprises 1xxx are also 10 digits. Anything else (9 digits
    missing leading zero, or 11+) goes through name-search fallback.
    """
    digits = "".join(c for c in query if c.isdigit())
    return len(digits) == 10


def _resolve_query(query: str, limit: int) -> list[dict]:
    """Single dispatch point — KBO match or name search."""
    if not query:
        return []
    if _looks_like_kbo(query):
        match = party_query.search_by_kbo(query)
        return [match] if match else []
    return party_query.search_by_name(query, limit=limit)


@bp.route("/parties")
def index() -> str:
    """List recent active parties, or search results if `?q=` present."""
    query = request.args.get("q", "").strip()
    if not query:
        parties = party_query.list_recent(limit=MAX_LIMIT)
    else:
        parties = _resolve_query(query, limit=MAX_LIMIT)
    return render_template("parties.html", parties=parties, query=query or None)


@bp.route("/parties/search")
def search_json():
    """JSON endpoint — autocomplete-friendly. Returns at most `limit` results.

    Response shape: {"results": [<party row dict>, ...], "query": "...", "count": N}
    """
    query = request.args.get("q", "").strip()
    try:
        limit = min(int(request.args.get("limit", 20)), MAX_LIMIT)
    except (TypeError, ValueError):
        limit = 20

    results = _resolve_query(query, limit=limit)
    return jsonify({
        "query": query,
        "count": len(results),
        "results": results,
    })
