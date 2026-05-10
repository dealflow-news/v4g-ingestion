-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 009 / fact_financials VIEW (compat layer)
-- ════════════════════════════════════════════════════════════════════════
-- v3 (2026-05-10): simplified compat view per W8-core scope.
--
-- Replaces the original fact_financials TABLE with a VIEW that:
--   1. Exposes ALL 30 original columns from fact_financials_evidence
--      (preserves backwards compat for vw_target_financials and any
--       other downstream consumer)
--   2. Applies analyst overrides via COALESCE on 7 KPI columns
--      (revenue, ebitda, ebit, net_income, total_debt, net_debt,
--       working_capital)
--   3. Adds W8-new additive columns:
--      - *_as_reported: evidence values (never override-blended)
--      - has_overrides: boolean flag
--      - adjustment_count: integer count of active overrides
--
-- NOTE: NBB-derived blending (from fact_filings + fact_financials_lines)
-- is OUT OF SCOPE for W8-core. fact_filings is empty until W8-worker
-- backfill lands. This view is a thin pass-through over evidence + overrides
-- only. The "BE-aware precedence with NBB-derived" extension comes in the
-- W8-worker branch as an additive update.
--
-- Idempotent: CREATE OR REPLACE VIEW. Safe to re-run.
--
-- Writability: the view is NOT auto-updatable due to LEFT JOINs. INSTEAD OF
-- triggers in 009b_create_view_insteadof_triggers.sql redirect writes to
-- fact_financials_evidence transparently.
-- ════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW public.fact_financials AS
WITH overrides_active AS (
    SELECT party_id, period_end, metric_key, adjusted_value
    FROM public.fact_financials_overrides
    WHERE superseded_by IS NULL
)
SELECT
    -- ─── Original 30 columns (backwards compat for all SELECT consumers) ─
    e.financial_id,
    e.party_id,
    e.period_label,
    e.period_end,
    e.period_type,
    -- KPI columns: COALESCE override over evidence (7 metrics)
    COALESCE(o_rev.adjusted_value, e.revenue_eur_m)         AS revenue_eur_m,
    COALESCE(o_ebd.adjusted_value, e.ebitda_eur_m)          AS ebitda_eur_m,
    COALESCE(o_ebt.adjusted_value, e.ebit_eur_m)            AS ebit_eur_m,
    COALESCE(o_ni.adjusted_value,  e.net_income_eur_m)      AS net_income_eur_m,
    e.total_assets_eur_m,
    e.total_equity_eur_m,
    e.cash_eur_m,
    COALESCE(o_td.adjusted_value, e.total_debt_eur_m)       AS total_debt_eur_m,
    COALESCE(o_nd.adjusted_value, e.net_debt_eur_m)         AS net_debt_eur_m,
    COALESCE(o_wc.adjusted_value, e.working_capital_eur_m)  AS working_capital_eur_m,
    e.enterprise_value_eur_m,
    e.market_cap_eur_m,
    e.employees,
    e.amount_currency,
    e.fx_rate_to_eur,
    e.fx_date,
    e.nbb_model_type,
    e.nbb_filing_date,
    e.fiscal_year_start,
    e.fiscal_year_end,
    e.source_code,
    e.confidence,
    e.notes,
    e.created_at,
    e.updated_at,

    -- ─── W8-new: as-reported columns (evidence values, never blended) ───
    e.revenue_eur_m         AS revenue_as_reported,
    e.ebitda_eur_m          AS ebitda_as_reported,
    e.ebit_eur_m            AS ebit_as_reported,
    e.net_income_eur_m      AS net_income_as_reported,
    e.total_debt_eur_m      AS total_debt_as_reported,
    e.net_debt_eur_m        AS net_debt_as_reported,
    e.working_capital_eur_m AS working_capital_as_reported,

    -- ─── W8-new: provenance flags ───────────────────────────────────────
    (o_rev.adjusted_value IS NOT NULL OR
     o_ebd.adjusted_value IS NOT NULL OR
     o_ebt.adjusted_value IS NOT NULL OR
     o_ni.adjusted_value  IS NOT NULL OR
     o_td.adjusted_value  IS NOT NULL OR
     o_nd.adjusted_value  IS NOT NULL OR
     o_wc.adjusted_value  IS NOT NULL) AS has_overrides,

    (CASE WHEN o_rev.adjusted_value IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN o_ebd.adjusted_value IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN o_ebt.adjusted_value IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN o_ni.adjusted_value  IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN o_td.adjusted_value  IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN o_nd.adjusted_value  IS NOT NULL THEN 1 ELSE 0 END +
     CASE WHEN o_wc.adjusted_value  IS NOT NULL THEN 1 ELSE 0 END) AS adjustment_count

FROM public.fact_financials_evidence e
LEFT JOIN overrides_active o_rev
       ON o_rev.party_id = e.party_id AND o_rev.period_end = e.period_end
      AND o_rev.metric_key = 'revenue_eur_m'
LEFT JOIN overrides_active o_ebd
       ON o_ebd.party_id = e.party_id AND o_ebd.period_end = e.period_end
      AND o_ebd.metric_key = 'ebitda_eur_m'
LEFT JOIN overrides_active o_ebt
       ON o_ebt.party_id = e.party_id AND o_ebt.period_end = e.period_end
      AND o_ebt.metric_key = 'ebit_eur_m'
LEFT JOIN overrides_active o_ni
       ON o_ni.party_id  = e.party_id AND o_ni.period_end  = e.period_end
      AND o_ni.metric_key  = 'net_income_eur_m'
LEFT JOIN overrides_active o_td
       ON o_td.party_id  = e.party_id AND o_td.period_end  = e.period_end
      AND o_td.metric_key  = 'total_debt_eur_m'
LEFT JOIN overrides_active o_nd
       ON o_nd.party_id  = e.party_id AND o_nd.period_end  = e.period_end
      AND o_nd.metric_key  = 'net_debt_eur_m'
LEFT JOIN overrides_active o_wc
       ON o_wc.party_id  = e.party_id AND o_wc.period_end  = e.period_end
      AND o_wc.metric_key  = 'working_capital_eur_m';

COMMENT ON VIEW public.fact_financials IS
    '[VIEW] Compat layer: exposes all 30 original fact_financials columns from fact_financials_evidence + 7 KPI overrides via COALESCE + W8-new metadata columns (*_as_reported, has_overrides, adjustment_count). Writability via INSTEAD OF triggers (see 009b). NBB-derived blending from fact_filings + fact_financials_lines is W8-worker scope (additive update).';
