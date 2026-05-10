-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 010 / post-deploy validation
-- ════════════════════════════════════════════════════════════════════════
-- Run AFTER all DDL applied. 8 tests; ALL must pass before continuing.
-- Read-only. Output goes to stdout for human review.
--
-- v3 (2026-05-10): added T7 (INSTEAD OF triggers) and T8 (vw_target_financials
-- SELECT compat — verifies all 20 columns it reads are exposed by view).
-- ════════════════════════════════════════════════════════════════════════

\echo '════════════════════════════════════════════════════════════════════'
\echo 'W8-core post-deploy validation — 8 tests'
\echo '════════════════════════════════════════════════════════════════════'

-- ─── T1: All new tables exist + RLS enabled ─────────────────────────────
\echo ''
\echo 'T1: Table existence + RLS check'

SELECT
    tablename,
    rowsecurity AS rls_enabled
FROM pg_tables
WHERE schemaname = 'public'
  AND tablename = ANY (ARRAY[
        'fact_filings',
        'fact_financials_lines',
        'fact_financials_overrides',
        'dim_pcmn_codes',
        'fact_financials_evidence'
  ])
ORDER BY tablename;

\echo 'PASS criteria: 5 rows, all rls_enabled = true'

-- ─── T2: dim_pcmn_codes seed correctness (exact counts) ─────────────────
\echo ''
\echo 'T2: dim_pcmn_codes seed correctness — exact counts per priority'

SELECT
    v4g_priority,
    count(*) AS code_count
FROM public.dim_pcmn_codes
WHERE deprecated_at IS NULL
GROUP BY v4g_priority
ORDER BY v4g_priority;

\echo 'PASS criteria (W8-core seed v1):'
\echo '  HIGH   = 21'
\echo '  MEDIUM =  3'
\echo '  total  = 24'

\echo ''
\echo 'T2b: section breakdown'

SELECT
    section,
    count(*) AS code_count
FROM public.dim_pcmn_codes
WHERE deprecated_at IS NULL
GROUP BY section
ORDER BY section;

\echo 'PASS criteria: PL=12, BS_A=7, BS_L=4, WORKERS=1'

-- ─── T3: fact_financials view exists and queryable ──────────────────────
\echo ''
\echo 'T3: fact_financials view existence (must be VIEW, not TABLE)'

SELECT
    schemaname,
    viewname,
    viewowner
FROM pg_views
WHERE schemaname = 'public'
  AND viewname = 'fact_financials';

\echo 'PASS criteria: 1 row showing fact_financials as a view'

\echo ''
\echo 'T3b: fact_financials view row count'

SELECT count(*) AS visible_rows
FROM public.fact_financials;

\echo 'PASS criteria: visible_rows = count of fact_financials_evidence rows'
\echo '(2,124 expected based on inventory)'

-- ─── T4: existing consumer vw_target_financials still works ─────────────
\echo ''
\echo 'T4: vw_target_financials backwards compat'

SELECT count(*) AS visible_rows
FROM public.vw_target_financials;

\echo 'PASS criteria: query succeeds (proves the rename + view-recreate did NOT'
\echo 'break the consumer chain). Row count should match pre-deploy count.'

-- ─── T5: governance entries logged ──────────────────────────────────────
\echo ''
\echo 'T5: schema_register entries'

SELECT
    object_type,
    object_name,
    severity,
    status
FROM gs_governance.schema_register
WHERE issue_type IN ('migration_w8_core', 'migration_w8_core_rename')
  AND owner = 'chris'
ORDER BY object_name;

\echo 'PASS criteria: 7 entries, all status=resolved'

-- ─── T6: convenience view fact_financials_overrides_current ─────────────
\echo ''
\echo 'T6: fact_financials_overrides_current convenience view'

SELECT
    schemaname,
    viewname
FROM pg_views
WHERE schemaname = 'public'
  AND viewname = 'fact_financials_overrides_current';

\echo 'PASS criteria: 1 row showing the convenience view exists'

\echo ''
\echo 'T6b: convenience view row count = active overrides count'

WITH
    in_table AS (
        SELECT count(*) AS n
        FROM public.fact_financials_overrides
        WHERE superseded_by IS NULL
    ),
    in_view AS (
        SELECT count(*) AS n
        FROM public.fact_financials_overrides_current
    )
SELECT
    in_table.n AS active_in_table,
    in_view.n  AS shown_in_view,
    CASE
        WHEN in_table.n = in_view.n THEN 'PASS'
        ELSE 'FAIL'
    END        AS status
FROM in_table, in_view;

\echo 'PASS criteria: active_in_table = shown_in_view AND status = PASS'

-- ─── T7: INSTEAD OF triggers attached to fact_financials view ───────────
\echo ''
\echo 'T7: INSTEAD OF triggers on fact_financials view'

SELECT
    tgname        AS trigger_name,
    CASE tgtype & 28
        WHEN  4 THEN 'INSERT'
        WHEN 16 THEN 'UPDATE'
        WHEN  8 THEN 'DELETE'
        ELSE        'OTHER'
    END           AS trigger_event,
    proname       AS function_name
FROM pg_trigger t
JOIN pg_proc p ON p.oid = t.tgfoid
WHERE tgrelid = 'public.fact_financials'::regclass
  AND NOT tgisinternal
ORDER BY tgname;

\echo 'PASS criteria: 3 rows — one each for INSERT/UPDATE/DELETE,'
\echo 'all referencing fact_financials_view_* functions'

-- ─── T8: vw_target_financials column compat with new view ───────────────
\echo ''
\echo 'T8: vw_target_financials column compat — all 20 columns exposed'

WITH expected_cols AS (
    SELECT unnest(ARRAY[
        'party_id', 'period_end', 'period_label', 'period_type',
        'revenue_eur_m', 'ebitda_eur_m', 'ebit_eur_m', 'net_income_eur_m',
        'total_assets_eur_m', 'total_equity_eur_m',
        'total_debt_eur_m', 'net_debt_eur_m', 'working_capital_eur_m',
        'enterprise_value_eur_m', 'market_cap_eur_m', 'employees',
        'nbb_model_type', 'nbb_filing_date',
        'source_code', 'confidence'
    ]) AS col_name
),
view_cols AS (
    SELECT column_name
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'fact_financials'
)
SELECT
    e.col_name,
    CASE WHEN v.column_name IS NOT NULL THEN 'present' ELSE 'MISSING' END AS status
FROM expected_cols e
LEFT JOIN view_cols v ON v.column_name = e.col_name
ORDER BY status DESC, e.col_name;

\echo 'PASS criteria: all 20 rows show status = present (none MISSING)'

-- ─── INFO: source breakdown sanity (informational) ──────────────────────
\echo ''
\echo 'INFO: fact_financials_evidence source breakdown (post-rename)'

SELECT
    source_code,
    count(*)        AS row_count,
    min(period_end) AS earliest,
    max(period_end) AS latest
FROM public.fact_financials_evidence
GROUP BY source_code
ORDER BY count(*) DESC;

\echo ''
\echo '════════════════════════════════════════════════════════════════════'
\echo 'If all 8 tests pass: W8-core foundation is ready.'
\echo 'Next: feature/W8-worker branch (NBB extractor + dual-write)'
\echo '════════════════════════════════════════════════════════════════════'
