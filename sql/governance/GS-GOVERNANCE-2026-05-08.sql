-- ============================================================================
-- GS-GOVERNANCE-2026-05-08 · Five schema_register entries
-- ----------------------------------------------------------------------------
-- Status:   PENDING — to be run live (idempotent if you guard with NOT EXISTS,
--           but as written below it will INSERT new rows; do not re-run).
-- Source:   Manual review on 2026-05-08; observations crystallized while
--           building Lane B (LB-001..LB-004).
-- Snapshot: 20260424_150949 (last public snapshot — predates LB-001..LB-004).
--
-- Five governance findings:
--   1. Exporter scan-gap — gs_enrichment is missing from gs_dict_export.py
--      SCHEMAS_TO_INTROSPECT. Schema lives since 2026-04-20 but is invisible
--      to dictionary, taxonomy, security report, health checks.
--   2. deal_registry — ordinal gap at column position #18 (dropped column
--      visible via attnum sequence).
--   3. deal_registry — two FK constraints on column sector_l1_code, both
--      pointing at the same parent (one is redundant).
--   4. deal_registry — table comment refers to FK by parent column name
--      "canonical_id" instead of actual local column name "canonical_deal_id".
--   5. changelog — live CHECK constraint on changelog.change_type permits
--      values that are not listed in PROJECT_INSTRUCTIONS Critical constants
--      (schema_remove, enum_modify, enum_remove). Same drift pattern affects
--      schema_register's chk_object_type and chk_source enums.
-- ============================================================================

INSERT INTO gs_governance.schema_register
  (object_type, schema_name, object_name, column_name, constraint_name,
   issue_type, severity, description, recommended_action,
   status, owner,
   first_snapshot_id, last_snapshot_id,
   source)
VALUES
-- 1. Exporter blind spot
('schema', 'gs_enrichment', 'gs_enrichment', NULL, NULL,
 'exporter_scan_gap', 'warning',
 'gs_dict_export.py SCHEMAS_TO_INTROSPECT misses gs_enrichment. Schema exists since GS-MIGRATE-021 (2026-04-20) with 5 tables (queue, policy, run_log, object_log, step_log) + 6 functions. Snapshot 20260424_150949 omits the entire schema from dictionary, taxonomy, security report, and health checks.',
 'Add ''gs_enrichment'' to SCHEMAS list in gs_dict_export.py, bump VERSION to 1.2 (GS-SOP-008). Re-run snapshot. Re-upload generated files to Project Knowledge.',
 'open', 'Chris',
 '20260424_150949', '20260424_150949',
 'manual'),

-- 2. deal_registry: ordinal gap at #18 (dropped column)
('table', 'public', 'deal_registry', NULL, NULL,
 'dropped_column_gap', 'info',
 'Column ordinal sequence has a gap at position 18, suggesting a historically dropped column. Not a functional bug — Postgres preserves attnum after DROP COLUMN — but documents schema evolution that is otherwise invisible.',
 'Document via comment OR rebuild table to compact ordinals. Decide based on whether the gap surfaces in any client tooling.',
 'open', 'Chris',
 '20260424_150949', '20260424_150949',
 'manual'),

-- 3. deal_registry: duplicate FK on sector_l1_code
('constraint', 'public', 'deal_registry', 'sector_l1_code', NULL,
 'duplicate_fk', 'warning',
 'Two FK constraints exist on column sector_l1_code, both pointing at the same parent. Redundant — one should be dropped. Pull exact constraint names from pg_catalog before dropping.',
 'Identify both constraint names via pg_constraint, drop the older one, verify integrity post-drop. Migration follows GS-SOP-003 pattern (migration + verify + rollback).',
 'open', 'Chris',
 '20260424_150949', '20260424_150949',
 'manual'),

-- 4. deal_registry: comment drift on FK
('table', 'public', 'deal_registry', 'canonical_deal_id', NULL,
 'comment_drift', 'info',
 'Table comment describes the FK to canonical_deals using parent column name "canonical_id" instead of the actual local column name "canonical_deal_id". Confusing for new readers reasoning from the comment.',
 'Update table comment to reference actual column name (GS-SOP-002 comment-only update).',
 'open', 'Chris',
 '20260424_150949', '20260424_150949',
 'manual'),

-- 5. changelog enum doc-drift
('constraint', 'public', 'changelog', 'change_type', 'changelog_change_type_check',
 'doc_drift_enum', 'info',
 'Live CHECK constraint on changelog.change_type permits values not listed in PROJECT_INSTRUCTIONS Critical constants table: schema_remove, enum_modify, enum_remove. Same drift pattern likely affects gs_governance.schema_register chk_object_type and chk_source enums (both used today by Claude with values that turned out to be invalid — e.g. tooling for object_type, live_check_* for source).',
 'Update PROJECT_INSTRUCTIONS Critical constants section to reflect live CHECK values for: changelog.change_type, schema_register.object_type, schema_register.source. Bump PROJECT_INSTRUCTIONS to v2.2 per "How this evolves".',
 'open', 'Chris',
 '20260424_150949', '20260424_150949',
 'manual');

-- ============================================================================
-- VERIFY
-- ============================================================================
SELECT issue_type, schema_name, object_name, severity, status
FROM gs_governance.schema_register
WHERE first_snapshot_id = '20260424_150949'
  AND source = 'manual'
ORDER BY severity DESC, issue_type;
-- Expected: 5 rows

