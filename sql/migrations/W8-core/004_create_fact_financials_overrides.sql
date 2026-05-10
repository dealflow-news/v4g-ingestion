-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 004 / fact_financials_overrides
-- ════════════════════════════════════════════════════════════════════════
-- Analyst-curated management adjustments captured during interviews.
-- NEVER a "fix" of NBB data — always parallel narrative.
--
-- Per V4G doctrine:
--   - Categories: NORMALIZATION, PROFORMA, CORRECTION, OTHER
--   - Hard delete forbidden — version via superseded_by
--   - Only one ACTIVE override per (party, period, metric) — partial UNIQUE
--   - INSERT restricted to senior analysts via RLS (separate file)
--
-- Idempotent: CREATE TABLE IF NOT EXISTS; safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.fact_financials_overrides (
    override_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id           uuid NOT NULL REFERENCES public.party_registry(party_id),
    period_end         date NOT NULL,

    -- What's being overridden
    metric_key         text NOT NULL,                  -- 'ebitda_eur_m' | 'revenue_eur_m' | '70' (raw pcmn)
    metric_kind        text NOT NULL,                  -- 'derived' | 'raw_line'
    adjusted_value     numeric NOT NULL,
    unit               text NOT NULL DEFAULT 'EUR_m',  -- 'EUR_m' for derived, 'EUR' for raw_line

    -- Why (mandatory provenance)
    category           text NOT NULL,                  -- NORMALIZATION/PROFORMA/CORRECTION/OTHER
    reason             text NOT NULL,                  -- free-text justification

    -- Source
    source_code        text NOT NULL DEFAULT 'PARTY_INTERVIEW',
    interview_date     date,
    contact_name       text,
    contact_role       text,                           -- 'CFO' | 'CEO' | 'Owner'

    -- Audit trail (mandatory)
    recorded_by        text NOT NULL,                  -- analyst email/id
    recorded_at        timestamptz NOT NULL DEFAULT now(),

    -- Versioning (never DELETE — supersede instead)
    superseded_by      uuid REFERENCES public.fact_financials_overrides(override_id),
    superseded_at      timestamptz,

    -- Constraints
    CONSTRAINT fact_financials_overrides_kind_chk
        CHECK (metric_kind IN ('derived','raw_line')),
    CONSTRAINT fact_financials_overrides_category_chk
        CHECK (category IN ('NORMALIZATION','PROFORMA','CORRECTION','OTHER')),
    CONSTRAINT fact_financials_overrides_source_chk
        CHECK (source_code IN ('PARTY_INTERVIEW','ANALYST_DECISION'))
);

-- Partial unique: only ONE active override per (party, period, metric).
-- Superseded overrides remain queryable for audit trail.
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_override_unique
    ON public.fact_financials_overrides (party_id, period_end, metric_key)
    WHERE superseded_by IS NULL;

CREATE INDEX IF NOT EXISTS idx_fact_financials_overrides_party
    ON public.fact_financials_overrides(party_id);
CREATE INDEX IF NOT EXISTS idx_fact_financials_overrides_recorded_by
    ON public.fact_financials_overrides(recorded_by, recorded_at DESC);

COMMENT ON TABLE public.fact_financials_overrides IS
    '[FACT] Analyst-curated management adjustments captured during interviews. NEVER a "fix" of NBB data — always parallel narrative. Only ONE active override per (party, period, metric); superseding via superseded_by. RLS pattern: SELECT for authenticated, ALL for service_role; application layer enforces senior-analyst check before connecting via service_role for write operations. See GOLDEN_SAFE_SOP.md / W8 doctrine.';

COMMENT ON COLUMN public.fact_financials_overrides.metric_key IS
    'What''s being adjusted. For derived metrics use canonical name (e.g. ''ebitda_eur_m''). For raw line use PCMN code (e.g. ''70'').';
COMMENT ON COLUMN public.fact_financials_overrides.metric_kind IS
    '''derived'' (most common — adjusts a computed KPI like EBITDA) or ''raw_line'' (replaces a specific PCMN value, rare).';
COMMENT ON COLUMN public.fact_financials_overrides.category IS
    'NORMALIZATION (eenmalige posten weghalen) | PROFORMA (acquisitie-effect simuleren) | CORRECTION (echte fout in filing — zeldzaam) | OTHER.';
COMMENT ON COLUMN public.fact_financials_overrides.superseded_by IS
    'When analyst updates view (e.g. after follow-up interview), insert NEW row + set this on the old. Active = WHERE superseded_by IS NULL.';
COMMENT ON COLUMN public.fact_financials_overrides.recorded_by IS
    'Analyst identity (email or user-id). Required for audit trail. RLS policy enforces same-user write only.';
