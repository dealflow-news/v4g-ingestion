-- ============================================================================
-- W8-EXT-002a · Fix swapped MAR_Dictionary subsections for 9086/9087
-- ============================================================================
-- Diagnosis (confirmed via Lens Motor Garage data, ~80-employee company):
--
--   pcmn_code 9086 = "Aantal werknemers" (employee count snapshot, integer)
--     - Tribel:    14    employees
--     - Lens Motor: 80    employees
--     - Magnitude matches headcount, NOT euros.
--     - MAR_Dictionary label 'Total Staff Costs' is misleading -- this is
--       a count of people, not a currency amount.
--
--   pcmn_code 9087 = "Gemiddelde FTE" (average FTE during the year, decimal)
--     - Tribel:    14.5   FTE
--     - Lens Motor: 82.2   FTE
--     - Decimal precision matters (part-time mix).
--
--   pcmn_code 9088 = "Totaal gepresteerde uren" (total hours, integer)
--     - already correct as 'Count' subsection
--
-- The MAR_Dictionary v1 spreadsheet has the 'Count' vs 'Headcount' labels
-- SWAPPED between 9086 and 9087. The data in fact_financials_lines is
-- correct -- only the dim_pcmn_codes.subsection metadata needs fixing.
--
-- analyst_export.py rendering rules:
--   subsection 'Count'     -> raw value, integer format (#,##0)
--   subsection 'Headcount' -> raw value, decimal format (#,##0.0)
--   subsection (other)     -> divide by 1000, decimal format (EUR k)
--
-- After this fix:
--   9086 -> Count     -> displays as integer:    80
--   9087 -> Headcount -> displays as decimal:   82.2
--   9088 -> Count     -> displays as integer: 124,457   (unchanged)
--
-- Logged in changelog as 'ref_data' (reference data correction, not schema).
-- ============================================================================

BEGIN;

-- Swap the subsections (each row goes to its semantically-correct bucket)
UPDATE dim_pcmn_codes SET subsection = 'Count'     WHERE pcmn_code = '9086';
UPDATE dim_pcmn_codes SET subsection = 'Headcount' WHERE pcmn_code = '9087';

-- OPTIONAL but recommended: correct the misleading label on 9086.
-- Uncomment if you agree -- otherwise leave the (wrong) MAR label as-is.
-- UPDATE dim_pcmn_codes
-- SET label_en = 'Average Number of Employees (headcount)'
-- WHERE pcmn_code = '9086';

INSERT INTO changelog (
    version, change_type, description, affected_tables, breaking, migration_plan
)
SELECT
    'W8-EXT-002a',
    'ref_data',
    'Swap dim_pcmn_codes.subsection for WORKERS codes 9086 (Count) and 9087 '
    '(Headcount). MAR_Dictionary v1 had these reversed; data in '
    'fact_financials_lines is correct (verified against Lens Motor Garage '
    '9086=80 employees, 9087=82.2 FTE). Affects analyst export rendering '
    'only: 9086 now renders as integer headcount, 9087 as decimal FTE.',
    ARRAY['dim_pcmn_codes'],
    FALSE,
    'Idempotent UPDATE; safe to re-run. Future MAR_Dictionary versions '
    'should fix rows 77/78 (Subsection column) upstream so that the next '
    'full populate does not reintroduce the swap.'
WHERE NOT EXISTS (
    SELECT 1 FROM changelog WHERE version = 'W8-EXT-002a'
);

COMMIT;

-- Verification (run after COMMIT)
SELECT pcmn_code, label_en, subsection, v4g_priority, display_order
FROM dim_pcmn_codes
WHERE pcmn_code IN ('9086', '9087', '9088', '1023', '1024')
ORDER BY display_order;

-- Expected:
--   9087  Average FTE (Full-Time Equivalents)        Headcount  HIGH    76
--   9086  Total Staff Costs *                        Count      HIGH    77
--   9088  Total Hours Worked (annual)                Count      MEDIUM  78
--   1023  Full-Time Wages (component of code 62)     EUR        LOW     79
--   1024  Part-Time Wages (component of code 62)     EUR        LOW     80
--
-- * Label is misleading; the actual semantic is "Average Number of Employees".
--   See optional UPDATE above to correct.
