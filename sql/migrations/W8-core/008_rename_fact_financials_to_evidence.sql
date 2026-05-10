-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 008 / rename fact_financials → fact_financials_evidence
-- ════════════════════════════════════════════════════════════════════════
-- ⚠️ MEDIUM RISK STEP — once-only operation
--
-- Rationale: existing fact_financials table has 4 source kinds:
--   SRC_NBB  (38 rows)   → moves to fact_filings + fact_financials_lines
--   SRC_PB   (1148 rows) → stays here, renamed table
--   SRC_ODB  (929 rows)  → stays here, renamed table
--   SRC_V4G  (47 rows)   → stays here, renamed table
--
-- New role of renamed table: KPI-level evidence from 3rd-party aggregators.
-- A view named fact_financials replaces (step 009) for backwards compat.
--
-- PRE-DEPLOY CHECKS (run manually):
--   1. NBB worker MUST be stopped (otherwise concurrent writes break)
--   2. Run inventory_consumers.sql to identify dependent objects
--   3. Verify no application code does direct INSERT on fact_financials
--      (those will break — view in step 009 is read-only)
--
-- POST-DEPLOY:
--   - All FK constraints from other tables to fact_financials.financial_id
--     auto-update (Postgres tracks via OID)
--   - All RLS policies move with the table
--   - All indexes move with the table
--   - Existing application-code SELECT queries on "fact_financials" will
--     temporarily fail until step 009 creates the view
--
-- ROLLBACK: ALTER TABLE public.fact_financials_evidence RENAME TO fact_financials;
-- ════════════════════════════════════════════════════════════════════════

-- Verify pre-conditions
DO $$
BEGIN
    -- Check source table exists
    IF NOT EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname='public' AND tablename='fact_financials'
    ) THEN
        RAISE EXCEPTION 'fact_financials table not found — already renamed?';
    END IF;

    -- Check target name not yet in use
    IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname='public' AND tablename='fact_financials_evidence'
    ) THEN
        RAISE EXCEPTION 'fact_financials_evidence already exists — rename already done?';
    END IF;
END $$;

-- THE rename
ALTER TABLE public.fact_financials RENAME TO fact_financials_evidence;

-- Update comment to reflect new role
COMMENT ON TABLE public.fact_financials_evidence IS
    '[FACT] KPI-level financial evidence from 3rd-party aggregators (SRC_PB PitchBook, SRC_ODB Open The Box) + V4G manual entries (SRC_V4G). NBB-source rows here are DEPRECATED as of W8 — superseded by fact_filings + fact_financials_lines (line-granulair). Read via fact_financials VIEW which blends NBB-derived + evidence + overrides per BE-aware precedence. See GOLDEN_SAFE_SOP.md / W8 doctrine.';

-- Confirm rename
DO $$
DECLARE
    v_row_count bigint;
BEGIN
    SELECT count(*) INTO v_row_count FROM public.fact_financials_evidence;
    RAISE NOTICE 'Rename successful. fact_financials_evidence now contains % rows.', v_row_count;
END $$;
