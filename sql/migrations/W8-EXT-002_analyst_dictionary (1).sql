-- ============================================================================
-- W8-EXT-002 (final) · Analyst Dictionary Migration
-- ============================================================================
-- IMPORTANT: Run this ENTIRE file as a single SQL editor execution.
-- Select all (Ctrl+A) then run. Do not highlight subsections.
--
-- Schema changes:
--   1. Add 3 new columns to dim_pcmn_codes: label_en, subsection, display_order
--      (reuses existing v4g_priority for HIGH/MEDIUM/LOW display density)
--   2. Rename section values: PL -> IS, PROFIT_APPR -> IS_APPR
--      (drop dim_pcmn_codes_section_chk, do UPDATEs, re-add CHECK)
--   3. Populate the new columns + v4g_priority for 80 codes from MAR_Dictionary
--   4. Log to changelog
--   5. Verification queries
--
-- Canonical section set (locked as doctrine):
--   BS_A, BS_L, IS, IS_APPR, WORKERS, NOTES
--
-- Rollback: see end of file (commented out).
-- ============================================================================

BEGIN;

-- 1. Add 3 new columns (v4g_priority already exists; do not duplicate)
ALTER TABLE dim_pcmn_codes
    ADD COLUMN IF NOT EXISTS label_en      TEXT,
    ADD COLUMN IF NOT EXISTS subsection    TEXT,
    ADD COLUMN IF NOT EXISTS display_order INTEGER;

COMMENT ON COLUMN dim_pcmn_codes.label_en IS
    'English display label for analyst exports (source: MAR_Dictionary v1).';
COMMENT ON COLUMN dim_pcmn_codes.subsection IS
    'Within-section grouping: Total / Fixed / Current / Equity / LT Debt / ST Debt / '
    'Revenue / OpEx / Finance / Tax / KPI / Appropriation / Count / Headcount / EUR.';
COMMENT ON COLUMN dim_pcmn_codes.display_order IS
    'Row position within section in analyst export layout (NULL = excluded).';
COMMENT ON COLUMN dim_pcmn_codes.v4g_priority IS
    'Display density for analyst exports: HIGH = bold, MEDIUM = normal, LOW = grey. '
    'Rendering hint only - distinct from data-quality or M&A-significance flags.';

-- 2. Section rename
ALTER TABLE dim_pcmn_codes DROP CONSTRAINT IF EXISTS dim_pcmn_codes_section_chk;

UPDATE dim_pcmn_codes SET section = 'IS'      WHERE section = 'PL';
UPDATE dim_pcmn_codes SET section = 'IS_APPR' WHERE section = 'PROFIT_APPR';

ALTER TABLE dim_pcmn_codes
    ADD CONSTRAINT dim_pcmn_codes_section_chk
    CHECK (section = ANY (ARRAY[
        'BS_A'::text, 'BS_L'::text, 'IS'::text, 'IS_APPR'::text,
        'WORKERS'::text, 'NOTES'::text
    ]));

-- 3. Populate from MAR_Dictionary (80 codes)
-- MAR uses 'MED' -> remapped to 'MEDIUM' below to satisfy dim_pcmn_codes_priority_chk
UPDATE dim_pcmn_codes AS t
SET
    label_en      = src.label_en,
    subsection    = src.subsection,
    v4g_priority  = src.v4g_priority,
    display_order = src.display_order,
    section       = src.section
