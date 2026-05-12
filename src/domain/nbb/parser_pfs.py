"""Parser for legacy pfs:ci XBRL format (NBB filings 2007-2021).

The NBB used a different XBRL taxonomy from 2007-2021 before switching to
cbso/bas:mXX in 2022. Files in the old format have:
  - Namespace: xmlns:pfs="http://www.nbb.be/be/fr/pfs/ci/<YYYY>-MM-DD"
  - PascalCase English element names: pfs:Turnover, pfs:Equity, pfs:Assets...
  - Multi-context structure: CurrentInstant/CurrentDuration (current period),
    PrecedingInstant/PrecedingDuration (comparative prior year), N-2Instant.
  - KBO embedded in: xbrli:identifier scheme="http://www.fgov.be"
  - Entity name in: pfs-gcd:EntityCurrentLegalName
  - Schema code in: pfs-vl:XCode_SchemaCode_<NN>
        (40/41 = m02 volledig, 10-13 = m01 verkort, 70-71 = m03 micro)
  - Legal form in: pfs-vl:XCode_LegalFormCode_<NNN>

This parser extracts the CURRENT period only (CurrentInstant + CurrentDuration);
the comparative prior-year data inside each file is redundant - each year's
filing covers its own period_end-1.

Output: parsed_dict matching the shape the existing aggregator + extractor
expect (see src/enrichment/workers/nbb_financials.py for the contract).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from lxml import etree

from src.domain.nbb.taxonomy import PFS_MAP

log = logging.getLogger(__name__)


# Namespaces - the pfs/ci URI changes per year version, so we read it from
# the file's nsmap rather than asserting a fixed URI.
NS_XBRLI = "http://www.xbrl.org/2003/instance"
PFS_URI_PREFIX = "http://www.nbb.be/be/fr/pfs/"

# Contexts containing CURRENT-period data. Everything else is comparative.
CURRENT_CONTEXTS = {"CurrentInstant", "CurrentDuration"}

# ISO 4217 EUR measure. The unit id varies across NBB years:
#   2007-2012: <xbrli:unit id="EUR">     -> unitRef="EUR"
#   2013-2021: <xbrli:unit id="U-EUR">   -> unitRef="U-EUR"
# Both definitions point to the same measure: iso4217:EUR. We detect any
# unit whose <xbrli:measure> resolves to this URI, regardless of its id.
ISO4217_URI = "http://www.xbrl.org/2003/iso4217"

# Schema-code mapping (XCode_SchemaCode_NN value -> model type).
# Source: NBB documentation; covers m01 verkort / m02 volledig / m03 micro.
SCHEMA_CODE_TO_MODEL = {
    "10": "m01", "11": "m01", "12": "m01", "13": "m01",  # verkort
    "20": "m01", "21": "m01",                              # micro-verkort variants
    "40": "m02", "41": "m02",                              # volledig
    "70": "m03", "71": "m03",                              # micro
}


def parse_pfs_xbrl(content: bytes, *, source_filename: str | None = None) -> dict[str, Any]:
    """Parse a pfs/ci-format XBRL file (NBB filings 2007-2021).

    Returns a parsed_dict shaped to match the existing W8-worker contract:
        {
            "amounts":         dict[pcmn_code, float],   # aggregator input
            "fy_start":        str | None,               # ISO date YYYY-MM-DD
            "fy_end":          str | None,               # ISO date YYYY-MM-DD
            "filing_date":     str | None,               # ISO date (GA approval)
            "model_type":      str | None,               # 'm01' | 'm02' | 'm03'
            "kbo":             str | None,               # 10-digit normalized
            "entity_name":     str | None,
            "legal_form_code": str | None,               # 3-digit (e.g. '014')
            "language":        str | None,               # 'FR' | 'NL' | 'DE'
            "ref_num":         str | None,               # from filename if given
            "_format":         "pfs",                    # for diagnostics
            "_schema_code":    str | None,               # raw NN value
        }

    Raises:
        ValueError: content is not parseable XML or not pfs/ci format.
    """
    # Strip BOM if present (lxml usually handles, be defensive)
    if content[:3] == b"\xef\xbb\xbf":
        content = content[3:]

    parser = etree.XMLParser(recover=True, ns_clean=True, huge_tree=True)
    try:
        root = etree.fromstring(content, parser=parser)
    except etree.XMLSyntaxError as e:
        raise ValueError(f"invalid XML: {e}") from e

    if root is None:
        raise ValueError("XML parse returned empty root")

    nsmap = {k: v for k, v in root.nsmap.items() if k}  # drop default ns (None key)
    pfs_uri = nsmap.get("pfs", "")
    if not pfs_uri.startswith(PFS_URI_PREFIX):
        raise ValueError(
            f"not a pfs/ci file: pfs namespace = {pfs_uri!r} "
            f"(expected to start with {PFS_URI_PREFIX!r})"
        )

    pfs_gcd_uri = nsmap.get("pfs-gcd", "")
    pfs_vl_uri = nsmap.get("pfs-vl", "")

    # Detect EUR-denominated unit IDs by scanning xbrli:unit definitions.
    # Handles both 'EUR' (pre-2013) and 'U-EUR' (2013+) naming variants.
    eur_unit_ids = _detect_eur_unit_ids(root)

    fy_start, fy_end = _extract_current_period(root)
    kbo = _extract_kbo(root)
    entity_name = _extract_entity_name(root, pfs_gcd_uri)
    schema_code, model_type = _extract_schema_code(root, pfs_vl_uri)
    legal_form_code = _extract_xcode(root, pfs_vl_uri, prefix="XCode_LegalFormCode_")
    language = _extract_xcode(root, pfs_vl_uri, prefix="XCode_LanguageCode_")

    # Filing date from pfs:AccountsApprovalDateGeneralAssembly
    pfs_pref = f"{{{pfs_uri}}}"
    filing_date_raw = _find_first_text(root, f"{pfs_pref}AccountsApprovalDateGeneralAssembly")
    filing_date = _normalize_iso_date(filing_date_raw)

    amounts = _extract_amounts(root, pfs_uri, eur_unit_ids=eur_unit_ids)
    ref_num = _ref_from_filename(source_filename) if source_filename else None

    return {
        "amounts":         amounts,
        "fy_start":        fy_start,
        "fy_end":          fy_end,
        "filing_date":     filing_date,
        "model_type":      model_type,
        "kbo":             kbo,
        "entity_name":     entity_name,
        "legal_form_code": legal_form_code,
        "language":        language,
        "ref_num":         ref_num,
        "_format":         "pfs",
        "_schema_code":    schema_code,
    }


# --- helpers ----------------------------------------------------------


def _detect_eur_unit_ids(root) -> set[str]:
    """Scan xbrli:unit definitions; return set of unit IDs that measure iso4217:EUR.

    Handles year-to-year naming variation:
      2007-2012: <xbrli:unit id="EUR"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>
      2013-2021: <xbrli:unit id="U-EUR"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>

    The measure value uses the file's own iso4217 prefix binding (typically
    "iso4217"). We resolve the QName against root.nsmap to compare URIs
    instead of string-matching the prefix.
    """
    eur_ids: set[str] = set()
    xb = f"{{{NS_XBRLI}}}"
    iso_pref_uri = root.nsmap.get("iso4217", ISO4217_URI)

    for unit_el in root.iter(f"{xb}unit"):
        uid = unit_el.get("id")
        if not uid:
            continue
        measure = unit_el.find(f"{xb}measure")
        if measure is None or not measure.text:
            continue
        text = measure.text.strip()
        # Format is "<prefix>:<local>" e.g. "iso4217:EUR"
        if ":" in text:
            prefix, local = text.split(":", 1)
            prefix_uri = root.nsmap.get(prefix)
            # Match either by exact namespace URI OR by canonical fallback
            if prefix_uri in (iso_pref_uri, ISO4217_URI) and local == "EUR":
                eur_ids.add(uid)
        elif text == "EUR":
            # Bare value (rare): accept it
            eur_ids.add(uid)

    return eur_ids


def _extract_current_period(root) -> tuple[str | None, str | None]:
    """Find CurrentInstant + CurrentDuration contexts; return (fy_start, fy_end).

    For CurrentDuration we use xbrli:startDate / xbrli:endDate.
    For CurrentInstant we use xbrli:instant as fy_end (it's the closing date).
    """
    fy_start: str | None = None
    fy_end: str | None = None
    xb = f"{{{NS_XBRLI}}}"

    for ctx in root.iter(f"{xb}context"):
        cid = ctx.get("id", "")
        if cid not in CURRENT_CONTEXTS:
            continue
        period = ctx.find(f"{xb}period")
        if period is None:
            continue

        if cid == "CurrentDuration":
            s = period.find(f"{xb}startDate")
            e = period.find(f"{xb}endDate")
            if s is not None and s.text:
                fy_start = _normalize_iso_date(s.text)
            if e is not None and e.text:
                fy_end = _normalize_iso_date(e.text)
        elif cid == "CurrentInstant" and fy_end is None:
            i = period.find(f"{xb}instant")
            if i is not None and i.text:
                fy_end = _normalize_iso_date(i.text)

    return fy_start, fy_end


def _extract_kbo(root) -> str | None:
    """KBO from xbrli:identifier scheme='http://www.fgov.be'. Returns 10-digit string."""
    xb = f"{{{NS_XBRLI}}}"
    for ident in root.iter(f"{xb}identifier"):
        if ident.get("scheme") == "http://www.fgov.be" and ident.text:
            raw = ident.text.strip()
            digits = re.sub(r"[^0-9]", "", raw)
            if 9 <= len(digits) <= 10:
                return digits.zfill(10)
    return None


def _extract_entity_name(root, pfs_gcd_uri: str) -> str | None:
    """Entity name from pfs-gcd:EntityCurrentLegalName (prefer current context)."""
    if not pfs_gcd_uri:
        return None
    tag = f"{{{pfs_gcd_uri}}}EntityCurrentLegalName"
    # Prefer current-context element
    for el in root.iter(tag):
        if el.get("contextRef") in CURRENT_CONTEXTS and el.text:
            return el.text.strip()
    # Fallback: first occurrence
    for el in root.iter(tag):
        if el.text:
            return el.text.strip()
    return None


def _extract_schema_code(root, pfs_vl_uri: str) -> tuple[str | None, str | None]:
    """Find XCode_SchemaCode_NN element. Returns (NN, model_type)."""
    if not pfs_vl_uri:
        return None, None
    code = _extract_xcode(root, pfs_vl_uri, prefix="XCode_SchemaCode_")
    if code is None:
        return None, None
    model = SCHEMA_CODE_TO_MODEL.get(code)
    return code, model


def _extract_xcode(root, pfs_vl_uri: str, *, prefix: str) -> str | None:
    """Find an XCode_<kind>_<value> element. Returns the <value> part.

    pfs-vl uses self-describing element names where the value is encoded
    in the local name AND (usually) the text content:
       <pfs-vl:XCode_SchemaCode_40 contextRef="CurrentDuration">40</pfs-vl:XCode_SchemaCode_40>

    Prefer text content (more authoritative); fall back to name suffix.
    """
    if not pfs_vl_uri:
        return None
    ns_pref = f"{{{pfs_vl_uri}}}"
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if not el.tag.startswith(ns_pref):
            continue
        local = el.tag[len(ns_pref):]
        if not local.startswith(prefix):
            continue
        # Prefer text content if it has anything
        if el.text and el.text.strip():
            return el.text.strip()
        # Fall back to the suffix part of the element name
        return local[len(prefix):]
    return None


def _find_first_text(root, clark_tag: str) -> str | None:
    """Find first element with given Clark-notation tag; return stripped text."""
    el = root.find(f".//{clark_tag}")
    if el is not None and el.text:
        return el.text.strip()
    return None


def _extract_amounts(root, pfs_uri: str, *, eur_unit_ids: set[str]) -> dict[str, float]:
    """Iterate pfs:* elements with EUR unit in current contexts; map to PCMN.

    Returns dict {pcmn_code: amount_eur}. Multiple pfs elements mapping to
    the same PCMN code collapse (last write wins; values should match in
    well-formed filings since pfs:Stocks == pfs:StockGoodsPurchasedResale
    in simple stock-only filings).

    `eur_unit_ids` is the set of unitRef values that resolve to iso4217:EUR
    in this specific file (see _detect_eur_unit_ids). This accommodates
    NBB's mid-decade rename: id="EUR" (pre-2013) -> id="U-EUR" (2013+).

    Elements not in PFS_MAP are silently skipped.
    """
    amounts: dict[str, float] = {}
    pfs_pref = f"{{{pfs_uri}}}"

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if not el.tag.startswith(pfs_pref):
            continue
        local = el.tag[len(pfs_pref):]

        # Filter: only current contexts, only EUR-denominated amounts
        ctx = el.get("contextRef")
        if ctx not in CURRENT_CONTEXTS:
            continue
        unit = el.get("unitRef")
        if not unit or unit not in eur_unit_ids:
            # Skip non-EUR (U-Shares, U-Pure, etc.) - not financial amounts
            continue
        if not el.text:
            continue

        entry = PFS_MAP.get(local)
        if entry is None:
            continue
        pcmn_code = entry[0]

        try:
            val = float(el.text.strip())
        except ValueError:
            log.debug("pfs.skip_non_numeric tag=%s text=%r", local, el.text)
            continue

        amounts[pcmn_code] = val

    return amounts


def _normalize_iso_date(s: str | None) -> str | None:
    """Strip time component, validate YYYY-MM-DD."""
    if not s:
        return None
    s = s.strip()[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def _ref_from_filename(filename: str) -> str | None:
    """NBB filing reference: 'YYYY-NNNNNNNN.xbrl' -> 'YYYY-NNNNNNNN'."""
    base = os.path.basename(filename)
    m = re.match(r"^(\d{4}-\d+)\.xbrl$", base, re.IGNORECASE)
    return m.group(1) if m else None


__all__ = ["parse_pfs_xbrl"]
