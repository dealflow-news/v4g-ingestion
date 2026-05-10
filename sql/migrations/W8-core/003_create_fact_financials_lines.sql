-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 003 / fact_financials_lines
-- ════════════════════════════════════════════════════════════════════════
-- Granular financial line items per NBB filing. One row per
-- (filing, pcmn_code, period, data_type). Raw EUR values, NOT scaled.
-- NULL allowed for empty rubrics (m02-f filings often have empty lines).
--
-- Source: Rubrics array from JSON-XBRL response, filtered/preserved per
-- aggregator logic (see W8-worker branch).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS; safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.fact_financials_lines (
    filing_id           uuid NOT NULL
                        REFERENCES public.fact_filings(filing_id) ON DELETE CASCADE,
    pcmn_code           text NOT NULL,                 -- e.g. '70', '20/58', '9087'
    amount_period       text NOT NULL DEFAULT 'N',     -- 'N' (current) | 'N-1' (prior)
    data_type           text NOT NULL DEFAULT 'met:am1', -- 'met:am1' (amount) | 'met:cnt1' (count) etc
    amount_eur          numeric,                       -- raw value, NULL allowed
    type_amount         text,                          -- 'original' | 'revaluated' (NBB enum)
    PRIMARY KEY (filing_id, pcmn_code, amount_period, data_type)
);

-- Index for cross-filing queries on a specific PCMN line
CREATE INDEX IF NOT EXISTS idx_fact_financials_lines_pcmn
    ON public.fact_financials_lines(pcmn_code);

-- Index for current-period-only queries (most common)
CREATE INDEX IF NOT EXISTS idx_fact_financials_lines_current
    ON public.fact_financials_lines(filing_id, pcmn_code)
    WHERE amount_period = 'N' AND data_type = 'met:am1';

COMMENT ON TABLE public.fact_financials_lines IS
    '[FACT] Granular financial line items per NBB filing. One row per (filing, pcmn_code, period, data_type). Raw EUR values, NOT scaled to millions. NULL amount_eur allowed (empty rubrics in m02-f filings). Joined to fact_filings via filing_id; PCMN labels via dim_pcmn_codes.';

COMMENT ON COLUMN public.fact_financials_lines.pcmn_code IS
    'Belgian PCMN code. Not FK-constrained (allows new taxonomy codes to land freely). Lookup labels via dim_pcmn_codes.';
COMMENT ON COLUMN public.fact_financials_lines.amount_period IS
    'Reporting period within filing. ''N'' = current fiscal year (always present). ''N-1'' = prior year comparative (when stored).';
COMMENT ON COLUMN public.fact_financials_lines.data_type IS
    'NBB taxonomy data_type: met:am1 (amount), met:cnt1 (count, e.g. 9087 FTE), met:per1 (percentage). Most queries filter to met:am1.';
COMMENT ON COLUMN public.fact_financials_lines.amount_eur IS
    'Raw EUR amount (NOT scaled to millions). NULL = rubric present in filing but no value reported.';
