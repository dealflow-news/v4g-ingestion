-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 007 / governance schema_register entries
-- ════════════════════════════════════════════════════════════════════════
-- Per GS-SOP-002 / GS-SOP-010: every schema change logs an entry.
-- Tracks the W8-core migration in gs_governance.schema_register.
--
-- v3 (2026-05-10): canonical column names per actual schema:
--   issue_id (UUID, gen_random_uuid()), object_type, schema_name,
--   object_name, issue_type, severity, description, status,
--   owner, source ('manual'). 'rename' is NOT a valid object_type
--   (per chk_object_type) — using 'table' for the rename entry.
--
-- Idempotent: ON CONFLICT skips re-insert via natural-key dedup.
-- Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

INSERT INTO gs_governance.schema_register
    (issue_id,
     object_type, schema_name, object_name,
     issue_type, severity, description,
     status, owner, source)
VALUES
    (gen_random_uuid(),
     'table', 'public', 'dim_pcmn_codes',
     'migration_w8_core',
     'info',
     'W8-core-001: New reference dim for PCMN/MAR codes. 24 codes seeded with NL/EN labels, V4G priority, valuation relevance. Multi-language support (FR pending). Not FK-locked from fact_financials_lines (allows new taxonomy codes). Versioning via deprecated_at; never DELETE.',
     'resolved', 'chris', 'manual'),

    (gen_random_uuid(),
     'table', 'public', 'fact_filings',
     'migration_w8_core',
     'info',
     'W8-core-002: New filing-level metadata table. One row per NBB filing (replaces NBB-source rows in fact_financials). Tracks period_start/end (was missing!), period_months, period_flag (extended-FY detection), nbb_model_type, taxonomy_version, point-in-time company state. Versioned via superseded_by for restatements.',
     'resolved', 'chris', 'manual'),

    (gen_random_uuid(),
     'table', 'public', 'fact_financials_lines',
     'migration_w8_core',
     'info',
     'W8-core-003: New granular financial line items. One row per (filing, pcmn_code, period, data_type). Raw EUR values, NULL allowed for empty rubrics. PK includes data_type to distinguish met:am1 (amounts) from met:cnt1 (counts like FTE 9087). FK to fact_filings; cascade delete.',
     'resolved', 'chris', 'manual'),

    (gen_random_uuid(),
     'table', 'public', 'fact_financials_overrides',
     'migration_w8_core',
     'info',
     'W8-core-004: New analyst-narrative table. Captures management adjustments from interviews. Categories: NORMALIZATION/PROFORMA/CORRECTION/OTHER. Mandatory reason + recorded_by. Hard delete forbidden — version via superseded_by. Partial UNIQUE: only one ACTIVE override per (party, period, metric). RLS: SELECT for authenticated, ALL for service_role; application layer enforces senior-analyst check before service_role connection. NOTE: metric_key is unguarded text in W8-core (Phase-2/3 backlog item — see W8_DOCTRINE.md).',
     'resolved', 'chris', 'manual'),

    (gen_random_uuid(),
     'table', 'public', 'fact_financials_evidence',
     'migration_w8_core_rename',
     'warning',
     'W8-core-005: RENAMED from public.fact_financials → public.fact_financials_evidence to clarify role: KPI-level evidence from non-NBB sources (PB, ODB, V4G). NBB rows in this table will be deprecated post-validation (separate cleanup PR). View fact_financials replaces with BE-aware precedence blending. Note: object_type=table because chk_object_type does not allow ''rename''.',
     'resolved', 'chris', 'manual'),

    (gen_random_uuid(),
     'view', 'public', 'fact_financials',
     'migration_w8_core',
     'info',
     'W8-core-006: Replaced TABLE with VIEW. Blends NBB-derived KPIs (from fact_filings + fact_financials_lines) + 3rd-party evidence (fact_financials_evidence) + analyst overrides (fact_financials_overrides). BE-aware precedence: BE entities → override > NBB > ODB > PB > V4G. Non-BE → override > PB > V4G. Exposes both effective values and *_as_reported columns + has_overrides flag.',
     'resolved', 'chris', 'manual'),

    (gen_random_uuid(),
     'view', 'public', 'fact_financials_overrides_current',
     'migration_w8_core',
     'info',
     'W8-core-007: Convenience view exposing only active overrides (WHERE superseded_by IS NULL). Default-case shortcut for application/UI queries. Full history queryable via fact_financials_overrides directly.',
     'resolved', 'chris', 'manual')

ON CONFLICT DO NOTHING;
-- Note: schema_register has no natural unique constraint on (object_type, schema_name, object_name)
-- by default. ON CONFLICT DO NOTHING above is a safety net for the (rare) case where
-- such a constraint exists; otherwise the unique deduplication relies on idempotent re-runs
-- being protected by external means (or accepting duplicate INSERT semantics; the issue_id
-- itself is unique via gen_random_uuid()). For strict idempotency, consider adding:
--    DELETE FROM gs_governance.schema_register
--    WHERE issue_type = 'migration_w8_core' AND object_name LIKE 'fact_%' OR ...
-- before INSERT. We do NOT do this here because deletion would fight the audit-trail
-- doctrine ("never hard delete governance records").

-- Quick sanity verify
DO $$
DECLARE
    v_count integer;
BEGIN
    SELECT count(*) INTO v_count
    FROM gs_governance.schema_register
    WHERE issue_type IN ('migration_w8_core', 'migration_w8_core_rename')
      AND owner = 'chris'
      AND object_name IN (
        'dim_pcmn_codes',
        'fact_filings',
        'fact_financials_lines',
        'fact_financials_overrides',
        'fact_financials_evidence',
        'fact_financials',
        'fact_financials_overrides_current'
      );
    RAISE NOTICE 'W8-core schema_register entries: % (expected 7)', v_count;
    IF v_count < 7 THEN
        RAISE WARNING 'Expected 7 W8-core entries; got %. Re-running this migration is idempotent if you delete by issue_id first.', v_count;
    END IF;
END $$;
