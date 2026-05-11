"""V4G Financial Tools — Flask blueprint.

Three modes:

- ``Fetch``      — enqueue a NBB ingestion task; live worker triple-writes
                   to fact_filings + fact_financials_lines + fact_financials_evidence.
- ``Upload ZIP`` — synchronous Lane B import of a ZIP of XBRL/JSON-XBRL files.
                   v1: behind ``TOOLS_UPLOAD_ENABLED`` env flag (off by default).
- ``Export``     — read canonical financials from the DB and stream an .xlsx.

Routes:

- ``GET  /tools/``                       — render tools.html (UI shell).
- ``POST /tools/fetch``                  — JSON body, enqueue task.
- ``GET  /tools/status/<queue_id>``      — JSON status of the queue row.
- ``GET  /tools/export``                 — return .xlsx (query params).
- ``POST /tools/upload-zip``             — multipart upload (feature-flagged).

Auth: inherits the app-wide ``before_request`` gate from ``src/web/auth.py``
(``AUTH_ENABLED`` env var). No per-route RBAC in v1 — explicitly mono-user
prototype scope. The module is structured to allow per-route gates later
without refactoring (each route is small and isolated).

All writes go through server-side helpers; no direct DB writes from the
browser. Audit trail is preserved via the existing run_log + object_log
infrastructure (worker writes), and the queue row itself for fetch jobs.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    render_template,
    request,
    send_file,
)

from src.persistence.supabase import admin_client
from src.services.excel_export import ExcelExporter, ExportError

log = logging.getLogger(__name__)

bp = Blueprint("tools", __name__, url_prefix="/tools")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_vat(raw: str) -> str:
    """Strip BE prefix, dots, spaces. Returns digits-only string."""
    if not raw:
        return ""
    cleaned = (
        raw.upper()
        .replace("BE", "")
        .replace(".", "")
        .replace(" ", "")
        .replace("-", "")
        .strip()
    )
    # KBO is exactly 10 digits; left-pad if 9 (some inputs miss the leading 0)
    if cleaned.isdigit() and len(cleaned) == 9:
        cleaned = "0" + cleaned
    return cleaned


def _resolve_party_id_from_vat(vat: str) -> str | None:
    """Look up party_id by KBO. Returns None if not found.

    No auto-create here — Fetch enqueues a task and the worker handles
    party_id resolution (or fails clearly); Export requires the party to
    already exist; Upload-zip handles auto-create in the ingester service.
    """
    if not vat:
        return None
    res = (
        admin_client()
        .table("party_identifiers")
        .select("party_id")
        .eq("id_type", "KBO")
        .eq("id_value", vat)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]["party_id"]
    return None


def _upload_enabled() -> bool:
    return os.environ.get("TOOLS_UPLOAD_ENABLED", "false").lower() in ("true", "1", "yes")


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/", methods=["GET"])
def index() -> str:
    """Render the tools UI (segmented control with Fetch / Upload / Export)."""
    return render_template(
        "tools.html",
        upload_enabled=_upload_enabled(),
    )


@bp.route("/fetch", methods=["POST"])
def fetch() -> tuple[Response, int]:
    """Enqueue a NBB Authentic Data ingestion task.

    JSON body::

        {"vat": "0459499688", "max_years": 10}

    Returns 200 with ``{"queue_id": "...", "party_id": "...", "kbo": "..."}``
    on success, 400 on validation error, 404 if party not in DB.

    The Render worker picks up the task; client polls ``GET /tools/status``.
    """
    payload = request.get_json(silent=True) or {}
    vat = _normalize_vat(str(payload.get("vat") or ""))
    if not vat or not vat.isdigit() or len(vat) != 10:
        return jsonify({"error": "Invalid KBO — must be 10 digits"}), 400

    party_id = _resolve_party_id_from_vat(vat)
    if not party_id:
        return jsonify({
            "error": f"KBO {vat} not found in party_identifiers. "
                     "Seed the party first, or upload a ZIP to auto-create.",
        }), 404

    try:
        max_years = int(payload.get("max_years", 10))
    except (TypeError, ValueError):
        return jsonify({"error": "max_years must be an integer"}), 400
    if max_years < 1 or max_years > 20:
        return jsonify({"error": "max_years must be between 1 and 20"}), 400

    trigger_payload = {
        "triggered_by": "tools_ui",
        "source": "v4g-ingestion-web /tools/fetch",
        "max_years": max_years,
        "date": datetime.now(UTC).isoformat(),
    }

    res = (
        admin_client()
        .schema("gs_enrichment")
        .table("queue")
        .insert({
            "party_id":        party_id,
            "enrichment_type": "nbb_financials",
            "status":          "pending",
            "priority":        5,
            "trigger_payload": trigger_payload,
        })
        .execute()
    )
    if not res.data:
        log.error("tools.fetch · enqueue failed for party=%s kbo=%s", party_id, vat)
        return jsonify({"error": "Failed to enqueue task — see server logs"}), 500

    row = res.data[0]
    log.info(
        "tools.fetch · enqueued · queue_id=%s party=%s kbo=%s max_years=%d",
        row["queue_id"], party_id, vat, max_years,
    )
    return jsonify({
        "queue_id": row["queue_id"],
        "party_id": party_id,
        "kbo":      vat,
    }), 200


@bp.route("/status/<queue_id>", methods=["GET"])
def status(queue_id: str) -> tuple[Response, int]:
    """Return the current state of a queue row.

    Polled by the client every ~2s while a fetch is running.
    """
    if not queue_id:
        return jsonify({"error": "queue_id required"}), 400

    res = (
        admin_client()
        .schema("gs_enrichment")
        .table("queue")
        .select(
            "queue_id, party_id, status, enqueued_at, started_at, "
            "finished_at, last_error, attempts, run_id",
        )
        .eq("queue_id", queue_id)
        .limit(1)
        .execute()
    )
    if not res.data:
        return jsonify({"error": "queue_id not found"}), 404

    row = res.data[0]

    # If done, enrich with summary from object_log
    summary: dict[str, Any] | None = None
    if row["status"] == "done" and row.get("run_id"):
        obj = (
            admin_client()
            .schema("gs_enrichment")
            .table("object_log")
            .select("outcome, rows_written, change_summary, duration_ms")
            .eq("run_id", row["run_id"])
            .eq("party_id", row["party_id"])
            .order("logged_at", desc=True)
            .limit(1)
            .execute()
        )
        if obj.data:
            summary = obj.data[0]

    return jsonify({**row, "summary": summary}), 200


@bp.route("/export", methods=["GET"])
def export() -> Any:
    """Generate and return an .xlsx for the requested party.

    Query params:

    - ``vat``    — required, KBO (with or without BE prefix, dots, spaces)
    - ``format`` — ``simple`` (default) | ``analyst``
    - ``years``  — int, 1-20, default 10

    Returns the file as an attachment; the response is fully buffered in
    memory (≪ 1 MB typical), so streaming/temp-files aren't needed.
    """
    vat = _normalize_vat(request.args.get("vat", ""))
    if not vat or not vat.isdigit() or len(vat) != 10:
        return jsonify({"error": "Invalid KBO — must be 10 digits"}), 400

    fmt_raw = request.args.get("format", "simple").lower()
    if fmt_raw not in ("simple", "analyst"):
        return jsonify({"error": "format must be 'simple' or 'analyst'"}), 400

    try:
        years = int(request.args.get("years", 10))
    except ValueError:
        return jsonify({"error": "years must be an integer"}), 400
    if years < 1 or years > 20:
        return jsonify({"error": "years must be between 1 and 20"}), 400

    party_id = _resolve_party_id_from_vat(vat)
    if not party_id:
        return jsonify({"error": f"KBO {vat} not found"}), 404

    try:
        exporter = ExcelExporter(
            client=admin_client(),
            party_id=party_id,
            mode=fmt_raw,  # type: ignore[arg-type]
            year_limit=years,
        ).fetch()
        content = exporter.build()
    except ExportError as e:
        log.info("tools.export · no data · kbo=%s reason=%s", vat, e)
        return jsonify({"error": str(e)}), 404

    filename = exporter.suggest_filename()
    log.info(
        "tools.export · ok · kbo=%s mode=%s bytes=%d filename=%s",
        vat, fmt_raw, len(content), filename,
    )

    # send_file consumes a file-like; wrap our bytes
    from io import BytesIO
    return send_file(
        BytesIO(content),
        mimetype=_XLSX_MIME,
        as_attachment=True,
        download_name=filename,
    )


@bp.route("/upload-zip", methods=["POST"])
def upload_zip() -> tuple[Response, int]:
    """Lane B sync ZIP ingester.

    v1: gated behind ``TOOLS_UPLOAD_ENABLED`` env var (off by default).
    UI shows the dropzone always; backend rejects if flag is off so we
    can ship the UI now and enable the path after canary.
    """
    if not _upload_enabled():
        return jsonify({
            "error": "ZIP upload is disabled in this environment "
                     "(set TOOLS_UPLOAD_ENABLED=true to enable).",
            "feature_flag": "TOOLS_UPLOAD_ENABLED",
        }), 503

    # When enabled, delegate to the ingester service. Stubbed here so the
    # route signature is fixed at v1 time and only the implementation
    # toggles in iteration 2.
    try:
        from src.services.zip_ingester import ingest_uploaded_zip  # type: ignore
    except ImportError:
        return jsonify({"error": "zip_ingester not yet implemented"}), 501

    if "file" not in request.files:
        return jsonify({"error": "No file in request"}), 400

    upload = request.files["file"]
    if not (upload.filename or "").lower().endswith(".zip"):
        return jsonify({"error": "Only .zip files accepted"}), 400

    try:
        result = ingest_uploaded_zip(upload.stream, admin_client(),
                                     uploaded_filename=upload.filename or "upload.zip")
    except Exception as e:  # noqa: BLE001
        log.exception("tools.upload_zip · ingest failed")
        return jsonify({"error": str(e)}), 500

    log.info("tools.upload_zip · ok · result=%s", result)
    return jsonify(result), 200


# ─────────────────────────────────────────────────────────────────────────────
# Health metadata — exposed so the UI can show what's enabled
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/_meta", methods=["GET"])
def meta() -> tuple[Response, int]:
    """Feature flags + capability metadata for the UI."""
    return jsonify({
        "upload_enabled":   _upload_enabled(),
        "supported_format": ["simple", "analyst"],
        "max_years":        20,
        "default_years":    10,
        "version":          current_app.config.get("VERSION", "0.1.0"),
    }), 200
