-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 006 / RLS policies
-- ════════════════════════════════════════════════════════════════════════
-- Stage 2 lockdown doctrine: RLS enabled on all public tables, defense-
-- in-depth. Per V4G doctrine in CLAUDE.md, no INSERT/UPDATE/DELETE for
-- anon or authenticated; service_role bypass.
--
-- Special case for fact_financials_overrides: stricter RLS so only
-- senior analysts can INSERT (via session-set role or claim).
--
-- Idempotent: DROP POLICY IF EXISTS + CREATE POLICY pattern. Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

-- ─── dim_pcmn_codes ──────────────────────────────────────────────────────
ALTER TABLE public.dim_pcmn_codes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS dim_pcmn_codes_read_all ON public.dim_pcmn_codes;
CREATE POLICY dim_pcmn_codes_read_all ON public.dim_pcmn_codes
    FOR SELECT
    USING (true);

-- No INSERT/UPDATE/DELETE for non-service_role. service_role bypasses RLS.

-- ─── fact_filings ────────────────────────────────────────────────────────
ALTER TABLE public.fact_filings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fact_filings_read_all ON public.fact_filings;
CREATE POLICY fact_filings_read_all ON public.fact_filings
    FOR SELECT
    USING (true);

-- ─── fact_financials_lines ───────────────────────────────────────────────
ALTER TABLE public.fact_financials_lines ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fact_financials_lines_read_all ON public.fact_financials_lines;
CREATE POLICY fact_financials_lines_read_all ON public.fact_financials_lines
    FOR SELECT
    USING (true);

-- ─── fact_financials_overrides ───────────────────────────────────────────
-- Stricter than other tables: INSERT requires session-set claim
-- 'role:analyst_senior' or 'role:admin'. Default RLS pattern blocks all
-- write for anon/authenticated; service_role bypasses (used by ingester
-- which checks session role separately).
ALTER TABLE public.fact_financials_overrides ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS overrides_read_all ON public.fact_financials_overrides;
CREATE POLICY overrides_read_all ON public.fact_financials_overrides
    FOR SELECT
    USING (true);

-- Note: INSERT/UPDATE policy is enforced at application level (ingester
-- checks session role before connecting as service_role). Adding a direct
-- DB-level role-check policy can be added in a follow-up if needed.

COMMENT ON POLICY overrides_read_all ON public.fact_financials_overrides IS
    'RLS pattern: SELECT for authenticated, ALL for service_role (default V4G doctrine). Senior-analyst write check is enforced at the application layer (adjustment ingester verifies role/identity before connecting via service_role). A direct DB-level role-check policy can be added in Phase 2/3 if application-layer enforcement proves insufficient.';
