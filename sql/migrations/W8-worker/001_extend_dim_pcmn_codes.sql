-- ============================================================================
-- Migration W8-worker-001: extend dim_pcmn_codes from 24 → 80 codes
-- ============================================================================
-- Version: v1.0 · 2026-05-11
-- Branch:  feature/W8-worker
-- Depends: W8-core deployed (dim_pcmn_codes table with 24 codes)
-- Risk:    LOW — pure additive seed extension, no schema change
--
-- Source data: merged from
--   - BAS_MAP (taxonomy.py) — Dutch labels + xbrl_element bas:mXX references
--   - MAR_DICT (mar_dictionary.py) — English labels + v4g_priority
--
-- Section mapping (taxonomy → DB CHECK):
--   IS    → PL
--   IS_X  → PROFIT_APPR
--   BS_A, BS_L, WORKERS, NOTES unchanged
--
-- Priority mapping (MAR → DB CHECK):
--   MED → MEDIUM (HIGH, LOW unchanged)
-- ============================================================================

BEGIN;

-- PART 1: MIGRATION (UPSERT all 80 codes)
INSERT INTO public.dim_pcmn_codes
    (pcmn_code, section, description_nl, description_en, xbrl_element, v4g_priority, notes)
VALUES
    ('3', 'BS_A', 'Voorraden en bestellingen in uitvoering', 'Inventories & Work in Progress', 'bas:m13', 'HIGH', 'subsection: Current'),
    ('20/28', 'BS_A', 'VASTE ACTIVA', 'Fixed Assets', 'bas:m1', 'HIGH', 'subsection: Fixed'),
    ('20/58', 'BS_A', 'TOTAAL DER ACTIVA', 'TOTAL ASSETS', 'bas:m21', 'HIGH', 'subsection: Total'),
    ('21', 'BS_A', 'Immateriële vaste activa', 'Intangible Fixed Assets', 'bas:m8', 'MEDIUM', 'subsection: Fixed'),
    ('22', 'BS_A', 'Terreinen en gebouwen', 'Land & Buildings', 'bas:m3', 'MEDIUM', 'subsection: Fixed'),
    ('22/27', 'BS_A', 'Materiële vaste activa', 'Tangible Fixed Assets', 'bas:m2', 'HIGH', 'subsection: Fixed'),
    ('23', 'BS_A', 'Installaties, machines en uitrusting', 'Plant, Machinery & Equipment', 'bas:m4', 'MEDIUM', 'subsection: Fixed'),
    ('24', 'BS_A', 'Meubilair en rollend materieel', 'Furniture & Vehicles', 'bas:m5', 'MEDIUM', 'subsection: Fixed'),
    ('26', 'BS_A', 'Leasing en soortgelijke rechten', 'Leasing & Similar Rights', 'bas:m6', 'LOW', 'subsection: Fixed'),
    ('27', 'BS_A', 'Overige materiële vaste activa', 'Other Tangible Fixed Assets', 'bas:m7', 'LOW', 'subsection: Fixed'),
    ('28', 'BS_A', 'Financiële vaste activa', 'Financial Fixed Assets', 'bas:m9', 'MEDIUM', 'subsection: Fixed'),
    ('29/58', 'BS_A', 'VLOTTENDE ACTIVA', 'Current Assets', 'bas:m12', 'HIGH', 'subsection: Current'),
    ('30/36', 'BS_A', 'Voorraden', 'Inventories', 'bas:m14', 'MEDIUM', 'subsection: Current'),
    ('37', 'BS_A', 'Bestellingen in uitvoering', 'Work in Progress', 'bas:m15', 'MEDIUM', 'subsection: Current'),
    ('40', 'BS_A', 'Handelsvorderingen', 'Trade Receivables', 'bas:m17', 'HIGH', 'subsection: Current'),
    ('40/41', 'BS_A', 'Vorderingen op ten hoogste één jaar', 'Trade & Other Receivables (≤1yr)', 'bas:m16', 'HIGH', 'subsection: Current'),
    ('41', 'BS_A', 'Overige vorderingen', 'Other Receivables (≤1yr)', 'bas:m18', 'MEDIUM', 'subsection: Current'),
    ('50/53', 'BS_A', 'Liquide middelen', 'Cash & Cash Equivalents', 'bas:m19', 'HIGH', 'subsection: Current'),
    ('280/1', 'BS_A', 'Verbonden ondernemingen — deelnemingen', 'Investments in Subsidiaries', 'bas:m10', 'LOW', 'subsection: Fixed'),
    ('284/5', 'BS_A', 'Andere ondernemingen — deelnemingen', 'Investments in Other Companies', 'bas:m11', 'LOW', 'subsection: Fixed'),
    ('490/1', 'BS_A', 'Overlopende rekeningen (activa)', 'Deferred Charges & Accrued Income', 'bas:m20', 'LOW', 'subsection: Current'),
    ('10', 'BS_L', 'Kapitaal', 'Share Capital', 'bas:m24', 'MEDIUM', 'subsection: Equity'),
    ('10/15', 'BS_L', 'EIGEN VERMOGEN', 'Equity', 'bas:m23', 'HIGH', 'subsection: Equity'),
    ('10/49', 'BS_L', 'TOTAAL DER PASSIVA', 'TOTAL LIABILITIES & EQUITY', 'bas:m61', 'HIGH', 'subsection: Total'),
    ('11', 'BS_L', 'Uitgiftepremies', 'Share Premium', 'bas:m27', 'LOW', 'subsection: Equity'),
    ('12', 'BS_L', 'Herwaarderingsmeerwaarden', 'Revaluation Surplus', 'bas:m28', 'LOW', 'subsection: Equity'),
    ('13', 'BS_L', 'Reserves', 'Reserves', 'bas:m29', 'MEDIUM', 'subsection: Equity'),
    ('14', 'BS_L', 'Overgedragen winst (verlies)', 'Retained Earnings (Loss)', 'bas:m37', 'HIGH', 'subsection: Equity'),
    ('15', 'BS_L', 'Kapitaalsubsidies', 'Capital Subsidies', 'bas:m38', 'LOW', 'subsection: Equity'),
    ('16', 'BS_L', 'VOORZIENINGEN EN UITGESTELDE BELASTINGEN', 'Provisions & Deferred Taxes', 'bas:m39', 'MEDIUM', 'subsection: LT Debt'),
    ('17', 'BS_L', 'Schulden op meer dan één jaar', 'Long-term Debts (>1yr)', 'bas:m43', 'HIGH', 'subsection: LT Debt'),
    ('42/43', 'BS_L', 'Financiële schulden ≤ 1 jaar', 'Financial Debts ≤1yr', 'bas:m53', 'HIGH', 'subsection: ST Debt'),
    ('42/48', 'BS_L', 'Schulden op ten hoogste één jaar', 'Current Liabilities (≤1yr)', 'bas:m52', 'HIGH', 'subsection: ST Debt'),
    ('44', 'BS_L', 'Handelsschulden ≤ 1 jaar', 'Trade Payables ≤1yr', 'bas:m54', 'HIGH', 'subsection: ST Debt'),
    ('45', 'BS_L', 'Schulden tov belasting/sociale lasten', 'Tax & Social Security Payables', 'bas:m57', 'MEDIUM', 'subsection: ST Debt'),
    ('46', 'BS_L', 'Ontvangen vooruitbetalingen', 'Advances Received', 'bas:m58', 'LOW', 'subsection: ST Debt'),
    ('47/48', 'BS_L', 'Overige schulden ≤ 1 jaar', 'Other Current Liabilities', 'bas:m59', 'MEDIUM', 'subsection: ST Debt'),
    ('100', 'BS_L', 'Geplaatst kapitaal', 'Issued Capital', 'bas:m25', 'LOW', 'subsection: Equity'),
    ('101', 'BS_L', 'Niet-opgevraagd kapitaal (–)', 'Uncalled Capital (–)', 'bas:m26', 'LOW', 'subsection: Equity'),
    ('130', 'BS_L', 'Wettelijke reserve', 'Legal Reserve', 'bas:m30', 'LOW', 'subsection: Equity'),
    ('132', 'BS_L', 'Onbeschikbare reserves', 'Unavailable Reserves', 'bas:m31', 'LOW', 'subsection: Equity'),
    ('133', 'BS_L', 'Belastingvrije reserves', 'Tax-exempt Reserves', 'bas:m32', 'LOW', 'subsection: Equity'),
    ('134', 'BS_L', 'Beschikbare reserves', 'Available Reserves', 'bas:m34', 'LOW', 'subsection: Equity'),
    ('170/4', 'BS_L', 'Financiële schulden > 1 jaar', 'Financial Debts >1yr', 'bas:m44', 'HIGH', 'subsection: LT Debt'),
    ('175', 'BS_L', 'Handelsschulden > 1 jaar', 'Trade Debts >1yr', 'bas:m49', 'LOW', 'subsection: LT Debt'),
    ('178/9', 'BS_L', 'Overige schulden > 1 jaar', 'Other Debts >1yr', 'bas:m50', 'LOW', 'subsection: LT Debt'),
    ('440/4', 'BS_L', 'Leveranciers', 'Suppliers', 'bas:m55', 'MEDIUM', 'subsection: ST Debt'),
    ('441', 'BS_L', 'Te betalen wissels', 'Bills of Exchange Payable', 'bas:m56', 'LOW', 'subsection: ST Debt'),
    ('492/3', 'BS_L', 'Overlopende rekeningen (passiva)', 'Accrued Charges & Deferred Income', 'bas:m60', 'LOW', 'subsection: ST Debt'),
    ('60', 'PL', 'Aankopen van grond- en hulpstoffen / hdl.', 'Purchases of Raw Materials & Goods', 'bas:m85', 'HIGH', 'subsection: OpEx'),
    ('60/61', 'PL', NULL, 'Purchases of Goods & Services', NULL, 'MEDIUM', 'subsection: OpEx'),
    ('60/66A', 'PL', 'BEDRIJFSKOSTEN', 'TOTAL OPERATING EXPENSES', 'bas:m79', 'HIGH', 'subsection: OpEx'),
    ('61', 'PL', 'Diensten en diverse goederen', 'Services & Other Goods', 'bas:m87', 'HIGH', 'subsection: OpEx'),
    ('62', 'PL', 'Bezoldigingen, sociale lasten en pensioenen', 'Salaries, Social Charges & Pensions', 'bas:m101', 'HIGH', 'subsection: OpEx'),
    ('65', 'PL', 'Financiële kosten', 'Financial Expenses', 'bas:m115', 'MEDIUM', 'subsection: Finance'),
    ('67', 'PL', 'Belastingen op het resultaat', 'Income Taxes', 'bas:m120', 'HIGH', 'subsection: Tax'),
    ('70', 'PL', 'Omzet', 'Revenue (Turnover)', 'bas:m70', 'HIGH', 'subsection: Revenue'),
    ('70/76A', 'PL', 'BEDRIJFSOPBRENGSTEN', 'TOTAL OPERATING INCOME', 'bas:m68', 'HIGH', 'subsection: Revenue'),
    ('71', 'PL', 'Wijziging in voorraden en bestellingen', 'Changes in Inventories & WIP', 'bas:m72', 'MEDIUM', 'subsection: Revenue'),
    ('72', 'PL', 'Geproduceerde vaste activa', 'Own Construction Capitalised', 'bas:m73', 'LOW', 'subsection: Revenue'),
    ('74', 'PL', 'Andere bedrijfsopbrengsten', 'Other Operating Income', 'bas:m77', 'MEDIUM', 'subsection: Revenue'),
    ('75', 'PL', 'Financiële opbrengsten', 'Financial Income', 'bas:m111', 'MEDIUM', 'subsection: Finance'),
    ('630', 'PL', 'Afschrijvingen en waardeverminderingen', 'Depreciation & Amortisation', 'bas:m103', 'HIGH', 'subsection: OpEx'),
    ('631/4', 'PL', 'Waardeverminderingen op vlottende activa', 'Impairment of Current Assets', 'bas:m104', 'MEDIUM', 'subsection: OpEx'),
    ('635/8', 'PL', 'Voorzieningen voor risico''s en kosten', 'Provisions for Risks & Charges', 'bas:m107', 'LOW', 'subsection: OpEx'),
    ('640/8', 'PL', 'Andere bedrijfskosten', 'Other Operating Expenses', 'bas:m109', 'MEDIUM', 'subsection: OpEx'),
    ('9901', 'PL', 'BEDRIJFSRESULTAAT', 'Operating Result (EBIT)', 'bas:m110', 'HIGH', 'subsection: KPI'),
    ('9903', 'PL', 'WINST (VERLIES) VÓÓR BELASTINGEN', 'Profit (Loss) Before Tax (EBT)', 'bas:m118', 'HIGH', 'subsection: KPI'),
    ('9904', 'PL', 'WINST (VERLIES) VAN HET BOEKJAAR', 'Net Profit (Loss) for the Period', 'bas:m122', 'HIGH', 'subsection: KPI'),
    ('9905', 'PL', 'TE VERDELEN WINST (VERLIES)', 'Profit (Loss) Available for Appropriation', 'bas:m124', 'HIGH', 'subsection: KPI'),
    ('690', 'PROFIT_APPR', 'Over te dragen winst / verlies', 'Profit (Loss) Carried Forward', 'bas:m133', 'LOW', 'subsection: Appropriation'),
    ('691', 'PROFIT_APPR', 'Uit te keren dividend', 'Dividends Paid', 'bas:m134', 'MEDIUM', 'subsection: Appropriation'),
    ('694', 'PROFIT_APPR', 'Wettelijke reserve', 'Transfer to Legal Reserve', 'bas:m131', 'LOW', 'subsection: Appropriation'),
    ('694/6', 'PROFIT_APPR', 'Toe te voegen aan reserves', 'Transfer to Reserves', 'bas:m130', 'LOW', 'subsection: Appropriation'),
    ('696', 'PROFIT_APPR', 'Andere reserves', 'Other Reserves', 'bas:m132', 'LOW', 'subsection: Appropriation'),
    ('1023', 'WORKERS', 'Voltijds', 'Full-Time Wages (component of code 62)', 'bas:m142', 'LOW', 'subsection: EUR'),
    ('1024', 'WORKERS', 'Deeltijds', 'Part-Time Wages (component of code 62)', 'bas:m143', 'LOW', 'subsection: EUR'),
    ('9086', 'WORKERS', 'Personeelskosten totaal', 'Total Staff Costs', 'bas:m144', 'HIGH', 'subsection: Headcount'),
    ('9087', 'WORKERS', 'Gemiddeld aantal werknemers (VTE)', 'Average FTE (Full-Time Equivalents)', 'bas:m140', 'HIGH', 'subsection: Count'),
    ('9088', 'WORKERS', 'Effectief aantal werknemers op afsluiting', 'Total Hours Worked (annual)', 'bas:m141', 'MEDIUM', 'subsection: Count')
