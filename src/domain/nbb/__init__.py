"""
v4g_ingestion.domain.nbb — NBB CBSO data extraction.

Three lanes covered:
  - Lane A (live API): fetcher.fetch_jsonxbrl() + extractor.extract_from_jsonxbrl()
  - Lane B (bulk ZIP): parser.parse_xbrl() + extractor.extract_from_xbrl_xml()
  - Lane C (archive, pre-2022): scoped for W10 — Authentic Archive subscription

Public API (re-exports):
  Fetcher (Lane A live API):
    - get_references(vat, api_key)        — list filings for a KBO
    - fetch_jsonxbrl(reference, api_key)  — download one filing
    - fetch_all_xbrl(vat, api_key, ...)   — batch fetch all filings
    - parse_rubrics(data, filing_meta)    — simplify one filing's rubrics
    - get_filing_dates(vat, api_key)      — LB-005 sidecar lookup
    - set_key(api_key) / clear_cache()    — config helpers
    - NBBApiError                          — exception type

  Parser (Lane B XBRL XML):
    - parse_xbrl(content)                  — universal XBRL XML parser

  Extractor (orchestrators, DB shape):
    - extract_filing_and_lines_from_parsed(parsed, ref, party_id, ...)
                                           — worker entry point (Lane A)
    - extract_from_jsonxbrl(raw, meta, party_id)
                                           — raw → filing+lines+evidence (3 outputs)
    - extract_from_xbrl_xml(content, party_id, ref)
                                           — Lane B
    - SOURCE_CODE                          — "SRC_NBB" constant

  Taxonomy:
    - BAS_MAP, PFS_MAP, BE_GAAP_CI_MAP     — namespace → MAR mapping tables
    - SECTION_TO_DB, section_to_db()       — taxonomy section → DB CHECK value
    - detect_schema()                       — namespace detection from XBRL XML
"""
from .fetcher import (
    NBBApiError,
    set_key,
    get_references,
    fetch_jsonxbrl,
    parse_rubrics,
    fetch_all_xbrl,
    get_filing_dates,
    clear_cache,
)
from .parser import parse_xbrl
from .extractor import (
    extract_filing_and_lines_from_parsed,
    extract_from_jsonxbrl,
    extract_from_xbrl_xml,
    SOURCE_CODE,
)
from .taxonomy import (
    BAS_MAP,
    PFS_MAP,
    BE_GAAP_CI_MAP,
    SECTION_TO_DB,
    section_to_db,
    detect_schema,
)

__all__ = [
    # Errors
    "NBBApiError",
    # Fetcher
    "set_key",
    "get_references",
    "fetch_jsonxbrl",
    "parse_rubrics",
    "fetch_all_xbrl",
    "get_filing_dates",
    "clear_cache",
    # Parser
    "parse_xbrl",
    # Extractor (orchestrators)
    "extract_filing_and_lines_from_parsed",
    "extract_from_jsonxbrl",
    "extract_from_xbrl_xml",
    "SOURCE_CODE",
    # Taxonomy
    "BAS_MAP",
    "PFS_MAP",
    "BE_GAAP_CI_MAP",
    "SECTION_TO_DB",
    "section_to_db",
    "detect_schema",
]
