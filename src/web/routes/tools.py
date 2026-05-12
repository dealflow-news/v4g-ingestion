"""V4G Financial Tools — Flask blueprint.

Three modes:

- ``Fetch``      — enqueue a NBB ingestion task; live worker triple-writes
                   to fact_filings + fact_financials_lines + fact_financials_evidence.
                   Covers 2021+ (NBB Authentic Data API range).
- ``Upload ZIP`` — synchronous Lane B import of a ZIP of XBRL files. Handles
                   the 2007-2021 pfs:ci legacy taxonomy (NBB API gap). Skips
                   2022+ cbso files in the ZIP (use Fetch instead).
                   Configurable kill-switch via ``TOOLS_UPLOAD_ENABLED`` env var
                   (default on; set to ``false`` to disable in an environment).
- ``Export``     — read canonical financials from the DB and stream an .xlsx.

Routes:

- ``GET  /tools/``                       — render tools.html (UI shell).
- ``POST /tools/fetch``                  — JSON body, enqueue task.
- ``GET  /tools/status/<queue_id>``      — JSON status of the queue row.
- ``GET  /tools/export``                 — return .xlsx (query params).
- ``POST /tools/upload-zip``             — multipart upload (synchronous).

Auth: inherits the app-wide ``before_request`` gate from ``src/web/auth.py``
(``AUTH_ENABLED`` env var). No per-route RBAC in v1 — explicitly mono-user
prototype scope. The module is structured to allow per-route gates later
without refactoring (each route is small and isolated).

All writes go through server-side helpers; no direct DB writes from the
browser. Audit trail is preserved via the existing run_log + object_log
infrastructure (worker writes for Fetch; ingester writes via FinancialsWriter
for Upload), and the queue row itself for Fetch jobs.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from typing import Any
from zipfile import BadZipFile

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
from src.services.analyst_export import AnalystExporter, AnalystExportError
from src.services.excel_export import ExcelExporter, ExportError
from src.services.screening_export import ScreeningExporter, ScreeningExportError

log = logging.getLogger(__name__)

bp = Blueprint("tools", __name__, url_prefix="/tools")

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_SUPPORTED_FORMATS: tuple[str, ...] = ("simple", "analyst", "screening")


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
    already exist; Upload-zip handles auto-create in the ingester service
    (resolve_or_create_party).
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
    """Kill-switch for ZIP upload. Default ON; set TOOLS_UPLOAD_ENABLED=false
    to disable in an environment (e.g., during incident response).
    """
    return os.environ.get("TOOLS_UPLOAD_ENABLED", "true").lower() not in (
        "false", "0", "no", "off",
    )


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
    - ``format`` — ``simple`` (default) | ``analyst`` | ``screening``
    - ``years``  — int, 1-20, default 10

    Returns the file as an attachment; the response is fully buffered in
    memory (≪ 1 MB typical), so streaming/temp-files aren't needed.

    Format dispatch:

    - ``simple``     → ExcelExporter (legacy 4-sheet P&L / BS / KPIs output).
                       Slated for retirement once ``screening`` validated
                       in production -- see BL "ExcelExporter mode cleanup".
    - ``analyst``    → AnalystExporter (PCMN-detailed Info + Yearly_Review
                       + Filings sheets; ~80 codes × N years).
    - ``screening``  → ScreeningExporter (single-sheet M&A first-cut readout
                       with snapshot, trend, ratios + Gauss benchmark,
                       flags, coverage quality).
    """
    vat = _normalize_vat(request.args.get("vat", ""))
    if not vat or not vat.isdigit() or len(vat) != 10:
        return jsonify({"error": "Invalid KBO — must be 10 digits"}), 400

    fmt_raw = request.args.get("format", "simple").lower()
    if fmt_raw not in _SUPPORTED_FORMATS:
        return jsonify({
            "error": f"format must be one of: {', '.join(_SUPPORTED_FORMATS)}",
        }), 400

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
        if fmt_raw == "analyst":
            exporter = AnalystExporter(
                client=admin_client(),
                party_id=party_id,
                year_count=years,
            ).fetch()
        elif fmt_raw == "screening":
            exporter = ScreeningExporter(
                client=admin_client(),
                party_id=party_id,
                year_limit=years,
            ).fetch()
        else:
            exporter = ExcelExporter(
                client=admin_client(),
                party_id=party_id,
                mode="simple",
                year_limit=years,
            ).fetch()
        content = exporter.build()
    except (ExportError, AnalystExportError, ScreeningExportError) as e:
        log.info(
            "tools.export · no data · kbo=%s mode=%s reason=%s",
            vat, fmt_raw, e,
        )
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
    """Synchronous ZIP ingester (Lane B).

    Accepts a multipart/form-data POST with field ``file`` containing a ZIP
    of NBB Consult XBRL exports. Parses the 2007-2021 pfs:ci files via
    src.domain.nbb.parser_pfs, dispatches each to the existing aggregator
    + extractor + FinancialsWriter (same triple-write pipeline as the
    NBB API worker). cbso (2022+) files in the ZIP are skipped with a
    pointer to the Fetch flow.

    Auto-creates the party stub if no party_identifiers entry exists for
    the KBO inside the file (handled by the ingester service).

    Response: 200 with IngestResult dict on success, with per-file
    outcomes the UI renders. Error responses:

    - 400 — malformed ZIP, no .xbrl members, mixed-company ZIP, missing KBO
            in first parseable file
    - 503 — TOOLS_UPLOAD_ENABLED=false in this environment
    - 500 — unexpected server error (check logs)

    Idempotency: re-uploading the same ZIP converges to the same DB state
    (UNIQUE on fact_filings.source_code+filing_reference; DELETE-INSERT
    on lines per filing_id; UPSERT on evidence per party+period+source).
    """
    if not _upload_enabled():
        return jsonify({
            "error": "ZIP upload is disabled in this environment.",
            "feature_flag": "TOOLS_UPLOAD_ENABLED",
        }), 503

    # The ingester service is the canonical implementation. Keep this
    # import inside the route so any import-time error surfaces as a 501
    # (clearly diagnosable) rather than crashing the blueprint at startup.
    try:
        from src.services.zip_ingester import ingest_uploaded_zip
    except ImportError as e:
        log.exception("tools.upload_zip · ingester import failed")
        return jsonify({"error": f"zip_ingester unavailable: {e}"}), 501

    if "file" not in request.files:
        return jsonify({"error": "No file in request (expected multipart field 'file')"}), 400

    upload = request.files["file"]
    filename = upload.filename or "upload.zip"
    if not filename.lower().endswith(".zip"):
        return jsonify({"error": "Only .zip files accepted"}), 400

    try:
        result = ingest_uploaded_zip(
            upload.stream,
            admin_client(),
            uploaded_filename=filename,
        )
    except BadZipFile:
        log.info("tools.upload_zip · bad_zip · filename=%s", filename)
        return jsonify({"error": "File is not a valid ZIP archive"}), 400
    except ValueError as e:
        # Mixed-company ZIP, no extractable KBO, etc. — user-facing.
        log.info("tools.upload_zip · validation · filename=%s reason=%s", filename, e)
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        log.exception("tools.upload_zip · ingest failed · filename=%s", filename)
        return jsonify({"error": f"ingest failed: {e}"}), 500

    log.info(
        "tools.upload_zip · ok · filename=%s kbo=%s party_id=%s "
        "total=%d ingested=%d skipped=%d failed=%d",
        filename,
        result.get("kbo"),
        result.get("party_id"),
        result.get("files_total", 0),
        result.get("files_ingested", 0),
        result.get("files_skipped", 0),
        result.get("files_failed", 0),
    )
    return jsonify(result), 200


# ─────────────────────────────────────────────────────────────────────────────
# Health metadata — exposed so the UI can show what's enabled
# ─────────────────────────────────────────────────────────────────────────────

@bp.route("/_meta", methods=["GET"])
def meta() -> tuple[Response, int]:
    """Feature flags + capability metadata for the UI."""
    return jsonify({
        "upload_enabled":   _upload_enabled(),
        "supported_format": list(_SUPPORTED_FORMATS),
        "max_years":        20,
        "default_years":    10,
        "version":          current_app.config.get("VERSION", "0.1.0"),
    }), 200