ON CONFLICT (pcmn_code) DO UPDATE SET
    section        = EXCLUDED.section,
    description_nl = COALESCE(NULLIF(EXCLUDED.description_nl, ''), public.dim_pcmn_codes.description_nl),
    description_en = COALESCE(NULLIF(EXCLUDED.description_en, ''), public.dim_pcmn_codes.description_en),
    xbrl_element   = COALESCE(NULLIF(EXCLUDED.xbrl_element, ''), public.dim_pcmn_codes.xbrl_element),
    v4g_priority   = EXCLUDED.v4g_priority,
    notes          = COALESCE(NULLIF(EXCLUDED.notes, ''), public.dim_pcmn_codes.notes);

COMMIT;

-- PART 2: VERIFY
SELECT
    'expected_total' AS check_name,
    80::int AS expected,
    (SELECT count(*) FROM public.dim_pcmn_codes)::int AS actual,
    CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes) = 80 THEN '✓' ELSE '✗ FAIL' END AS status
UNION ALL
SELECT 'BS_A_count',     21, (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='BS_A')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='BS_A') = 21 THEN '✓' ELSE '✗' END
UNION ALL
SELECT 'BS_L_count',     28, (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='BS_L')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='BS_L') = 28 THEN '✓' ELSE '✗' END
UNION ALL
SELECT 'PL_count',       21, (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='PL')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='PL') = 21 THEN '✓' ELSE '✗' END
UNION ALL
SELECT 'PROFIT_APPR_count', 5, (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='PROFIT_APPR')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='PROFIT_APPR') = 5 THEN '✓' ELSE '✗' END
UNION ALL
SELECT 'WORKERS_count',  5, (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='WORKERS')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE section='WORKERS') = 5 THEN '✓' ELSE '✗' END
UNION ALL
SELECT 'HIGH_priority',  30, (SELECT count(*) FROM public.dim_pcmn_codes WHERE v4g_priority='HIGH')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE v4g_priority='HIGH') = 30 THEN '✓' ELSE '✗' END
UNION ALL
SELECT 'MEDIUM_priority', 23, (SELECT count(*) FROM public.dim_pcmn_codes WHERE v4g_priority='MEDIUM')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE v4g_priority='MEDIUM') = 23 THEN '✓' ELSE '✗' END
UNION ALL
SELECT 'LOW_priority',   27, (SELECT count(*) FROM public.dim_pcmn_codes WHERE v4g_priority='LOW')::int,
       CASE WHEN (SELECT count(*) FROM public.dim_pcmn_codes WHERE v4g_priority='LOW') = 27 THEN '✓' ELSE '✗' END;