FROM (VALUES
  ('20/58'     , 'BS_A'    , 'TOTAL ASSETS'                                    , 'Total'          , 'HIGH'    ,   1),
  ('20/28'     , 'BS_A'    , 'Fixed Assets'                                    , 'Fixed'          , 'HIGH'    ,   2),
  ('21'        , 'BS_A'    , 'Intangible Fixed Assets'                         , 'Fixed'          , 'MEDIUM'  ,   3),
  ('22/27'     , 'BS_A'    , 'Tangible Fixed Assets'                           , 'Fixed'          , 'HIGH'    ,   4),
  ('22'        , 'BS_A'    , 'Land & Buildings'                                , 'Fixed'          , 'MEDIUM'  ,   5),
  ('23'        , 'BS_A'    , 'Plant, Machinery & Equipment'                    , 'Fixed'          , 'MEDIUM'  ,   6),
  ('24'        , 'BS_A'    , 'Furniture & Vehicles'                            , 'Fixed'          , 'MEDIUM'  ,   7),
  ('26'        , 'BS_A'    , 'Leasing & Similar Rights'                        , 'Fixed'          , 'LOW'     ,   8),
  ('27'        , 'BS_A'    , 'Other Tangible Fixed Assets'                     , 'Fixed'          , 'LOW'     ,   9),
  ('28'        , 'BS_A'    , 'Financial Fixed Assets'                          , 'Fixed'          , 'MEDIUM'  ,  10),
  ('280/1'     , 'BS_A'    , 'Investments in Subsidiaries'                     , 'Fixed'          , 'LOW'     ,  11),
  ('284/5'     , 'BS_A'    , 'Investments in Other Companies'                  , 'Fixed'          , 'LOW'     ,  12),
  ('29/58'     , 'BS_A'    , 'Current Assets'                                  , 'Current'        , 'HIGH'    ,  13),
  ('3'         , 'BS_A'    , 'Inventories & Work in Progress'                  , 'Current'        , 'HIGH'    ,  14),
  ('30/36'     , 'BS_A'    , 'Inventories'                                     , 'Current'        , 'MEDIUM'  ,  15),
  ('37'        , 'BS_A'    , 'Work in Progress'                                , 'Current'        , 'MEDIUM'  ,  16),
  ('40/41'     , 'BS_A'    , 'Trade & Other Receivables (≤1yr)'                , 'Current'        , 'HIGH'    ,  17),
  ('40'        , 'BS_A'    , 'Trade Receivables'                               , 'Current'        , 'HIGH'    ,  18),
  ('41'        , 'BS_A'    , 'Other Receivables (≤1yr)'                        , 'Current'        , 'MEDIUM'  ,  19),
  ('50/53'     , 'BS_A'    , 'Cash & Cash Equivalents'                         , 'Current'        , 'HIGH'    ,  20),
  ('490/1'     , 'BS_A'    , 'Deferred Charges & Accrued Income'               , 'Current'        , 'LOW'     ,  21),
  ('10/49'     , 'BS_L'    , 'TOTAL LIABILITIES & EQUITY'                      , 'Total'          , 'HIGH'    ,  22),
  ('10/15'     , 'BS_L'    , 'Equity'                                          , 'Equity'         , 'HIGH'    ,  23),
  ('10'        , 'BS_L'    , 'Share Capital'                                   , 'Equity'         , 'MEDIUM'  ,  24),
  ('100'       , 'BS_L'    , 'Issued Capital'                                  , 'Equity'         , 'LOW'     ,  25),
  ('101'       , 'BS_L'    , 'Uncalled Capital (–)'                            , 'Equity'         , 'LOW'     ,  26),
  ('11'        , 'BS_L'    , 'Share Premium'                                   , 'Equity'         , 'LOW'     ,  27),
  ('12'        , 'BS_L'    , 'Revaluation Surplus'                             , 'Equity'         , 'LOW'     ,  28),
  ('13'        , 'BS_L'    , 'Reserves'                                        , 'Equity'         , 'MEDIUM'  ,  29),
  ('130'       , 'BS_L'    , 'Legal Reserve'                                   , 'Equity'         , 'LOW'     ,  30),
  ('132'       , 'BS_L'    , 'Unavailable Reserves'                            , 'Equity'         , 'LOW'     ,  31),
  ('133'       , 'BS_L'    , 'Tax-exempt Reserves'                             , 'Equity'         , 'LOW'     ,  32),
  ('134'       , 'BS_L'    , 'Available Reserves'                              , 'Equity'         , 'LOW'     ,  33),
  ('14'        , 'BS_L'    , 'Retained Earnings (Loss)'                        , 'Equity'         , 'HIGH'    ,  34),
  ('15'        , 'BS_L'    , 'Capital Subsidies'                               , 'Equity'         , 'LOW'     ,  35),
  ('16'        , 'BS_L'    , 'Provisions & Deferred Taxes'                     , 'LT Debt'        , 'MEDIUM'  ,  36),
  ('17'        , 'BS_L'    , 'Long-term Debts (>1yr)'                          , 'LT Debt'        , 'HIGH'    ,  37),
  ('170/4'     , 'BS_L'    , 'Financial Debts >1yr'                            , 'LT Debt'        , 'HIGH'    ,  38),
  ('175'       , 'BS_L'    , 'Trade Debts >1yr'                                , 'LT Debt'        , 'LOW'     ,  39),
  ('178/9'     , 'BS_L'    , 'Other Debts >1yr'                                , 'LT Debt'        , 'LOW'     ,  40),
  ('42/48'     , 'BS_L'    , 'Current Liabilities (≤1yr)'                      , 'ST Debt'        , 'HIGH'    ,  41),
  ('42/43'     , 'BS_L'    , 'Financial Debts ≤1yr'                            , 'ST Debt'        , 'HIGH'    ,  42),
  ('44'        , 'BS_L'    , 'Trade Payables ≤1yr'                             , 'ST Debt'        , 'HIGH'    ,  43),
  ('440/4'     , 'BS_L'    , 'Suppliers'                                       , 'ST Debt'        , 'MEDIUM'  ,  44),
  ('441'       , 'BS_L'    , 'Bills of Exchange Payable'                       , 'ST Debt'        , 'LOW'     ,  45),
  ('45'        , 'BS_L'    , 'Tax & Social Security Payables'                  , 'ST Debt'        , 'MEDIUM'  ,  46),
  ('46'        , 'BS_L'    , 'Advances Received'                               , 'ST Debt'        , 'LOW'     ,  47),
  ('47/48'     , 'BS_L'    , 'Other Current Liabilities'                       , 'ST Debt'        , 'MEDIUM'  ,  48),
  ('492/3'     , 'BS_L'    , 'Accrued Charges & Deferred Income'               , 'ST Debt'        , 'LOW'     ,  49),
  ('70/76A'    , 'IS'      , 'TOTAL OPERATING INCOME'                          , 'Revenue'        , 'HIGH'    ,  50),
  ('70'        , 'IS'      , 'Revenue (Turnover)'                              , 'Revenue'        , 'HIGH'    ,  51),
  ('71'        , 'IS'      , 'Changes in Inventories & WIP'                    , 'Revenue'        , 'MEDIUM'  ,  52),
  ('72'        , 'IS'      , 'Own Construction Capitalised'                    , 'Revenue'        , 'LOW'     ,  53),
  ('74'        , 'IS'      , 'Other Operating Income'                          , 'Revenue'        , 'MEDIUM'  ,  54),
  ('75'        , 'IS'      , 'Financial Income'                                , 'Finance'        , 'MEDIUM'  ,  55),
  ('60/66A'    , 'IS'      , 'TOTAL OPERATING EXPENSES'                        , 'OpEx'           , 'HIGH'    ,  56),
  ('60/61'     , 'IS'      , 'Purchases of Goods & Services'                   , 'OpEx'           , 'MEDIUM'  ,  57),
  ('60'        , 'IS'      , 'Purchases of Raw Materials & Goods'              , 'OpEx'           , 'HIGH'    ,  58),
  ('61'        , 'IS'      , 'Services & Other Goods'                          , 'OpEx'           , 'HIGH'    ,  59),
  ('62'        , 'IS'      , 'Salaries, Social Charges & Pensions'             , 'OpEx'           , 'HIGH'    ,  60),
  ('630'       , 'IS'      , 'Depreciation & Amortisation'                     , 'OpEx'           , 'HIGH'    ,  61),
  ('631/4'     , 'IS'      , 'Impairment of Current Assets'                    , 'OpEx'           , 'MEDIUM'  ,  62),
  ('635/8'     , 'IS'      , 'Provisions for Risks & Charges'                  , 'OpEx'           , 'LOW'     ,  63),
  ('640/8'     , 'IS'      , 'Other Operating Expenses'                        , 'OpEx'           , 'MEDIUM'  ,  64),
  ('65'        , 'IS'      , 'Financial Expenses'                              , 'Finance'        , 'MEDIUM'  ,  65),
  ('67'        , 'IS'      , 'Income Taxes'                                    , 'Tax'            , 'HIGH'    ,  66),
  ('9901'      , 'IS'      , 'Operating Result (EBIT)'                         , 'KPI'            , 'HIGH'    ,  67),
  ('9903'      , 'IS'      , 'Profit (Loss) Before Tax (EBT)'                  , 'KPI'            , 'HIGH'    ,  68),
  ('9904'      , 'IS'      , 'Net Profit (Loss) for the Period'                , 'KPI'            , 'HIGH'    ,  69),
  ('9905'      , 'IS'      , 'Profit (Loss) Available for Appropriation'       , 'KPI'            , 'HIGH'    ,  70),
  ('690'       , 'IS_APPR' , 'Profit (Loss) Carried Forward'                   , 'Appropriation'  , 'LOW'     ,  71),
  ('691'       , 'IS_APPR' , 'Dividends Paid'                                  , 'Appropriation'  , 'MEDIUM'  ,  72),
  ('694'       , 'IS_APPR' , 'Transfer to Legal Reserve'                       , 'Appropriation'  , 'LOW'     ,  73),
  ('694/6'     , 'IS_APPR' , 'Transfer to Reserves'                            , 'Appropriation'  , 'LOW'     ,  74),
  ('696'       , 'IS_APPR' , 'Other Reserves'                                  , 'Appropriation'  , 'LOW'     ,  75),
  ('9087'      , 'WORKERS' , 'Average FTE (Full-Time Equivalents)'             , 'Count'          , 'HIGH'    ,  76),
  ('9086'      , 'WORKERS' , 'Total Staff Costs'                               , 'Headcount'      , 'HIGH'    ,  77),
  ('9088'      , 'WORKERS' , 'Total Hours Worked (annual)'                     , 'Count'          , 'MEDIUM'  ,  78),
  ('1023'      , 'WORKERS' , 'Full-Time Wages (component of code 62)'          , 'EUR'            , 'LOW'     ,  79),
  ('1024'      , 'WORKERS' , 'Part-Time Wages (component of code 62)'          , 'EUR'            , 'LOW'     ,  80)
) AS src(pcmn_code, section, label_en, subsection, v4g_priority, display_order)
WHERE t.pcmn_code = src.pcmn_code;

