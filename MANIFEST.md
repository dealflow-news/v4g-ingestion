# MANIFEST · v4g-ingestion SQL & code artifacts

**Doctrine.** The repo holds the *truth-as-intent* of every SQL change; Supabase holds the *state*. When state and intent disagree, this manifest is how to reason about what should happen next.

Every `.sql` file in `sql/migrations/` and `sql/governance/` follows the GS-SOP-003 three-part shape: PART 1 MIGRATION (transactional `BEGIN…COMMIT`), PART 2 VERIFY (read-only checks), PART 3 ROLLBACK (commented-out, paste-and-run when needed). VERIFY blocks are safe to re-run any time.

---

## sql/migrations/ · forward-only schema mutations

| Code | File | Status | Live since | Affects | One-line summary |
|---|---|---|---|---|---|
| LB-001 | `GS-MIGRATE-LB-001.sql` | ✅ live | 2026-05-08 | `_stg_nbb_filings` | Lane B staging table — raw XBRL + parsed metadata, idempotent on `filing_reference`. Originally numbered `GS-MIGRATE-023`; renamed after a version-string collision with the 2026-03-12 `fact_participations` migration. |
| LB-002 | `GS-MIGRATE-LB-002.sql` | ✅ live | 2026-05-08 | `fn_promote_nbb_filing(uuid, jsonb)`, `fact_financials.source_code` comment | Atomic promotion: latest `filing_date` wins per `(kbo_nr, fiscal_year_end)`; older parsed filings demoted to `'superseded'`; UPSERT on `(party_id, period_label, source_code='SRC_NBB')`. |
| LB-003 | `GS-MIGRATE-LB-003.sql` | ✅ live | 2026-05-08 | `gs_enrichment.policy` | Policy seed: new event-policy `nbb_xbrl_staging_arrival` + extension of the `manual` policy with `nbb_xbrl_parse`. |
| LB-004 | `GS-MIGRATE-LB-004.sql` | ⚠️ **optional** | — | trigger on `_stg_nbb_filings`, `fn_stg_nbb_filing_enqueue()` | Auto-enqueue `nbb_xbrl_parse` tasks via `gs_enrichment.enqueue` on staging arrival. **Optional under the sync-in-CLI architecture (β3).** Useful only as a β4 fallback for re-processing or if Lane B ever needs an event-driven path. |

## sql/governance/ · schema_register / changelog batches

| File | Status | Affects | Summary |
|---|---|---|---|
| `GS-GOVERNANCE-2026-05-08.sql` | ⏳ pending | `gs_governance.schema_register` (5 INSERTs) | Five findings: exporter scan-gap (gs_enrichment missing in `gs_dict_export.py` SCHEMAS), `deal_registry` ordinal-gap #18, duplicate FK on `sector_l1_code`, comment-drift on `canonical_deal_id`, `changelog.change_type` enum doc-drift. |

## src/cli/ · operator-facing entrypoints

| File | Phase | Status | Summary |
|---|---|---|---|
| `__init__.py` | — | ready | Package marker; states three-act doctrine. |
| `export_financials_xlsx.py` | β0 | ready | Read-only export of one party's financials from `vw_target_financials` to a 3-sheet Excel (Pivot, Raw, Provenance). Validates the architecture without any DB writes. |
| `ingest_nbb_zip.py` | β1 | ready | Stages a NBB CBSO bulk-XBRL ZIP into `_stg_nbb_filings`. Idempotent on `filing_reference`. Lazy-fetches `DepositDate` per KBO via `fetcher.get_filing_dates` (β2). |

## docs/ · session deliverables

| File | Phase | Summary |
|---|---|---|
| `LB-005-filing-date-patches.md` | β2 | Three small patches to wire `DepositDate` from the NBB references API through to `fact_financials.nbb_filing_date` for both Lane A and Lane B. |

---

## Phasing summary

| Phase | Concern | Files |
|---|---|---|
| **β0** read-only validation | Confirms the analytical layer (`vw_target_financials`) renders cleanly to Excel | `cli/export_financials_xlsx.py` |
| **β1** staging-only writes | Stages NBB ZIPs without canonical writes — safe to iterate against AB LENS MOTOR canary | `cli/ingest_nbb_zip.py` |
| **β2** filing-date wiring | Surfaces `DepositDate` for both lanes, unblocks promotion | `docs/LB-005-filing-date-patches.md` |
| **β3** sync promote (next) | Extend `cli/ingest_nbb_zip.py` to also call `fn_promote_nbb_filing` per filing | — |
| **β4** queue/worker fallback (optional) | LB-004 trigger + LB-006 worker for async re-processing | `sql/migrations/GS-MIGRATE-LB-004.sql` |

## Recommended deployment order from a clean checkout

1. **Already-live migrations (LB-001..LB-003)** are documentation + rollback source — do *not* re-run against production.
2. `sql/governance/GS-GOVERNANCE-2026-05-08.sql` — adds the 5 schema_register entries.
3. Apply the β2 patches in `docs/LB-005-filing-date-patches.md` to `src/domain/nbb/fetcher.py` and `src/enrichment/workers/nbb_financials.py`.
4. `pip install openpyxl` (and add `"openpyxl>=3.1"` to `pyproject.toml [project] dependencies` for next time).
5. Validate β0 against a known party with SRC_NBB rows (e.g. V4G's own party_id).
6. Validate β1 against the AB LENS MOTOR ZIP; expect 20 rows in `_stg_nbb_filings`.
7. Decide whether to run `sql/migrations/GS-MIGRATE-LB-004.sql` (optional β4 fallback).
