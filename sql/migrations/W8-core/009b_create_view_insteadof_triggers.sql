-- ════════════════════════════════════════════════════════════════════════
-- W8-core / 009b / INSTEAD OF triggers on fact_financials view
-- ════════════════════════════════════════════════════════════════════════
-- Makes the fact_financials view transparently writable. All INSERT/UPDATE/
-- DELETE operations route to fact_financials_evidence (the renamed table).
--
-- Why: existing functions (fn_promote_nbb_filing, fn_merge_party) write to
-- fact_financials by name. INSTEAD OF triggers preserve their behavior
-- without requiring function rewrites. Forward-compatible — any future
-- write-caller works automatically.
--
-- Doctrine note: writes to the view ALWAYS go to evidence. To set/modify
-- analyst overrides, write directly to fact_financials_overrides (not
-- through the view). This matches the V4G separation between "evidence"
-- and "narrative" layers.
--
-- Idempotent: CREATE OR REPLACE FUNCTION + DROP TRIGGER IF EXISTS pattern.
-- Safe to re-run.
-- ════════════════════════════════════════════════════════════════════════

-- ─── INSERT redirect ────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fact_financials_view_insert()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO public.fact_financials_evidence (
        financial_id, party_id, period_label, period_end, period_type,
        revenue_eur_m, ebitda_eur_m, ebit_eur_m, net_income_eur_m,
        total_assets_eur_m, total_equity_eur_m, cash_eur_m,
        total_debt_eur_m, net_debt_eur_m, working_capital_eur_m,
        enterprise_value_eur_m, market_cap_eur_m, employees,
        amount_currency, fx_rate_to_eur, fx_date,
        nbb_model_type, nbb_filing_date,
        fiscal_year_start, fiscal_year_end,
        source_code, confidence, notes,
        created_at, updated_at
    )
    VALUES (
        COALESCE(NEW.financial_id, uuid_generate_v4()),
        NEW.party_id,
        NEW.period_label,
        NEW.period_end,
        COALESCE(NEW.period_type, 'Annual'),
        NEW.revenue_eur_m,
        NEW.ebitda_eur_m,
        NEW.ebit_eur_m,
        NEW.net_income_eur_m,
        NEW.total_assets_eur_m,
        NEW.total_equity_eur_m,
        NEW.cash_eur_m,
        NEW.total_debt_eur_m,
        NEW.net_debt_eur_m,
        NEW.working_capital_eur_m,
        NEW.enterprise_value_eur_m,
        NEW.market_cap_eur_m,
        NEW.employees,
        COALESCE(NEW.amount_currency, 'EUR'),
        NEW.fx_rate_to_eur,
        NEW.fx_date,
        NEW.nbb_model_type,
        NEW.nbb_filing_date,
        NEW.fiscal_year_start,
        NEW.fiscal_year_end,
        NEW.source_code,
        COALESCE(NEW.confidence, 'Confirmed'),
        NEW.notes,
        COALESCE(NEW.created_at, now()),
        COALESCE(NEW.updated_at, now())
    );
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.fact_financials_view_insert() IS
    '[TRIGGER fn] INSTEAD OF INSERT on fact_financials view → routes to fact_financials_evidence. COALESCEs defaults for fields with table-level defaults that NULL would bypass. Per W8-core doctrine.';

DROP TRIGGER IF EXISTS fact_financials_view_insert_trg ON public.fact_financials;
CREATE TRIGGER fact_financials_view_insert_trg
INSTEAD OF INSERT ON public.fact_financials
FOR EACH ROW
EXECUTE FUNCTION public.fact_financials_view_insert();


-- ─── UPDATE redirect ────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fact_financials_view_update()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- Identify the underlying evidence row by financial_id (PK).
    -- Writes go to evidence; overrides remain untouched (separate write
    -- path via fact_financials_overrides per W8 doctrine).
    UPDATE public.fact_financials_evidence SET
        party_id               = NEW.party_id,
        period_label           = NEW.period_label,
        period_end             = NEW.period_end,
        period_type            = NEW.period_type,
        revenue_eur_m          = NEW.revenue_eur_m,
        ebitda_eur_m           = NEW.ebitda_eur_m,
        ebit_eur_m             = NEW.ebit_eur_m,
        net_income_eur_m       = NEW.net_income_eur_m,
        total_assets_eur_m     = NEW.total_assets_eur_m,
        total_equity_eur_m     = NEW.total_equity_eur_m,
        cash_eur_m             = NEW.cash_eur_m,
        total_debt_eur_m       = NEW.total_debt_eur_m,
        net_debt_eur_m         = NEW.net_debt_eur_m,
        working_capital_eur_m  = NEW.working_capital_eur_m,
        enterprise_value_eur_m = NEW.enterprise_value_eur_m,
        market_cap_eur_m       = NEW.market_cap_eur_m,
        employees              = NEW.employees,
        amount_currency        = NEW.amount_currency,
        fx_rate_to_eur         = NEW.fx_rate_to_eur,
        fx_date                = NEW.fx_date,
        nbb_model_type         = NEW.nbb_model_type,
        nbb_filing_date        = NEW.nbb_filing_date,
        fiscal_year_start      = NEW.fiscal_year_start,
        fiscal_year_end        = NEW.fiscal_year_end,
        source_code            = NEW.source_code,
        confidence             = NEW.confidence,
        notes                  = NEW.notes,
        updated_at             = now()  -- always refresh on write
    WHERE financial_id = OLD.financial_id;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.fact_financials_view_update() IS
    '[TRIGGER fn] INSTEAD OF UPDATE on fact_financials view → routes to fact_financials_evidence. Identifies row via OLD.financial_id (PK). updated_at always refreshed. Override values are NOT written here; overrides have their own write path (fact_financials_overrides).';

DROP TRIGGER IF EXISTS fact_financials_view_update_trg ON public.fact_financials;
CREATE TRIGGER fact_financials_view_update_trg
INSTEAD OF UPDATE ON public.fact_financials
FOR EACH ROW
EXECUTE FUNCTION public.fact_financials_view_update();


-- ─── DELETE redirect ────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.fact_financials_view_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    DELETE FROM public.fact_financials_evidence
    WHERE financial_id = OLD.financial_id;
    RETURN OLD;
END;
$$;

COMMENT ON FUNCTION public.fact_financials_view_delete() IS
    '[TRIGGER fn] INSTEAD OF DELETE on fact_financials view → routes to fact_financials_evidence by financial_id. Note: overrides for the deleted row are NOT cascaded — they remain in fact_financials_overrides (audit trail). Manual cleanup if needed.';

DROP TRIGGER IF EXISTS fact_financials_view_delete_trg ON public.fact_financials;
CREATE TRIGGER fact_financials_view_delete_trg
INSTEAD OF DELETE ON public.fact_financials
FOR EACH ROW
EXECUTE FUNCTION public.fact_financials_view_delete();


-- ─── Sanity verify ──────────────────────────────────────────────────────
DO $$
DECLARE
    v_trigger_count integer;
BEGIN
    SELECT count(*) INTO v_trigger_count
    FROM pg_trigger
    WHERE tgrelid = 'public.fact_financials'::regclass
      AND NOT tgisinternal;

    IF v_trigger_count <> 3 THEN
        RAISE EXCEPTION 'Expected 3 INSTEAD OF triggers on fact_financials view; got %', v_trigger_count;
    END IF;

    RAISE NOTICE 'fact_financials view now has % INSTEAD OF triggers (INSERT/UPDATE/DELETE → evidence)', v_trigger_count;
END $$;
