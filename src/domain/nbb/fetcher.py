"""
NBB CBSO REST API Fetcher  —  application/x.jsonxbrl
Endpoint: https://ws.cbso.nbb.be/authentic/
Subscription: CLIENT-000446 / Authentic Data
"""
import requests, uuid, time, json
from pathlib import Path

API_BASE  = "https://ws.cbso.nbb.be/authentic"
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

class NBBApiError(Exception):
    pass

def _headers(accept="application/json"):
    return {
        "NBB-CBSO-Subscription-Key": _key(),
        "Ocp-Apim-Subscription-Key": _key(),
        "X-Request-Id":              str(uuid.uuid4()),
        "Accept":                    accept,
        "User-Agent":                "Microsoft.Data.Mashup (https://go.microsoft.com/fwlink/?LinkID=304225)",
    }

_cfg_cache = {}
def _key():
    if not _cfg_cache:
        cfg_path = Path(__file__).parent / "config.json"
        if cfg_path.exists():
            _cfg_cache.update(json.loads(cfg_path.read_text()))
    return _cfg_cache.get("api_key", "")

def set_key(api_key: str):
    _cfg_cache["api_key"] = api_key


def get_references(vat: str, api_key: str) -> list:
    _cfg_cache["api_key"] = api_key
    vat = vat.replace("BE","").replace(".","").replace(" ","").strip()
    url = f"{API_BASE}/legalEntity/{vat}/references"
    try:
        r = requests.get(url, headers=_headers(), timeout=20)
    except requests.RequestException as e:
        raise NBBApiError(f"Netwerkfout: {e}")

    if r.status_code == 401:
        raise NBBApiError("Sleutel ongeldig (401) — controleer Instellingen.")
    if r.status_code == 403:
        raise NBBApiError("Toegang geweigerd (403) — gebruik Primary Key van 'Authentic Data'.")
    if r.status_code == 404:
        raise NBBApiError(f"Geen neerleggingen gevonden voor {vat} (404).")
    if r.status_code != 200:
        raise NBBApiError(f"API fout {r.status_code}: {r.text[:200]}")

    data = r.json()
    refs = data if isinstance(data, list) else data.get("references") or data.get("items") or []
    result = []
    for item in refs:
        ref_num = (item.get("ReferenceNumber") or item.get("referenceNumber") or
                   item.get("reference") or str(item))
        result.append({"referenceNumber": str(ref_num), **item})
    return result


def fetch_jsonxbrl(reference: str, api_key: str) -> dict | None:
    """Download one filing as application/x.jsonxbrl — returns parsed dict."""
    _cfg_cache["api_key"] = api_key
    url = f"{API_BASE}/deposit/{reference}/accountingData"
    try:
        r = requests.get(url, headers=_headers("application/x.jsonxbrl"), timeout=30)
    except requests.RequestException as e:
        raise NBBApiError(f"Netwerkfout bij {reference}: {e}")

    if r.status_code == 200:
        return r.json()
    if r.status_code in (404, 406, 415):
        # Older filing — not available as jsonxbrl on authentic API (PDF-only)
        # Historical years must be loaded via manual ZIP upload
        return None
    raise NBBApiError(f"Download fout {r.status_code} voor {reference}: {r.text[:150]}")


