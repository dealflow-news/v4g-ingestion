-- ============================================================================
-- W8-EXT-002c: Fix dim_pcmn_codes.label_en for code 9086
-- ============================================================================
-- Context: MAR_Dictionary v1 had codes 9086 and 9087 with swapped semantics.
-- W8-EXT-002a fixed the *subsection* mapping so values render with the
-- right unit (integer headcount vs decimal FTE). But the label_en for code
-- 9086 remained "Total Staff Costs" from MAR_Dictionary v1.
--
-- This is misleading in analyst exports: code 9086 shows headcount values
-- (e.g. Lens Motor: 80 employees in 2024), but the label suggests EUR
-- amounts. "Total Staff Costs" already exists at PCMN code 62 (Salaries,
-- Social Charges & Pensions).
--
-- Belgian NBB code 9086 semantics in our DB (verified via Lens Motor data):
--   9086 = Average Number of Employees (integer headcount)
--   9087 = Average FTE (Full-Time Equivalents, decimal)
--   9088 = Total Hours Worked (annual, integer)
--
-- This migration:
-- 1. Updates label_en for 9086 to "Average Number of Employees"
-- 2. Logs in changelog as ref_data change
--
-- Idempotent: the WHERE clause guards against re-application after success.
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- PART 1: MIGRATION
-- ----------------------------------------------------------------------------

UPDATE dim_pcmn_codes
SET label_en = 'Average Number of Employees'
WHERE pcmn_code = '9086'
  AND label_en  = 'Total Staff Costs';  -- defensive: only update if still wrong

-- Log
INSERT INTO changelog (version, change_type, description, affected_tables, breaking)
VALUES (
    'W8-EXT-002c',
    'ref_data',
    'Fix dim_pcmn_codes.label_en for code 9086 from "Total Staff Costs" to '
    '"Average Number of Employees". MAR_Dictionary v1 had this code mislabeled; '
    'the values stored under 9086 are integer headcount (e.g. Lens Motor 80 '
    'employees), not EUR amounts. True total staff costs are at PCMN code 62. '
    'W8-EXT-002a fixed the subsection mapping (unit rendering); this completes '
    'the label fix surfaced by analyst export review.',
    ARRAY['dim_pcmn_codes'],
    false
);

-- ----------------------------------------------------------------------------
-- PART 2: VERIFY
-- ----------------------------------------------------------------------------

-- Expected: 9086 = "Average Number of Employees", subsection=Count (integer)
--           9087 = "Average FTE..." subsection=Headcount (decimal)
--           9088 = "Total Hours..." subsection=Count (integer)
SELECT pcmn_code, label_en, subsection, v4g_priority, display_order, section
FROM dim_pcmn_codes
WHERE pcmn_code IN ('9086', '9087', '9088', '1023', '1024', '62')
ORDER BY section, display_order, pcmn_code;

-- Expected: 1 changelog entry for W8-EXT-002c
SELECT id, version, change_type, change_date
FROM changelog
WHERE version = 'W8-EXT-002c';

COMMIT;

-- ----------------------------------------------------------------------------
-- PART 3: ROLLBACK (commented out, run manually if needed)
-- ----------------------------------------------------------------------------
-- BEGIN;
--   UPDATE dim_pcmn_codes
--   SET label_en = 'Total Staff Costs'
--   WHERE pcmn_code = '9086' AND label_en = 'Average Number of Employees';
--   DELETE FROM changelog WHERE version = 'W8-EXT-002c';
-- COMMIT;
