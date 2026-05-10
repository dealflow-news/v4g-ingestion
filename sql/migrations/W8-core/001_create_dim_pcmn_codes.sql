-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 001 / dim_pcmn_codes
-- ════════════════════════════════════════════════════════════════════════
-- Reference dictionary for NBB PCMN/MAR codes. Multi-language labels.
-- NOT FK-locked from fact_financials_lines (allows new codes from new
-- taxonomy versions to land naturally). Versioning via deprecated_at.
--
-- Per V4G doctrine: never DELETE codes (mark deprecated_at instead).
-- Multi-language labels support FR/NL/EN analyst use.
--
-- Idempotent: CREATE TABLE IF NOT EXISTS pattern; safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.dim_pcmn_codes (
    pcmn_code           text PRIMARY KEY,
    section             text NOT NULL,
    description_nl      text,
    description_fr      text,
    description_en      text,
    xbrl_element        text,                          -- e.g. 'be-gaap-ci:OperatingIncome'
    v4g_priority        text NOT NULL,
    valuation_relevance text[],                        -- e.g. {'DCF','EV/EBITDA','LBO'}
    notes               text,
    deprecated_at       timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- Constraints
    CONSTRAINT dim_pcmn_codes_section_chk CHECK (section IN
        ('PL','BS_A','BS_L','WORKERS','PROFIT_APPR','NOTES')),
    CONSTRAINT dim_pcmn_codes_priority_chk CHECK (v4g_priority IN
        ('HIGH','MEDIUM','LOW'))
);

CREATE INDEX IF NOT EXISTS idx_dim_pcmn_codes_section
    ON public.dim_pcmn_codes(section);
CREATE INDEX IF NOT EXISTS idx_dim_pcmn_codes_priority
    ON public.dim_pcmn_codes(v4g_priority)
    WHERE deprecated_at IS NULL;

COMMENT ON TABLE public.dim_pcmn_codes IS
    '[DIM] Reference dictionary for NBB PCMN/MAR codes (W8). Multi-language labels (NL/FR/EN). NOT FK-locked from fact_financials_lines — allows new codes from new taxonomy versions to land. Versioning via deprecated_at; never DELETE.';

COMMENT ON COLUMN public.dim_pcmn_codes.pcmn_code IS
    'Belgian PCMN code (e.g. ''70'' for revenue, ''20/58'' for total assets). Used as join key from fact_financials_lines.';
COMMENT ON COLUMN public.dim_pcmn_codes.section IS
    'PL=P&L, BS_A=Assets, BS_L=Liab+Equity, WORKERS=workforce, PROFIT_APPR=profit appropriation, NOTES=toelichtingen.';
COMMENT ON COLUMN public.dim_pcmn_codes.v4g_priority IS
    'V4G usage priority. HIGH = always extract (W8-core). MEDIUM = extract if available (W8-plus). LOW = on-demand only.';
COMMENT ON COLUMN public.dim_pcmn_codes.valuation_relevance IS
    'Valuation methods this code feeds. Array of: DCF, EV/EBITDA, LBO, Comparable, RoE, NetDebt, WorkingCapital, etc.';