-- 4. Log to changelog
INSERT INTO changelog (
    version, change_type, description, affected_tables, breaking, migration_plan
)
SELECT
    'W8-EXT-002',
    'schema_modify',
    'Analyst dictionary metadata: added label_en, subsection, display_order; '
    'reused existing v4g_priority (HIGH/MEDIUM/LOW). Renamed section PL->IS '
    'and PROFIT_APPR->IS_APPR; updated dim_pcmn_codes_section_chk. Populated '
    '80 codes from MAR_Dictionary v1. Canonical section set locked: '
    'BS_A, BS_L, IS, IS_APPR, WORKERS, NOTES.',
    ARRAY['dim_pcmn_codes'],
    FALSE,
    'See migrations/W8-EXT-002_analyst_dictionary.sql. Idempotent: '
    'ADD COLUMN IF NOT EXISTS + DROP CONSTRAINT IF EXISTS + UPDATE WHERE. '
    'Rollback section included at the end of the migration file.'
WHERE NOT EXISTS (
    SELECT 1 FROM changelog WHERE version = 'W8-EXT-002'
);

COMMIT;

-- ============================================================================
-- Verification (run after COMMIT - these are SELECTs, no side effects)
-- ============================================================================

-- 5a. Distribution per section (expect 5 sections with label_en, plus NOTES)
SELECT section,
       COUNT(*) AS n,
       COUNT(label_en) FILTER (WHERE label_en IS NOT NULL) AS with_label,
       COUNT(*) FILTER (WHERE v4g_priority = 'HIGH')   AS pri_high,
       COUNT(*) FILTER (WHERE v4g_priority = 'MEDIUM') AS pri_medium,
       COUNT(*) FILTER (WHERE v4g_priority = 'LOW')    AS pri_low
