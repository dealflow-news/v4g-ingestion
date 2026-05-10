-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 004b / fact_financials_overrides_current (convenience view)
-- ════════════════════════════════════════════════════════════════════════
-- Convenience view exposing ONLY active overrides (superseded_by IS NULL).
-- Most application/UI queries want the current state, not history.
--
-- Per V4G doctrine: full history stays in fact_financials_overrides
-- (auditable, queryable). This view is the default-case shortcut.
--
-- Idempotent: CREATE OR REPLACE VIEW. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW public.fact_financials_overrides_current AS
SELECT
    override_id,
    party_id,
    period_end,
    metric_key,
    metric_kind,
    adjusted_value,
    unit,
    category,
    reason,
    source_code,
    interview_date,
    contact_name,
    contact_role,
    recorded_by,
    recorded_at
FROM public.fact_financials_overrides
WHERE superseded_by IS NULL;

COMMENT ON VIEW public.fact_financials_overrides_current IS
    '[VIEW] Active overrides only (superseded_by IS NULL). Convenience for the 99% of queries that want current state. Full history queryable via fact_financials_overrides directly. Per W8 doctrine.';