-- Expected output: 9 rows, all status='✓'

-- Governance entry
INSERT INTO gs_governance.schema_register (
    object_type, schema_name, object_name, issue_type, severity, description, status, owner, source,
    first_snapshot_id, last_snapshot_id
) VALUES (
    'table', 'public', 'dim_pcmn_codes', 'seed_extension', 'info',
    'W8-worker-001: extended seed from 24 → 80 codes (full MAR_DICT + BAS_MAP merge). Covers BS_A (21), BS_L (28), PL (21), PROFIT_APPR (5), WORKERS (5). Priority distribution HIGH 30 / MEDIUM 23 / LOW 27.',
    'resolved', 'chris@v4g.be', 'migration_diff',
    'manual_W8-worker_20260511', 'manual_W8-worker_20260511'
);


-- PART 3: ROLLBACK (only restore W8-core 24 codes)
-- ---------------------------------------------------------------------------
-- BEGIN;
-- DELETE FROM public.dim_pcmn_codes
-- WHERE pcmn_code IN (
--   -- list of codes added in W8-worker-001 (not in W8-core seed)
--   -- delete these to revert to 24-code baseline
--   SELECT pcmn_code FROM public.dim_pcmn_codes
--   WHERE pcmn_code NOT IN (
--     -- W8-core 24 codes:
--     '20/58', '20/28', '21', '22/27', '28', '30/36', '40/41', '50/53',
--     '10/15', '17', '42/48', '44',
--     '70', '60', '61', '62', '630', '649', '65', '9134', '9904',
--     '631/4', '635/8', '75',
--     '9087'
--   )
-- );
-- UPDATE gs_governance.schema_register
--   SET status = 'wontfix',
--       description = description || ' [ROLLED BACK in W8-worker-001-rollback]'
--   WHERE object_name = 'dim_pcmn_codes' AND issue_type = 'seed_extension';
-- COMMIT;
