"""Flask test-client tests for the financials blueprint.

All tests mock `src.services.party_query` and `src.services.financial_export`
— no DB calls. Pure routing + template + status-code verification. The
end-to-end Excel pipeline is exercised separately by the CLI smoke test.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.web.app import create_app


# ─── Fixtures ─────────────────────────────────────────────────────────────
@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


def _ab_lens_motor_meta():
    return {
        "party_id": "982d23e1-9f6d-4551-84cf-12dcab4835ce",
        "display_name": "Lens Motor Garage",
        "legal_name": "AB LENS MOTOR",
        "country_iso2": "BE",
        "city": "Aalter",
        "party_type": "company",
        "status": "Active",
        "actor_type": None,
        "actor_type_confirmed": False,
        "founded_year": 1965,
        "enrichment_status": "raw",
        "kbo_nr": "0401452019",
        "website_domain": None,
    }


def _sample_rows(n: int = 3):
    """Lightweight 3-year sample for fast assertions."""
    return [
        {"period_label": "2024", "period_end": "2024-12-31", "period_type": "Annual",
         "revenue_eur_m": 47.938, "ebitda_eur_m": 2.059, "ebitda_margin_pct": 4.3,
         "net_debt_eur_m": -2.0, "employees": 82,
         "source_code": "SRC_NBB", "nbb_model_type": "cbso-new",
         "nbb_filing_date": "2025-04-15", "confidence": "Confirmed"},
        {"period_label": "2023", "period_end": "2023-12-31", "period_type": "Annual",
         "revenue_eur_m": 45.440, "ebitda_eur_m": 1.530, "ebitda_margin_pct": 3.4,
         "net_debt_eur_m": -1.3, "employees": 73,
         "source_code": "SRC_NBB", "nbb_model_type": "cbso-new",
         "nbb_filing_date": "2024-04-12", "confidence": "Confirmed"},
        {"period_label": "2022", "period_end": "2022-12-31", "period_type": "Annual",
         "revenue_eur_m": 40.454, "ebitda_eur_m": 1.353, "ebitda_margin_pct": 3.3,
         "net_debt_eur_m": -1.3, "employees": 66,
         "source_code": "SRC_NBB", "nbb_model_type": "cbso-new",
         "nbb_filing_date": "2023-04-15", "confidence": "Confirmed"},
    ][:n]


# ─── /party/<uuid> — happy path ──────────────────────────────────────────
def test_party_detail_renders_header_and_chart(client):
    party = _ab_lens_motor_meta()
    rows = _sample_rows()
    with (
        patch("src.web.routes.financials.party_query.get_party_meta",
              return_value=party),
        patch("src.web.routes.financials.financial_export.get_financial_history",
              return_value=rows),
    ):
        resp = client.get("/party/982d23e1-9f6d-4551-84cf-12dcab4835ce")
    assert resp.status_code == 200
    body = resp.data
    assert b"Lens Motor Garage" in body          # display_name as h1
    assert b"AB LENS MOTOR" in body              # legal_name as subname
    assert b"0401452019" in body                 # KBO
    assert b"<svg" in body                       # chart embedded
    assert b"<polyline" in body                  # chart has data
    assert b"47.94" in body                      # 2024 revenue formatted to 2 decimals
    assert b"Download Excel" in body             # download button


def test_party_detail_status_warn_for_non_active(client):
    party = _ab_lens_motor_meta() | {"status": "Dormant"}
    with (
        patch("src.web.routes.financials.party_query.get_party_meta",
              return_value=party),
        patch("src.web.routes.financials.financial_export.get_financial_history",
              return_value=[]),
    ):
        resp = client.get("/party/982d23e1-9f6d-4551-84cf-12dcab4835ce")
    assert resp.status_code == 200
    assert b"non-active" in resp.data            # warn badge


def test_party_detail_no_financials_shows_empty_state(client):
    party = _ab_lens_motor_meta()
    with (
        patch("src.web.routes.financials.party_query.get_party_meta",
              return_value=party),
        patch("src.web.routes.financials.financial_export.get_financial_history",
              return_value=[]),
    ):
        resp = client.get("/party/982d23e1-9f6d-4551-84cf-12dcab4835ce")
    assert resp.status_code == 200
    assert b"Geen financial data" in resp.data
    # Chart not rendered when no rows
    assert b"<polyline" not in resp.data


def test_party_detail_provenance_table_renders(client):
    party = _ab_lens_motor_meta()
    rows = _sample_rows()
    with (
        patch("src.web.routes.financials.party_query.get_party_meta",
              return_value=party),
        patch("src.web.routes.financials.financial_export.get_financial_history",
              return_value=rows),
    ):
        resp = client.get("/party/982d23e1-9f6d-4551-84cf-12dcab4835ce")
    assert b"Provenance" in resp.data
    assert b"cbso-new" in resp.data              # nbb_model_type
    assert b"2025-04-15" in resp.data            # nbb_filing_date
    assert b"SRC_NBB" in resp.data


# ─── /party/<uuid> — error paths ─────────────────────────────────────────
def test_party_detail_invalid_uuid_returns_404(client):
    resp = client.get("/party/not-a-uuid")
    assert resp.status_code == 404


def test_party_detail_unknown_party_returns_404(client):
    with patch("src.web.routes.financials.party_query.get_party_meta",
               return_value=None):
        resp = client.get("/party/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


# ─── /api/party/<uuid>/export.xlsx ───────────────────────────────────────
def test_export_xlsx_invalid_uuid_returns_404(client):
    resp = client.get("/api/party/not-a-uuid/export.xlsx")
    assert resp.status_code == 404


def test_export_xlsx_unknown_party_returns_404(client):
    with patch("src.web.routes.financials.party_query.get_party_meta",
               return_value=None):
        resp = client.get("/api/party/00000000-0000-0000-0000-000000000000/export.xlsx")
    assert resp.status_code == 404


def test_export_xlsx_no_financial_data_returns_404(client):
    party = _ab_lens_motor_meta()
    with (
        patch("src.web.routes.financials.party_query.get_party_meta",
              return_value=party),
        patch("src.web.routes.financials.financial_export.get_financial_history",
              return_value=[]),
    ):
        resp = client.get("/api/party/982d23e1-9f6d-4551-84cf-12dcab4835ce/export.xlsx")
    assert resp.status_code == 404


def test_export_xlsx_returns_workbook_with_correct_headers(client):
    party = _ab_lens_motor_meta()
    rows = _sample_rows()
    fake_bytes = b"PK\x03\x04fake-xlsx-bytes"  # Real workbook starts with PK (zip magic)

    with (
        patch("src.web.routes.financials.party_query.get_party_meta",
              return_value=party),
        patch("src.web.routes.financials.financial_export.get_financial_history",
              return_value=rows),
        patch("src.web.routes.financials.financial_export.build_xlsx_bytes",
              return_value=fake_bytes) as mock_build,
        patch("src.web.routes.financials.financial_export.suggest_filename",
              return_value="V4G_Lens_Motor_Garage_0401452019_financials.xlsx"),
    ):
        resp = client.get("/api/party/982d23e1-9f6d-4551-84cf-12dcab4835ce/export.xlsx")

    assert resp.status_code == 200
    assert resp.mimetype == \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    # Filename should appear in Content-Disposition
    cd = resp.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert "V4G_Lens_Motor_Garage_0401452019_financials.xlsx" in cd
    assert resp.data == fake_bytes
    # Service was called with both rows and party_meta
    mock_build.assert_called_once_with(rows, party)


# ─── Doctrine: detail page must NOT filter by status ─────────────────────
def test_party_detail_works_for_dormant_party(client):
    """Direct UUID lookup must work even for non-Active parties.

    Listing routes filter by status; detail routes don't. Analyst always
    needs to inspect Dormant/Liquidated/Acquired by direct link.
    """
    party = _ab_lens_motor_meta() | {"status": "Liquidated"}
    rows = _sample_rows(1)
    with (
        patch("src.web.routes.financials.party_query.get_party_meta",
              return_value=party),
        patch("src.web.routes.financials.financial_export.get_financial_history",
              return_value=rows),
    ):
        resp = client.get("/party/982d23e1-9f6d-4551-84cf-12dcab4835ce")
    assert resp.status_code == 200
    assert b"Liquidated" in resp.data


# ─── Regression: existing endpoints still respond ────────────────────────
def test_healthz_still_works_after_financials_blueprint(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_parties_index_still_works_after_financials_blueprint(client):
    with patch("src.web.routes.parties.party_query.list_recent",
               return_value=[]):
        resp = client.get("/parties")
    assert resp.status_code == 200
