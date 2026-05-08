-- ============================================================================
-- Migration GS-M-2026-001: LB-008 — Add raw_json_xbrl JSONB cache column
-- ============================================================================
-- Rationale: Lane B β3 (sync-promote) for cbso-new filings calls NBB JSON-XBRL
-- API per filing. Caching the response on the staging row avoids re-fetching
-- on re-promote runs (e.g. tests/repromote_filings.py) and prepares for bulk
-- ingestion where rate limits become a concern.
--
-- Impact:
--   - src/cli/ingest_nbb_zip.py::_extract_year_data reads/writes this column
--   - tests/repromote_filings.py benefits transparently (calls promote_filings)
--   - No impact on Lane A enrichment (uses separate cache dir on disk)
--   - No impact on pfs-old promotion path (raw_xbrl XML still used)
--
-- Rollback risk: minimal — new nullable column, no backfill, no FK.
-- Code degrades gracefully if column missing (cache write wrapped in try/except).
-- ============================================================================

-- ── Pre-flight ──
SELECT count(*) AS staging_rows_total FROM public._stg_nbb_filings;
SELECT count(*) AS cbso_new_rows
FROM   public._stg_nbb_filings
WHERE  taxonomy_format = 'cbso-new';

SELECT column_name, data_type, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name = '_stg_nbb_filings'
  AND  column_name = 'raw_json_xbrl';
