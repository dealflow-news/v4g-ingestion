-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 005 / dim_pcmn_codes seed (24 W8-core codes)
-- ════════════════════════════════════════════════════════════════════════
-- W8-core minimum canonical line set: 24 codes that
--   (a) reproduce existing fact_financials KPIs
--   (b) enable DCF + EV multiples building blocks
-- Additional codes (W8-plus) added later as needed.
--
-- Source: V4G_NBB_CBSO_Connector_v03c.xlsx XBRL_MAR_Mapping sheet
-- (taxonomy: nbb-cbso-26.0.8, BE-GAAP).
--
-- Expected counts after seed (harmonized with 010_validate_canary.sql T2):
--   HIGH   = 21
--   MEDIUM =  3
--   total  = 24
--   sections: PL=12, BS_A=7, BS_L=4, WORKERS=1
--
-- Idempotent: ON CONFLICT DO UPDATE pattern. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

INSERT INTO public.dim_pcmn_codes
    (pcmn_code, section, description_nl, description_en, xbrl_element, v4g_priority, valuation_relevance, notes)
VALUES
    -- ─── P&L (Resultatenrekening) ─────────────────────────────────────
    ('70',    'PL', 'Bedrijfsopbrengsten (Omzet)',
     'Operating Income (Revenue)', 'be-gaap-ci:OperatingIncome',
     'HIGH', '{DCF,EV/EBITDA,LBO,Comparable}',
     'Top-line revenue. Maps to revenue_eur_m in fact_financials view.'),

    ('60',    'PL', 'Handelsgoederen, grond- en hulpstoffen',
     'Raw Materials/Goods for Resale', 'be-gaap-ci:RawMaterialsConsumablesGoodsForResale',
     'HIGH', '{DCF,EV/EBITDA,GrossMargin}',
     'COGS component 1.'),

    ('61',    'PL', 'Diensten en diverse goederen',
     'Services and Other Goods', 'be-gaap-ci:ServicesAndOtherGoods',
     'HIGH', '{DCF,EV/EBITDA,GrossMargin}',
     'COGS component 2 — services + other goods.'),

    ('62',    'PL', 'Bezoldigingen, sociale lasten, pensioenen',
     'Personnel Costs', 'be-gaap-ci:RemunerationsSocialSecurityPensions',
     'HIGH', '{DCF,EV/EBITDA,Productivity}',
     'Personnel costs. Combined with 9087 (FTE) for productivity metrics.'),

    ('630',   'PL', 'Afschrijvingen en waardeverminderingen',
     'Depreciation/Amortisation', 'be-gaap-ci:DepreciationAmortisationFixedAssets',
     'HIGH', '{DCF,EBITDA-bridge,CapEx-proxy}',
     'D&A. Critical for EBITDA reconstruction (EBIT + D&A) and CapEx proxy.'),

    ('631/4', 'PL', 'Waardeverminderingen op vlottende activa',
     'Write-downs on Current Assets', 'be-gaap-ci:AmountsWrittenDownCurrentAssets',
     'MEDIUM', '{EBITDA-normalization}',
     'Inventory/receivables write-downs. EBITDA add-back if non-recurring.'),

    ('635/8', 'PL', 'Voorzieningen voor risico''s en kosten',
     'Provisions for Risks and Charges', 'be-gaap-ci:ProvisionsRisksCharges',
     'MEDIUM', '{EBITDA-normalization}',
     'Provisions movement. EBITDA add-back if non-recurring.'),

    ('649',   'PL', 'Bedrijfswinst (verlies) (EBIT)',
     'Operating Profit/Loss (EBIT)', 'be-gaap-ci:OperatingProfitLoss',
     'HIGH', '{DCF,EV/EBITDA,Multiples}',
     'EBIT — operating profit. Pre-financial-result, pre-tax.'),

    ('65',    'PL', 'Financiële kosten',
     'Financial Charges', 'be-gaap-ci:FinancialCharges',
     'HIGH', '{DCF,Coverage,EBITDA-norm}',
     'Interest expense + other financial charges.'),

    ('75',    'PL', 'Financiële opbrengsten',
     'Financial Income', 'be-gaap-ci:FinancialIncome',
     'MEDIUM', '{NetFinancialResult,EBITDA-norm}',
     'Interest income + dividend income from financial assets.'),

    ('9134',  'PL', 'Belastingen op het resultaat',
     'Income Tax Expense', 'be-gaap-ci:IncomeTaxExpense',
     'HIGH', '{DCF,EffectiveTaxRate}',
     'Income tax line. Combined with 9904 for effective tax rate.'),

    ('9904',  'PL', 'Resultaat van het boekjaar (na belasting)',
     'Profit/Loss After Tax (Net Income)', 'be-gaap-ci:ProfitLossAfterTax',
     'HIGH', '{RoE,Comparable,DividendPayout}',
     'Net income / bottom line. Maps to net_income_eur_m.'),

    -- ─── BS — Activa (Assets) ─────────────────────────────────────────
    ('21',    'BS_A', 'Immateriële vaste activa',
     'Intangible Fixed Assets', 'be-gaap-ci:IntangibleAssets',
     'HIGH', '{Goodwill,DD,Acquisition}',
     'Intangibles (goodwill, R&D capitalized, concessions). Critical for DD goodwill exposure.'),

    ('22/27', 'BS_A', 'Materiële vaste activa',
     'Tangible Fixed Assets', 'be-gaap-ci:TangibleAssets',
     'HIGH', '{CapEx,DCF,AssetIntensity}',
     'PPE total. CapEx proxy = ΔPPE + D&A.'),

    ('28',    'BS_A', 'Financiële vaste activa',
     'Financial Fixed Assets', 'be-gaap-ci:FinancialAssets',
     'HIGH', '{Participations,GroupStructure}',
     'Investments in subsidiaries / associates. Linked to fact_participations.'),

    ('30/36', 'BS_A', 'Voorraden',
     'Inventory', 'be-gaap-ci:Inventories',
     'HIGH', '{WorkingCapital,DCF,InventoryDays}',
     'Total inventory. Working capital component.'),

    ('40/41', 'BS_A', 'Vorderingen op ten hoogste een jaar',
     'Current Receivables (≤1 year)', 'be-gaap-ci:CurrentReceivables',
     'HIGH', '{WorkingCapital,DCF,DSO}',
     'Trade + other current receivables. Working capital component.'),

    ('54/58', 'BS_A', 'Liquide middelen',
     'Cash and Equivalents', 'be-gaap-ci:CashEquivalents',
     'HIGH', '{NetDebt,DCF,Liquidity}',
     'Cash + cash equivalents. Maps to cash_eur_m. Net debt = (17 + 42/48) - 54/58.'),

    ('20/58', 'BS_A', 'TOTAAL DER ACTIVA',
     'TOTAL ASSETS', 'be-gaap-ci:TotalAssets',
     'HIGH', '{Sanity,Multiples,EV/Assets}',
     'Total assets. Sanity check for balance sheet integrity.'),

    -- ─── BS — Passiva (Liabilities & Equity) ──────────────────────────
    ('10/15', 'BS_L', 'Eigen vermogen',
     'Total Equity', 'be-gaap-ci:Equity',
     'HIGH', '{Multiples,RoE,BookValue}',
     'Total equity. Includes capital, reserves, retained earnings. Maps to total_equity_eur_m.'),

    ('17',    'BS_L', 'Schulden op meer dan een jaar',
     'Long-term Debt', 'be-gaap-ci:LongTermDebt',
     'HIGH', '{NetDebt,LBO,MaturityWall}',
     'Long-term financial + non-financial debt. Component of total debt.'),

    ('42/48', 'BS_L', 'Schulden op ten hoogste een jaar',
     'Short-term Debt', 'be-gaap-ci:ShortTermDebt',
     'HIGH', '{NetDebt,WorkingCapital}',
     'Short-term debt incl. current portion of long-term. Net debt component.'),

    ('44',    'BS_L', 'Handelsschulden',
     'Trade Payables', 'be-gaap-ci:TradePayables',
     'HIGH', '{WorkingCapital,DCF,DPO}',
     'Trade creditors. Working capital component (negative — reduces WC need).'),

    -- ─── Workforce ─────────────────────────────────────────────────────
    ('9087',  'WORKERS', 'Gemiddelde personeelsbestand (FTE)',
     'Average Number of Employees (FTE)', 'be-gaap-ci:AverageNumberEmployees',
     'HIGH', '{Productivity,Scale,UnitEconomics}',
     'Average FTE for fiscal year. Note: data_type = met:cnt1, NOT met:am1. Maps to employees column.')

