# LB-005 · filing_date wiring patches (β2)

**Purpose**: Surface NBB's `DepositDate` (per-filing depot-datum) into the canonical `fact_financials.nbb_filing_date` for both Lane A and Lane B.

**Background**: filing_date is **not embedded in XBRL bytes** in either format (pfs-old or cbso-new). It comes through the NBB CBSO `/legalEntity/{vat}/references` JSON response as `DepositDate`. Currently `fetcher.py` passes that response through `**item` spread but `parse_rubrics()` doesn't extract the field, so `workers/nbb_financials.py` is forced to write `filing_date=None`. These patches fix that, in three small changes.

---

## Status

| Patch | Where | Status |
|---|---|---|
| 1 | `src/domain/nbb/fetcher.py` → `parse_rubrics()` | pending — drop-in addition |
| 2 | `src/enrichment/workers/nbb_financials.py` → `_fetch_and_parse()` | pending — one-liner |
| 3 | `src/domain/nbb/fetcher.py` → new `get_filing_dates()` helper | pending — additive |

After these patches, **Lane A worker writes `nbb_filing_date` automatically** for newly enqueued tasks. Existing rows in `fact_financials` with `nbb_filing_date=NULL` stay until reprocessed (run a manual re-enqueue via the `manual` policy if you want them backfilled).

---

## Patch 1 — `src/domain/nbb/fetcher.py`, function `parse_rubrics()`

**Find** (around line 142, the existing `return` block):

```python
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
```

**Replace with**:

```python
    return {
        "year":           year,
        "company_name":   data.get("EnterpriseName") or filing_meta.get("EnterpriseName",""),
        "model_type":     filing_meta.get("ModelType") or filing_meta.get("modelType",""),
        "reference":      filing_meta.get("referenceNumber",""),
        "fy_start":       fy_start_str,
        "fy_end":         fy_end_str,
        "period_months":  period_months,
        "amounts":        amounts,   # {code: eur_amount}
        # LB-005: surface depot-metadata through the parser
        "filing_date":    filing_meta.get("DepositDate")  or filing_meta.get("depositDate"),
        "deposit_type":   filing_meta.get("DepositType")  or filing_meta.get("depositType"),
    }
```

---

## Patch 2 — `src/enrichment/workers/nbb_financials.py`, function `_fetch_and_parse()`

**Find** (currently the `years.append({...})` block, with the literal `"filing_date": None,  # future:`):

```python
        years.append({
            "period_label":      year_str,
            "period_end":        fy_end,
            "fiscal_year_start": fy_start,
            "fiscal_year_end":   fy_end,
            "codes":             codes,
            "model_type":        parsed_dict.get("model_type") or None,
            "filing_date":       None,  # future: extract from ref_num/ref_meta
        })
```

**Replace with**:

```python
        years.append({
            "period_label":      year_str,
            "period_end":        fy_end,
            "fiscal_year_start": fy_start,
            "fiscal_year_end":   fy_end,
            "codes":             codes,
            "model_type":        parsed_dict.get("model_type") or None,
            # LB-005: filing_date is now extracted in fetcher.parse_rubrics
            "filing_date":       _parse_iso_date(parsed_dict.get("filing_date")),
        })
```

(The existing `_parse_iso_date` helper at the bottom of the file is null-safe, so this works even if the API ever omits `DepositDate`.)

---

## Patch 3 — `src/domain/nbb/fetcher.py`, **new helper** `get_filing_dates()`

**Append** to the bottom of `fetcher.py` (before `clear_cache` is fine, or right after `get_references` — author's choice):

```python
def get_filing_dates(vat: str, api_key: str) -> dict[str, dict]:
    """Pull DepositDate per filing reference for one entity.

    Returns: {referenceNumber: {filing_date, deposit_type, model_type}}
    Lane B uses this as a sidecar lookup since bulk-XBRL ZIPs don't bundle
    depot metadata. One API call per unique KBO, cheap and cacheable.

    On any failure (auth, network, missing key) raises NBBApiError with
    the underlying cause — caller decides whether to proceed with NULL.

    LB-005 implementation note: filing_date comes from the API field
    `DepositDate` (e.g. "2025-07-04") — confirmed via developer.cbso.nbb.be
    response inspection on 2026-05-08.
    """
    refs = get_references(vat, api_key)
    out: dict[str, dict] = {}
    for r in refs:
        ref = r.get("referenceNumber") or r.get("ReferenceNumber")
        if ref:
            out[str(ref)] = {
                "filing_date":  r.get("DepositDate")  or r.get("depositDate"),
                "deposit_type": r.get("DepositType")  or r.get("depositType"),
                "model_type":   r.get("ModelType")    or r.get("modelType"),
            }
    return out
```

---

## Verification (no DB writes)

After applying patches 1 & 2, you can sanity-check Lane A end-to-end without re-running the worker:

```python
# Quick probe — uses your existing NBB_API_KEY, hits one filing
from src.domain.nbb.fetcher import fetch_all_xbrl
results = fetch_all_xbrl("0401452019", api_key, use_cache=True)
year, parsed, ref = results[0]
print(f"year={year} ref={ref}")
print(f"  filing_date  = {parsed.get('filing_date')}")
print(f"  deposit_type = {parsed.get('deposit_type')}")
print(f"  model_type   = {parsed.get('model_type')}")
# Expect filing_date as 'YYYY-MM-DD' string, deposit_type='Initial' or similar
```

Patch 3 (`get_filing_dates`) is then used by `cli/ingest_nbb_zip.py` for the Lane B sidecar lookup (already wired in β1).

---

## What happens after these patches land

| Lane | Effect |
|---|---|
| Lane A (queue+worker) | New enrichment runs write `nbb_filing_date` to `fact_financials` automatically. Old rows stay NULL until re-enqueued. |
| Lane B (CLI) | Staging-rows (`_stg_nbb_filings.filing_date`) get filled at ingestion time via `get_filing_dates()`, unblocking `fn_promote_nbb_filing` for β3 sync-promote. |
| Lane B fallback (worker) | When LB-006 worker is built, it reads filing_date from staging — already filled by β1 CLI. No further changes needed there. |