-- expected: 0 rows (column doesn't exist yet)

-- ── MIGRATION ──
ALTER TABLE public._stg_nbb_filings
    ADD COLUMN IF NOT EXISTS raw_json_xbrl JSONB;

COMMENT ON COLUMN public._stg_nbb_filings.raw_json_xbrl IS
'[CACHE] Cached NBB JSON-XBRL response for cbso-new filings. Populated
lazily by Lane B β3 promote (src/cli/ingest_nbb_zip.py). Avoids re-fetching
on re-promote runs. Null for pfs-old (uses raw_xbrl XML instead). LB-008.';

-- ── VERIFY ──
SELECT column_name, data_type, is_nullable
FROM   information_schema.columns
WHERE  table_schema = 'public'
  AND  table_name = '_stg_nbb_filings'
  AND  column_name = 'raw_json_xbrl';
-- expected: 1 row, data_type=jsonb, is_nullable=YES

-- ── ROLLBACK (commented — run manually if needed) ──
-- ALTER TABLE public._stg_nbb_filings DROP COLUMN IF EXISTS raw_json_xbrl;


-- ============================================================================
-- Schema register entries — LB-007, LB-008, LB-009, LB-010
-- ============================================================================
-- Per GS-SOP-002 / GS-SOP-010, document each finding for governance audit
-- trail. status='resolved' for already-fixed items, owner='Chris'.
-- ============================================================================

INSERT INTO gs_governance.schema_register
  (object_type, schema_name, object_name, column_name,
   issue_type, severity, description, recommended_action,
   status, owner, decision, decision_at, source)
VALUES

-- LB-007: cbso-new dimensional XBRL bug
('function', 'public', 'fn_promote_nbb_filing', NULL,
 'lane_b_b3_cbso_new_parser_inadequate', 'critical',
 'Lane B β3 used parse_xbrl() (bulk-XBRL) for cbso-new filings, which '
 'have dimensional XBRL: bas:m70 had multiple contextual values across '
 '(part, bkd, ntr, ...) dimensions. parse_xbrl picked the wrong one '
 'consistently, yielding revenue=1.47M placeholder for AB LENS MOTOR''s '
 '4 cbso-new filings (2021-2024) instead of real ~30-48M values.',
 'Branch _build_canonical_jsonb on taxonomy_format. cbso-new now uses '
 'fetch_jsonxbrl + parse_rubrics (NBB normalizes dimensional XBRL '
 'server-side, returns canonical PCMN codes). pfs-old keeps parse_xbrl. '
 'Verified end-to-end on AB LENS MOTOR: revenue 36-48M, EBITDA, EBIT, '
 'total_assets, equity, employees all populated.',
 'resolved', 'Chris',
 'Fixed in commit f323f9f. Re-promoted 4 affected filings via '
 'tests/repromote_filings.py. fact_financials now correct for all 23 '
 'years on AB LENS MOTOR.',
 now(), 'manual'),

-- LB-008: raw_json_xbrl cache column
('column', 'public', '_stg_nbb_filings', 'raw_json_xbrl',
 'lane_b_b3_cbso_new_cache', 'info',
 'Each cbso-new promote call hits NBB API (~250ms). For re-promote '
 'workflows and future bulk ingestion runs, caching the JSON-XBRL '
 'response on the staging row avoids redundant API calls and prepares '
 'for rate-limit-aware bulk loading.',
 'Added raw_json_xbrl JSONB column (this migration). _extract_year_data '
 'checks cache first, lazy-writes on miss. Cache write failures wrapped '
 'in try/except so missing-column scenarios degrade gracefully.',
 'resolved', 'Chris',
 'Implemented as part of LB-007 follow-up. Schema migrated, code uses '
 'cache transparently.',
 now(), 'manual'),

-- LB-009: cash mapping correction
('function', 'public', 'aggregate_year', NULL,
 'lane_a_b_cash_pcmn_code_mismatch', 'warning',
 'aggregator DIRECT_MAP had pcmn 50/53 → cash. Per official Belgian '
 'PCMN, 50/53 is "Geldbeleggingen" (current investments / securities) '
 'and 54/58 is "Liquide middelen" (actual cash). NBB JSON-XBRL uses '
 'the canonical 54/58. Legacy pfs-old taxonomy tagged pfs:CashBankHand '
 'as 50/53 (label mismatch but data flow worked for pre-2021). For '
 'cbso-new this caused cash_eur_m=NULL across 4 AB LENS MOTOR years '
 'after LB-007 fix.',
 'Add 54/58 to DIRECT_MAP alongside 50/53. cash extraction tries 54/58 '
 'first (canonical), falls back to 50/53 (legacy pfs-old). Same fallback '
 'pattern as 20/58 vs 20/28 for total_assets.',
 'resolved', 'Chris',
 'Fixed in src/domain/nbb/aggregator.py.',
 now(), 'manual'),

-- LB-010: CLI counter cosmetic clarification
('function', 'public', 'fn_promote_nbb_filing', NULL,
 'lane_b_b3_counter_misleading', 'info',
 'CLI summary "20 promoted, 0 superseded" was technically per-call '
 'correct but misleading: when sibling A promotes first, then sibling B '
 '(later filing_date) promotes and demotes A, the function returns '
 'outcome=parsed for both calls. CLI showed promoted=2 even though net '
 'active rows in fact_financials = 1. Accurate but confusing for '
 'analysts checking "did the doctrine work".',
 'promote_filings now tracks demoted_by_others (sum of '
 'demoted_older_filings across all calls). CLI summary shows "X promoted '
 '(Y demoted later by sibling)" plus "→ N net active rows". Per-filing '
 'output unchanged ("(demoted N older)" tag).',
 'resolved', 'Chris',
 'Fixed in src/cli/ingest_nbb_zip.py promote_filings + run().',
 now(), 'manual')

ON CONFLICT (schema_name, object_name, COALESCE(column_name, ''),
             COALESCE(constraint_name, ''), issue_type)
WHERE status NOT IN ('resolved', 'wontfix', 'superseded')
DO NOTHING;

-- ── VERIFY ──
SELECT issue_type, severity, status, owner,
       LEFT(description, 60) || '…' AS description_preview
FROM   gs_governance.schema_register
WHERE  issue_type IN (
    'lane_b_b3_cbso_new_parser_inadequate',
    'lane_b_b3_cbso_new_cache',
    'lane_a_b_cash_pcmn_code_mismatch',
    'lane_b_b3_counter_misleading'
)
ORDER  BY created_at DESC;
-- expected: 4 rows, all status='resolved'

-- ============================================================================
-- Optional: backfill cache for already-promoted cbso-new filings
-- ============================================================================
-- The 4 AB LENS MOTOR cbso-new rows currently have raw_json_xbrl=NULL even
-- though they were re-promoted via repromote_filings.py earlier (cache write
-- failed silently because the column didn't exist yet). Future re-promote
-- runs will hit the API again. Three options:
--
--   1. Do nothing — re-promotes are rare; one extra API call each is fine
--   2. Run repromote_filings.py once more — populates cache, re-runs RPC
--      (idempotent, just an extra API call now to save future ones)
--   3. Manual UPDATE if we have the JSON cached elsewhere
--
-- Recommended: option 1 unless you plan to re-promote in the next week.