FROM dim_pcmn_codes
GROUP BY section
ORDER BY section;

-- 5b. Zero stale section values (PL or PROFIT_APPR should NOT appear)
SELECT pcmn_code, section
FROM dim_pcmn_codes
WHERE section IN ('PL', 'PROFIT_APPR');

-- 5c. Section CHECK constraint in place
SELECT check_clause
FROM information_schema.check_constraints
WHERE constraint_name = 'dim_pcmn_codes_section_chk';

-- 5d. Changelog entry
SELECT id, version, change_type, change_date, author
FROM changelog
WHERE version = 'W8-EXT-002';

-- ============================================================================
-- ROLLBACK (run manually if needed)
-- ============================================================================
-- BEGIN;
--   ALTER TABLE dim_pcmn_codes DROP CONSTRAINT IF EXISTS dim_pcmn_codes_section_chk;
--   UPDATE dim_pcmn_codes SET section = 'PL'          WHERE section = 'IS';
--   UPDATE dim_pcmn_codes SET section = 'PROFIT_APPR' WHERE section = 'IS_APPR';
--   ALTER TABLE dim_pcmn_codes ADD CONSTRAINT dim_pcmn_codes_section_chk
--     CHECK (section = ANY (ARRAY['PL'::text,'BS_A'::text,'BS_L'::text,
--                                  'WORKERS'::text,'PROFIT_APPR'::text,'NOTES'::text]));
--   ALTER TABLE dim_pcmn_codes
--     DROP COLUMN IF EXISTS display_order,
--     DROP COLUMN IF EXISTS subsection,
--     DROP COLUMN IF EXISTS label_en;
--   DELETE FROM changelog WHERE version = 'W8-EXT-002';
-- COMMIT;
