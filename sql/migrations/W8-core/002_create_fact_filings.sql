-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 002 / fact_filings
-- ════════════════════════════════════════════════════════════════════════
-- One row per NBB filing. Filing-level metadata (model_type, period dates,
-- point-in-time company state). Granular line items in fact_financials_lines
-- via filing_id FK. Versioned via superseded_by for restatements.
--
-- Per V4G doctrine: NBB filing = primary canonical evidence for BE entities.
-- Other sources land in fact_financials_evidence (KPI-level only).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS; safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.fact_filings (
    filing_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id            uuid NOT NULL REFERENCES public.party_registry(party_id),

    -- Provenance
    source_code         text NOT NULL REFERENCES public.ref_sources(source_code),
    filing_reference    text NOT NULL,                 -- e.g. '2025-00231176'
    deposit_date        date,                          -- NBB DepositDate

    -- Period
    period_start        date NOT NULL,                 -- ExerciseDates.startDate
    period_end          date NOT NULL,                 -- ExerciseDates.endDate
    period_months       smallint NOT NULL,             -- derived; supports verlengd FY
    period_flag         text NOT NULL DEFAULT 'normal',
    period_label        text NOT NULL,                 -- '2024' | '2021-extended' etc.

    -- Filing characteristics
    nbb_model_type      text,                          -- 'cbso-new' | 'pfs-old'
    nbb_schema_subtype  text,                          -- 'm01-f' | 'm02-f' | 'm02-a'
    taxonomy_version    text,                          -- 'nbb-cbso-26.0.8'
    consolidation       text,                          -- 'standalone' | 'consolidated'

    -- Filing-time company snapshot (point-in-time)
    enterprise_name     text,
    legal_form_code     text,                          -- 'lgf:m014' (NBB taxonomy)
    raw_address         jsonb,                         -- {Street, Number, Box, City, Country, ...}
    language            text,                          -- 'NL' | 'FR' | 'DE'
    currency            text NOT NULL DEFAULT 'EUR',

    -- Versioning (for restatements)
    superseded_by       uuid REFERENCES public.fact_filings(filing_id),
    superseded_at       timestamptz,

    -- Audit
    loaded_at           timestamptz NOT NULL DEFAULT now(),
    loaded_by           text,                          -- 'lane_a' | 'lane_b_b3' | 'backfill'

    -- Constraints
    CONSTRAINT fact_filings_period_months_chk
        CHECK (period_months > 0 AND period_months <= 24),
    CONSTRAINT fact_filings_period_flag_chk
        CHECK (period_flag IN ('normal','extended','shortened')),
    CONSTRAINT fact_filings_consolidation_chk
        CHECK (consolidation IS NULL OR consolidation IN ('standalone','consolidated')),
    CONSTRAINT fact_filings_period_dates_chk
        CHECK (period_end > period_start),
    CONSTRAINT fact_filings_unique_filing
        UNIQUE (source_code, filing_reference)
);

CREATE INDEX IF NOT EXISTS idx_fact_filings_party
    ON public.fact_filings(party_id);
CREATE INDEX IF NOT EXISTS idx_fact_filings_period_end
    ON public.fact_filings(period_end);
CREATE INDEX IF NOT EXISTS idx_fact_filings_active
    ON public.fact_filings(party_id, period_end)
    WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS idx_fact_filings_source
    ON public.fact_filings(source_code);

COMMENT ON TABLE public.fact_filings IS
    '[FACT] One row per NBB filing. Filing-level metadata (model, period, point-in-time company state). Granular line items in fact_financials_lines via filing_id FK. Versioned via superseded_by for restatements. W8 doctrine: see GOLDEN_SAFE_SOP.md.';

COMMENT ON COLUMN public.fact_filings.filing_reference IS
    'NBB filing reference, e.g. ''2025-00231176''. UNIQUE per source_code.';
COMMENT ON COLUMN public.fact_filings.period_flag IS
    'Period anomaly flag. ''extended'' = verlengd boekjaar (>12 months), ''shortened'' = verkort (<12), ''normal'' = standard 12.';
COMMENT ON COLUMN public.fact_filings.nbb_model_type IS
    'NBB model family: cbso-new (post-04/2022, JSON-XBRL) | pfs-old (pre-04/2022 or smaller filings, XBRL only).';
COMMENT ON COLUMN public.fact_filings.nbb_schema_subtype IS
    'NBB schema variant: m01-f (volledig), m02-f (verkort), m02-a (micro-aanvullend), etc. Affects which PCMN codes are available.';
COMMENT ON COLUMN public.fact_filings.taxonomy_version IS
    'NBB taxonomy version, e.g. ''nbb-cbso-26.0.8''. Tracks which code-set was used at filing time.';
COMMENT ON COLUMN public.fact_filings.superseded_by IS
    'If this filing was replaced by a later restatement, points to the new filing_id. Active filings have NULL.';
COMMENT ON COLUMN public.fact_filings.raw_address IS
    'Address-at-filing-time as structured JSONB. Different from party_registry address (which tracks current state).';
