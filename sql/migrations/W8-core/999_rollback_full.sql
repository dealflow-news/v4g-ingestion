-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 999 / FULL ROLLBACK (emergency only)
-- ════════════════════════════════════════════════════════════════════════
-- ⚠️ DESTRUCTIVE: drops all W8-core objects in reverse dependency order.
--
-- Use only when:
--   - Validation tests fail and a fresh deploy is needed, OR
--   - Critical issue found post-deploy and quick reversal needed
--
-- After running this, DB state is identical to pre-W8-core deploy:
--   - fact_financials is a TABLE again (restored from evidence)
--   - All 4 new tables removed
--   - All trigger functions removed
--   - schema_register entries marked superseded
--
-- v2 (2026-05-10): explicit DROP FUNCTION for the 3 INSTEAD OF trigger fns.
-- (DROP VIEW cascades the triggers but not the trigger functions themselves.)
-- ════════════════════════════════════════════════════════════════════════

BEGIN;

-- Step 9b reversal: drop INSTEAD OF trigger functions explicitly
-- (DROP VIEW below will cascade-drop the trigger objects, but the
-- functions persist as orphans without explicit cleanup.)
DROP FUNCTION IF EXISTS public.fact_financials_view_insert() CASCADE;
DROP FUNCTION IF EXISTS public.fact_financials_view_update() CASCADE;
DROP FUNCTION IF EXISTS public.fact_financials_view_delete() CASCADE;

-- Step 9 reversal: drop the view (also drops any remaining triggers via cascade)
DROP VIEW IF EXISTS public.fact_financials CASCADE;

-- Step 8 reversal: rename evidence back to fact_financials
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname='public' AND tablename='fact_financials_evidence'
    ) THEN
        ALTER TABLE public.fact_financials_evidence RENAME TO fact_financials;
        RAISE NOTICE 'Renamed fact_financials_evidence back to fact_financials';
    END IF;
END $$;

-- Step 4b reversal: drop convenience view
DROP VIEW IF EXISTS public.fact_financials_overrides_current CASCADE;

-- Step 4 reversal: drop overrides
DROP TABLE IF EXISTS public.fact_financials_overrides CASCADE;

-- Step 3 reversal: drop lines (must come before filings due to FK)
DROP TABLE IF EXISTS public.fact_financials_lines CASCADE;

-- Step 2 reversal: drop filings
DROP TABLE IF EXISTS public.fact_filings CASCADE;

-- Step 1 reversal: drop dim_pcmn_codes
DROP TABLE IF EXISTS public.dim_pcmn_codes CASCADE;

-- Step 7 reversal: mark governance entries as rolled back (don't delete — history)
UPDATE gs_governance.schema_register
SET status      = 'wontfix',
    description = description || ' [ROLLED BACK on ' || now()::text || ']',
    updated_at  = now()
WHERE issue_type IN ('migration_w8_core', 'migration_w8_core_rename')
  AND status = 'resolved';

-- Verify restoration
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname='public' AND tablename='fact_financials'
    ) THEN
        RAISE EXCEPTION 'Rollback INCOMPLETE: fact_financials table not restored';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_tables
        WHERE schemaname='public' AND tablename = ANY (ARRAY[
            'fact_filings',
            'fact_financials_lines',
            'fact_financials_overrides',
            'dim_pcmn_codes'
        ])
    ) THEN
        RAISE EXCEPTION 'Rollback INCOMPLETE: new tables still present';
    END IF;

    IF EXISTS (
        SELECT 1 FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname = 'public'
          AND p.proname IN (
            'fact_financials_view_insert',
            'fact_financials_view_update',
            'fact_financials_view_delete'
          )
    ) THEN
        RAISE EXCEPTION 'Rollback INCOMPLETE: trigger functions still present';
    END IF;

    RAISE NOTICE 'Rollback verified complete. fact_financials restored as TABLE; all W8-core objects dropped.';
END $$;

COMMIT;

\echo ''
\echo '✅ W8-core fully rolled back. DB state restored to pre-W8.'
\echo 'You can re-run 001_*.sql through 010_*.sql for a fresh deploy.'
