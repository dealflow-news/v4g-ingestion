"""
NBB CBSO — Universal XBRL Parser
Handles ALL schema versions: pfs-abbr-*, pfs-full-*, cbso m01/m02/m03/m04
Strategy: extract everything, label what we can, preserve unknowns as raw codes.
"""

import xml.etree.ElementTree as ET
import re
from .taxonomy import BAS_MAP, PFS_MAP, detect_schema, BAS_M107_WORKERS_ONLY

# XML namespaces
NS_XBRL   = "http://www.xbrl.org/2003/instance"
NS_XBRLDI = "http://xbrl.org/2006/xbrldi"
NS_MET    = "http://www.nbb.be/be/fr/cbso/dict/met"


def _build_ctx_map(root) -> dict:
    """Build {ctx_id: {period, dims, entity}} from all xbrli:context elements"""
    ctx_map = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag != "context":
            continue
        cid = el.get("id")
        period = {}
        dims = {}
        entity = None

        for child in el.iter():
            ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if ctag in ("instant", "startDate", "endDate"):
                period[ctag] = child.text
            elif ctag == "explicitMember":
                dim_name = child.get("dimension", "").split(":")[-1]
                dims[dim_name] = child.text or ""
            elif ctag == "identifier":
                entity = (child.text or "").replace("BE", "").strip()

        ctx_map[cid] = {"period": period, "dims": dims, "entity": entity}
    return ctx_map


def _get_period_date(ctx: dict) -> str:
    """Return the closing date for a context (instant or endDate)"""
    p = ctx.get("period", {})
    return p.get("instant") or p.get("endDate") or ""


def _is_current_period(ctx_ref: str, ctx_map: dict, canonical_end: str) -> bool:
    """True if this context belongs to the main/current reporting period"""
    ctx = ctx_map.get(ctx_ref, {})
    date = _get_period_date(ctx)
    if not date or not canonical_end:
        return ctx_ref in {"CurrentInstant", "CurrentDuration"}
    return date == canonical_end


def _detect_canonical_end(ctx_map: dict) -> str:
    """Find the most recent closing date across all contexts — that's current year"""
    dates = []
    for ctx in ctx_map.values():
        d = _get_period_date(ctx)
        if d:
            dates.append(d)
    if not dates:
        return ""
    return max(dates)


# ─────────────────────────────────────────────────────────────────────────────
# NEW FORMAT PARSER  (met:am1 + dim:bas dimensions)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_new(root, ctx_map: dict, schema_info: dict) -> dict:
    """Extract financial data from new CBSO format"""
    canonical_end = _detect_canonical_end(ctx_map)

    # Detect prd:m1 = current year (prd:m2 = previous year — we skip)
    # Find all am1 (monetary) and dec1 (decimal, for workers) elements
    data = {}

    for el in root.findall(f"{{{NS_MET}}}am1"):
        ctx_ref = el.get("contextRef", "")
        ctx = ctx_map.get(ctx_ref, {})
        dims = ctx.get("dims", {})

        # Skip if not current period
        prd = dims.get("prd", "")
        if prd and prd != "prd:m1":
            continue

        # Skip if wrong closing date (preceding year)
        date = _get_period_date(ctx)
        if date and canonical_end and date != canonical_end:
            continue

        bas = dims.get("bas", "")
        if not bas or not el.text:
            continue

        try:
            val = float(el.text)
        except (ValueError, TypeError):
            continue

        # Look up label from taxonomy
        if bas in BAS_MAP:
            pcmn, label, section = BAS_MAP[bas]
        else:
            # Unknown code — preserve as-is
            code_num = bas.replace("bas:m", "")
            pcmn  = f"?{code_num}"
            label = f"[{bas}]"
            section = _guess_section(bas, val)

        key = (bas, pcmn, label, section)
        if key not in data:
            data[key] = val

    # dec1 = decimal values (workers, ratios)
    for el in root.findall(f"{{{NS_MET}}}dec1"):
        ctx_ref = el.get("contextRef", "")
        ctx = ctx_map.get(ctx_ref, {})
        dims = ctx.get("dims", {})

        prd = dims.get("prd", "")
        if prd and prd != "prd:m1":
            continue

        bas = dims.get("bas", "")
        if not bas or not el.text:
            continue

        # Only worker-type data (pure unit)
        unit = el.get("unitRef", "")
        if "EUR" in unit:
            continue

        try:
            val = float(el.text)
        except (ValueError, TypeError):
            continue

        if bas in BAS_MAP:
            pcmn, label, section = BAS_MAP[bas]
        else:
            pcmn  = f"?{bas.replace('bas:m','')}"
            label = f"[{bas}]"
            section = "WORKERS"

        key = (bas, pcmn, label, section)
        if key not in data:
            data[key] = val

    return data


def _guess_section(bas: str, val: float) -> str:
    """Heuristic section assignment for unknown bas: codes"""
    try:
        n = int(bas.replace("bas:m", ""))
        if n < 22:
            return "BS_A"
        elif n < 65:
            return "BS_L"
        elif n < 130:
            return "IS"
        elif n < 140:
            return "IS_X"
        else:
            return "WORKERS"
    except ValueError:
        return "NOTES"


# ─────────────────────────────────────────────────────────────────────────────
# OLD FORMAT PARSER  (pfs: namespace)
# ─────────────────────────────────────────────────────────────────────────────