ON CONFLICT (pcmn_code) DO UPDATE SET
    description_nl      = EXCLUDED.description_nl,
    description_en      = EXCLUDED.description_en,
    xbrl_element        = EXCLUDED.xbrl_element,
    v4g_priority        = EXCLUDED.v4g_priority,
    valuation_relevance = EXCLUDED.valuation_relevance,
    notes               = EXCLUDED.notes,
    deprecated_at       = NULL;  -- un-deprecate if previously deprecated

-- Sanity verify — exact counts (harmonized with 010 validation)
DO $$
DECLARE
    v_high    integer;
    v_medium  integer;
    v_total   integer;
BEGIN
    SELECT count(*) FILTER (WHERE v4g_priority = 'HIGH'),
           count(*) FILTER (WHERE v4g_priority = 'MEDIUM'),
           count(*)
      INTO v_high, v_medium, v_total
      FROM public.dim_pcmn_codes
      WHERE deprecated_at IS NULL;

    RAISE NOTICE 'dim_pcmn_codes seed result: HIGH=%, MEDIUM=%, total=%',
                 v_high, v_medium, v_total;

    IF v_high <> 21 THEN
        RAISE EXCEPTION 'Expected exactly 21 HIGH codes after W8-core seed; got %', v_high;
    END IF;
    IF v_medium <> 3 THEN
        RAISE EXCEPTION 'Expected exactly 3 MEDIUM codes after W8-core seed; got %', v_medium;
    END IF;
    IF v_total <> 24 THEN
        RAISE EXCEPTION 'Expected exactly 24 total codes after W8-core seed; got %', v_total;
    END IF;

    RAISE NOTICE 'Seed counts match W8-core spec (21 HIGH + 3 MEDIUM = 24 total).';
END $$;