def parse_rubrics(data: dict, filing_meta: dict) -> dict:
    """
    Extract rubrics from jsonxbrl response.
    Returns {mar_code: amount_eur} for Period='N' rows only.
    Also returns metadata: year, company_name, model_type.
    """
    rubrics = data.get("Rubrics") or data.get("rubrics") or []

    # Filter Period=N (current year, no comparative)
    # Pure-count codes: stored as raw number, NOT divided by 1000 later
    PURE_COUNT_CODES = {"9087", "9088"}  # FTE average and total hours: raw counts, no /1000

    amounts = {}
    for r in rubrics:
        code   = r.get("Code") if r.get("Code") is not None else r.get("code")
        # Use explicit None check — value CAN be 0.0 (valid!)
        value  = r.get("Value") if r.get("Value") is not None else r.get("value")
        period = r.get("Period") if r.get("Period") is not None else (r.get("period") or "N")
        if code is None or value is None or period not in ("N", None):
            continue
        # DataType from API: "pure" or "dec1" = count/ratio, NOT monetary
        dtype = str(r.get("DataType") or r.get("dataType") or "")
        is_count = str(code) in PURE_COUNT_CODES or "pure" in dtype or "dec1" in dtype
        try:
            fval = float(str(value).replace(",", "."))
            amounts[str(code)] = fval
            # Tag count codes so staging_builder can skip /1000
            if is_count:
                amounts[f"_count_{code}"] = True
        except (ValueError, TypeError):
            pass

    # Extract year from filing meta
    ex = filing_meta.get("ExerciseDates") or filing_meta.get("exerciseDates") or {}
    end_date = ex.get("endDate") or ex.get("EndDate") or ""
    year = end_date[:4] if end_date else filing_meta.get("referenceNumber","")[:4]

    # Fiscal period metadata
    fy_start_str = ex.get("startDate") or ex.get("StartDate") or ""
    fy_end_str   = end_date

    # Period in months
    period_months = 12
    if fy_start_str and fy_end_str:
        try:
            from datetime import date
            def _parse(s):
                return date.fromisoformat(s[:10])
            delta = _parse(fy_end_str) - _parse(fy_start_str)
            period_months = round(delta.days / 30.4375)
        except Exception:
            period_months = 12

    return {
        "year":           year,
        "company_name":   data.get("EnterpriseName") or filing_meta.get("EnterpriseName",""),
        "model_type":     filing_meta.get("ModelType") or filing_meta.get("modelType",""),
        "reference":      filing_meta.get("referenceNumber",""),
        "fy_start":       fy_start_str,
        "fy_end":         fy_end_str,
        "period_months":  period_months,
        "amounts":        amounts,   # {code: eur_amount}
    }


def fetch_all_xbrl(vat: str, api_key: str,
                   progress_cb=None, use_cache: bool = True) -> list:
    """
    Fetch all filings for a company.
    Returns list of (year_str, parsed_dict, ref_num) sorted by year ASC.
    parsed_dict = {"year","company_name","model_type","reference","amounts"}
    """
    vat = vat.replace("BE","").replace(".","").replace(" ","").strip()

    if progress_cb:
        progress_cb(0, 1, f"Opzoeken neerleggingen voor {vat}…")

    refs = get_references(vat, api_key)
    if not refs:
        raise NBBApiError(f"Geen neerleggingen gevonden voor {vat}")

    ref_meta = {r["referenceNumber"]: r for r in refs}

    # Only attempt jsonxbrl for filings from 2021 onwards (older = PDF only on authentic API)
    def _fy_end(r):
        ex = r.get("ExerciseDates") or r.get("exerciseDates") or {}
        return (ex.get("endDate") or ex.get("EndDate") or "0000")[:4]

    eligible = [r for r in refs if _fy_end(r) >= "2021"]
    if not eligible:
        eligible = refs  # fallback: try all if nothing passes filter

    ref_nums = sorted(set(r["referenceNumber"] for r in eligible), reverse=True)[:10]
    total    = len(ref_nums)
    results  = []

    for i, ref_num in enumerate(ref_nums):
        cache_path = CACHE_DIR / f"{vat}_{ref_num}.json"

        if use_cache and cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            if progress_cb:
                progress_cb(i + 1, total, f"Cache: {ref_num}")
        else:
            if progress_cb:
                progress_cb(i, total, f"Downloaden {ref_num} ({i+1}/{total})…")
            try:
                data = fetch_jsonxbrl(ref_num, api_key)
                if data is None:
                    if progress_cb:
                        progress_cb(i, total, f"Overgeslagen {ref_num}: geen data")
                    continue

                if use_cache:
                    cache_path.write_text(json.dumps(data), encoding="utf-8")
                if progress_cb:
                    progress_cb(i + 1, total, f"OK: {ref_num}")
            except NBBApiError as e:
                if progress_cb:
                    progress_cb(i, total, f"Fout {ref_num}: {e}")
                continue
            time.sleep(0.2)

        parsed = parse_rubrics(data, ref_meta.get(ref_num, {"referenceNumber": ref_num}))
        results.append((parsed["year"], parsed, ref_num))

    if not results:
        raise NBBApiError("Geen data beschikbaar (enkel PDF-neerleggingen?)")

    results.sort(key=lambda x: x[0])
    return results


def clear_cache(vat: str = None):
    if vat:
        vat = vat.replace("BE","").replace(".","").replace(" ","").strip()
        for f in CACHE_DIR.glob(f"{vat}_*.json"):
            f.unlink()
    else:
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()

