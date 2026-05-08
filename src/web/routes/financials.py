"""Financials blueprint — Web-α party detail + Excel download.

Routes:
  GET /party/<party_id>                     HTML detail page
  GET /api/party/<party_id>/export.xlsx     Excel binary download

Both routes resolve the party via `party_query.get_party_meta()` and
fetch financials via `financial_export.get_financial_history()`. The
Excel response is byte-identical to the CLI export (same service builds
the workbook), satisfying the "one source of truth, two delivery paths"
doctrine.

Status-filter doctrine: the listing routes (`/parties`) restrict to
status='Active'. This blueprint deliberately does NOT — analysts can
inspect Dormant/Liquidated/Acquired entities by direct UUID lookup.
"""
from __future__ import annotations

import io
import logging
from uuid import UUID

from flask import Blueprint, abort, render_template, send_file

from src.services import financial_export, party_query
from src.web.charts import revenue_ebitda_svg

log = logging.getLogger(__name__)

bp = Blueprint("financials", __name__)


def _validate_uuid_or_404(party_id: str) -> str:
    """Validate UUID syntax, abort 404 on garbage input.

    Cheap pre-flight before hitting the DB — protects against UUID-
    parsing errors in supabase-py and keeps logs clean.
    """
    try:
        UUID(party_id)
    except (ValueError, AttributeError):
        abort(404)
    return party_id


@bp.route("/party/<party_id>")
def party_detail(party_id: str) -> str:
    """HTML detail page: header + chart + financials table + provenance."""
    _validate_uuid_or_404(party_id)
    party = party_query.get_party_meta(party_id)
    if party is None:
        abort(404)

    rows = financial_export.get_financial_history(party_id)
    chart_svg = revenue_ebitda_svg(rows)

    return render_template(
        "party_detail.html",
        party=party,
        rows=rows,           # service returns DESC by period_end → newest first in table
        chart_svg=chart_svg, # chart helper sorts ASC internally
    )


@bp.route("/api/party/<party_id>/export.xlsx")
def export_xlsx(party_id: str):
    """Excel download — same 3-sheet workbook as the CLI export."""
    _validate_uuid_or_404(party_id)
    party = party_query.get_party_meta(party_id)
    if party is None:
        abort(404)

    rows = financial_export.get_financial_history(party_id)
    if not rows:
        # No financial data — Excel would be empty pivot + raw + provenance.
        # Better UX: 404 so the analyst gets a clear "nothing to download"
        # signal rather than a workbook with three empty sheets.
        abort(404)

    xlsx_bytes = financial_export.build_xlsx_bytes(rows, party)
    filename = financial_export.suggest_filename(party)

    return send_file(
        io.BytesIO(xlsx_bytes),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )
