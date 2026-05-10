-- ════════════════════════════════════════════════════════════════════════
-- W8-core / inventory / consumer dependencies on fact_financials
-- ════════════════════════════════════════════════════════════════════════
-- Run BEFORE step 008 (rename). Identifies which database objects depend
-- on fact_financials so you know what could break.
--
-- Read-only. Output to stdout.
--
-- After review, decide:
--   - SELECT consumers: continue working via the new view (step 009)
--   - INSERT/UPDATE consumers: must be redirected to fact_financials_evidence
-- ════════════════════════════════════════════════════════════════════════

\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo 'W8-core consumer inventory — before deploy'
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'

\echo
\echo '── Views referencing fact_financials ────────────────────────'
SELECT
    schemaname,
    viewname,
    LEFT(definition, 200) AS first_200_chars
FROM pg_views
WHERE definition ILIKE '%fact_financials%'
  AND schemaname = 'public'
  AND viewname != 'fact_financials';

\echo
\echo '── Materialized views ───────────────────────────────────────'
SELECT
    schemaname,
    matviewname,
    LEFT(definition, 200) AS first_200_chars
FROM pg_matviews
WHERE definition ILIKE '%fact_financials%';

\echo
\echo '── Functions referencing fact_financials ────────────────────'
SELECT
    n.nspname AS schema_name,
    p.proname AS function_name,
    pg_get_function_arguments(p.oid) AS arguments,
    -- Detect probable usage type
    CASE
        WHEN p.prosrc ~* 'INSERT\s+INTO\s+(public\.)?fact_financials\s' THEN '⚠️ WRITE (INSERT)'
        WHEN p.prosrc ~* 'UPDATE\s+(public\.)?fact_financials\s' THEN '⚠️ WRITE (UPDATE)'
        WHEN p.prosrc ~* 'DELETE\s+FROM\s+(public\.)?fact_financials\s' THEN '⚠️ WRITE (DELETE)'
        WHEN p.prosrc ~* '(FROM|JOIN)\s+(public\.)?fact_financials\b' THEN 'READ (SELECT)'
        ELSE 'mention only'
    END AS usage_pattern
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.prosrc ILIKE '%fact_financials%'
  AND n.nspname IN ('public','gs_governance');

\echo
\echo '── RLS policies referencing fact_financials in their predicate ──'
SELECT
    schemaname,
    tablename,
    policyname,
    LEFT(qual::text, 150) AS predicate
FROM pg_policies
WHERE qual::text ILIKE '%fact_financials%'
   OR with_check::text ILIKE '%fact_financials%';

\echo
\echo '── Foreign keys to fact_financials ──────────────────────────'
SELECT
    conrelid::regclass AS dependent_table,
    conname AS constraint_name,
    pg_get_constraintdef(oid) AS definition
FROM pg_constraint
WHERE contype = 'f'
  AND confrelid = 'public.fact_financials'::regclass;

\echo
\echo '── Triggers on fact_financials ──────────────────────────────'
SELECT
    tgname AS trigger_name,
    pg_get_triggerdef(oid) AS definition
FROM pg_trigger
WHERE tgrelid = 'public.fact_financials'::regclass
  AND NOT tgisinternal;

\echo
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
\echo 'Review above output:'
\echo '  ✅ READ consumers (views, SELECT functions): will keep working via new view'
\echo '  ⚠️ WRITE consumers: must be redirected to fact_financials_evidence'
\echo '  ⚠️ Triggers: will move with the rename automatically — verify they still apply'
\echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