def _parse_old(root, ctx_map: dict, schema_info: dict) -> dict:
    """Extract financial data from old PFS format"""
    # Current period contexts
    current_ctx_ids = {"CurrentInstant", "CurrentDuration"}

    # Also find by max date
    canonical_end = _detect_canonical_end(ctx_map)
    if canonical_end:
        for cid, ctx in ctx_map.items():
            if _get_period_date(ctx) == canonical_end:
                current_ctx_ids.add(cid)

    data = {}

    for el in root.iter():
        ctx_ref = el.get("contextRef", "")
        unit_ref = el.get("unitRef", "")

        if ctx_ref not in current_ctx_ids:
            continue

        # Only monetary (EUR) or pure (worker counts) values
        if not unit_ref:
            continue
        if "EUR" not in unit_ref and "pure" not in unit_ref.lower():
            continue
        if el.text is None:
            continue

        # Extract local tag name (remove namespace prefix)
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        ns_uri = el.tag.split("}")[0].lstrip("{") if "}" in el.tag else ""

        # Only process tags from nbb.be namespaces
        if "nbb.be" not in ns_uri:
            continue

        if tag in PFS_MAP:
            pcmn, label, section = PFS_MAP[tag]
        else:
            # Dynamic discovery — keep unknown elements with raw name
            pcmn  = f"?{tag[:8]}"
            label = f"[pfs:{tag}]"
            section = _guess_section_pfs(tag, unit_ref)

        try:
            val = float(el.text)
        except (ValueError, TypeError):
            continue

        key = (f"pfs:{tag}", pcmn, label, section)
        if key not in data:
            data[key] = val

    return data


def _guess_section_pfs(tag: str, unit_ref: str) -> str:
    if "pure" in unit_ref.lower() or "Worker" in tag or "Employee" in tag:
        return "WORKERS"
    if any(k in tag for k in ("Asset", "Receiv", "Stock", "Cash", "Fixed")):
        return "BS_A"
    if any(k in tag for k in ("Equity", "Capital", "Reserve", "Payable", "Debt",
                               "Provision", "Liabilit")):
        return "BS_L"
    if any(k in tag for k in ("Turnover", "Income", "Charge", "Profit", "Loss",
                               "Personnel", "Depreci", "Operating", "Financial")):
        return "IS"
    return "NOTES"


# ─────────────────────────────────────────────────────────────────────────────
# METADATA EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _extract_metadata(root, ctx_map: dict, content: str) -> dict:
    """Extract company VAT, name, fiscal year info"""
    meta = {"vat": None, "company": None, "fiscal_end": None,
            "fiscal_start": None, "year": None}

    # VAT from entity identifier
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "identifier" and el.text:
            meta["vat"] = el.text.replace("BE", "").strip()
            break

    # Fiscal year end = latest closing date
    canonical_end = _detect_canonical_end(ctx_map)
    meta["fiscal_end"] = canonical_end
    meta["year"] = canonical_end[:4] if canonical_end else "????"

    # Fiscal start = earliest startDate
    start_dates = [
        ctx["period"].get("startDate")
        for ctx in ctx_map.values()
        if ctx["period"].get("startDate")
    ]
    if start_dates:
        meta["fiscal_start"] = min(start_dates)

    # Company name — old format has it in str2 with bas:m26 = company name dim
    # New format: try finding ParticipantEntityName or company string
    for el in root.findall(f"{{{NS_MET}}}str2"):
        ctx_ref = el.get("contextRef", "")
        ctx = ctx_map.get(ctx_ref, {})
        dims = ctx.get("dims", {})
        if dims.get("bas") == "bas:m26":  # bas:m26 = company name in CBSO taxonomy
            if el.text and el.text != meta["vat"]:
                meta["company"] = el.text
                break

    # Old format: ParticipantEntityName
    if not meta["company"]:
        for el in root.iter():
            tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
            if tag == "EntityName" and el.text and len(el.text) > 2:
                meta["company"] = el.text.strip()
                break

    # Fallback: any str2 element with text > 3 chars (new XBRL format)
    # Guard: reject if text looks like a VAT number (all digits/dots/spaces)
    if not meta["company"] or not meta["company"].strip():
        for el in root.findall(f"{{{NS_MET}}}str2"):
            txt = (el.text or "").strip()
            is_vat_like = txt.replace(" ","").replace(".","").replace("BE","").isdigit()
            if txt and len(txt) > 3 and txt != meta.get("vat","") and not is_vat_like:
                meta["company"] = txt
                break

    # Final strip
    if meta["company"]:
        meta["company"] = meta["company"].strip()

    return meta


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def parse_xbrl(content: str) -> dict:
    """
    Parse an NBB XBRL file (any format/schema version).

    Returns:
        {
          "year": "2024",
          "fiscal_end": "2024-06-30",
          "fiscal_start": "2023-07-01",
          "company": "ACME NV",
          "vat": "0463112444",
          "schema": { model, format, version, model_label, schema_url },
          "data": { (bas_or_pfs_key, pcmn_code, label, section): float_value, ... }
        }
    """
    schema_info = detect_schema(content)
    root = ET.fromstring(content)
    ctx_map = _build_ctx_map(root)
    meta = _extract_metadata(root, ctx_map, content)

    if schema_info["format"] == "new":
        data = _parse_new(root, ctx_map, schema_info)
    else:
        data = _parse_old(root, ctx_map, schema_info)

    return {
        "year":        meta["year"],
        "fiscal_end":  meta["fiscal_end"],
        "fiscal_start": meta["fiscal_start"],
        "company":     meta["company"],
        "vat":         meta["vat"],
        "schema":      schema_info,
        "data":        data,
    }
