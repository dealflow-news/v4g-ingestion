-- ============================================================================
-- GS-MIGRATE-LB-003 · Lane B policy seed
-- ----------------------------------------------------------------------------
-- Purpose: 1 new event-policy + 1 update to manual-policy. Adds the policy
--          rules needed to dispatch nbb_xbrl_parse tasks. The actual
--          AFTER INSERT trigger op _stg_nbb_filings volgt in LB-004.
-- ============================================================================

BEGIN;

-- 1. New event-policy
INSERT INTO gs_enrichment.policy (
  policy_code, description, trigger_kind, target_types, config
)
VALUES (
  'nbb_xbrl_staging_arrival',
  'New filing arrives in _stg_nbb_filings with parse_status=pending. Auto-enqueue an nbb_xbrl_parse task to claim and promote the filing to fact_financials via fn_promote_nbb_filing (LB-002). Trigger source: AFTER INSERT trigger op _stg_nbb_filings (added in LB-004).',
  'event',
  ARRAY['nbb_xbrl_parse'],
  jsonb_build_object(
    'staging_table', '_stg_nbb_filings',
    'pending_only', true
  )
);

-- 2. Extend manual policy to allow nbb_xbrl_parse re-enqueue
UPDATE gs_enrichment.policy
SET target_types = ARRAY[
      'nbb_financials',
      'kbo_directors',
      'actor_classification',
      'profile_completion',
      'nbb_xbrl_parse'
    ],
    updated_at = now()
WHERE policy_code = 'manual';

-- 3. Changelog
INSERT INTO public.changelog (change_type, version, description, affected_tables)
VALUES (
  'ref_data',
  'GS-MIGRATE-LB-003',
  'Lane B policy seed: new event-policy ''nbb_xbrl_staging_arrival'' triggers ''nbb_xbrl_parse'' tasks on staging-row arrival. Manual policy extended to include ''nbb_xbrl_parse'' for CLI/admin re-enqueue. Actual DB trigger + Python worker follow in LB-004 / CLI.',
  ARRAY['policy']
);

COMMIT;

-- ============================================================================
-- PART 2: VERIFY
-- ============================================================================
-- Expected: manual heeft 5 target_types incl. nbb_xbrl_parse, nieuwe policy heeft 1
SELECT policy_code, trigger_kind, target_types, is_active
FROM gs_enrichment.policy
WHERE policy_code IN ('nbb_xbrl_staging_arrival','manual')
ORDER BY policy_code;

-- Expected: 1 row
SELECT version, change_type, change_date
FROM public.changelog
WHERE version = 'GS-MIGRATE-LB-003';

-- ============================================================================
-- PART 3: ROLLBACK
-- ============================================================================
/*
BEGIN;
DELETE FROM gs_enrichment.policy WHERE policy_code = 'nbb_xbrl_staging_arrival';
UPDATE gs_enrichment.policy
SET target_types = ARRAY['nbb_financials','kbo_directors','actor_classification','profile_completion'],
    updated_at = now()
WHERE policy_code = 'manual';
DELETE FROM public.changelog WHERE version = 'GS-MIGRATE-LB-003';
COMMIT;
*/
