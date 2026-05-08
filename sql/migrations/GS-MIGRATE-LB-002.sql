-- ============================================================================
-- GS-MIGRATE-LB-002 · Lane B promotion: fn_promote_nbb_filing
-- ----------------------------------------------------------------------------
-- Purpose:  Atomic, transactional promotion van een _stg_nbb_filings row naar
--           public.fact_financials. Encodes the conflict-resolutie doctrine
--           in SQL zodat de Python worker stateless kan blijven:
--             - Latest filing_date wins per (kbo_nr, fiscal_year_end)
--             - Older parsed filings worden gedemoteerd naar 'superseded'
--             - UPSERT op (party_id, period_label, source_code='SRC_NBB')
--
-- Signature:
--   fn_promote_nbb_filing(p_filing_id uuid, p_canonical jsonb) → jsonb
--
-- Returns: jsonb met outcome ('parsed' | 'superseded'), fact_financial_id
--          (nullable), en demoted_older_filings count.
-- ============================================================================

-- ============================================================================
-- PART 1: MIGRATION
-- ============================================================================
BEGIN;

CREATE OR REPLACE FUNCTION public.fn_promote_nbb_filing(
  p_filing_id uuid,
  p_canonical jsonb
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
  v_filing      public._stg_nbb_filings%ROWTYPE;
  v_winner_date date;
  v_demoted     integer := 0;
  v_fact_id     uuid;
BEGIN
  -- 1. Lock target row
  SELECT * INTO v_filing
  FROM public._stg_nbb_filings
  WHERE filing_id = p_filing_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Filing not found: %', p_filing_id
      USING ERRCODE = 'P0002';
  END IF;

  IF v_filing.parse_status <> 'pending' THEN
    RAISE EXCEPTION 'Filing % already in status %, can only promote pending',
      p_filing_id, v_filing.parse_status
      USING ERRCODE = '42501';
  END IF;

  IF v_filing.filing_date IS NULL THEN
    RAISE EXCEPTION 'Filing % has NULL filing_date — required for conflict resolution',
      p_filing_id USING ERRCODE = '23502';
  END IF;

  IF v_filing.party_id IS NULL OR v_filing.fiscal_year_end IS NULL THEN
    RAISE EXCEPTION 'Filing % missing party_id or fiscal_year_end', p_filing_id
      USING ERRCODE = '23502';
  END IF;

  -- 2. Find current winner for this (kbo_nr, fiscal_year_end)
  SELECT MAX(filing_date) INTO v_winner_date
  FROM public._stg_nbb_filings
  WHERE kbo_nr = v_filing.kbo_nr
    AND fiscal_year_end = v_filing.fiscal_year_end
    AND parse_status = 'parsed'
    AND filing_id <> p_filing_id;

  -- 3. CASE B — we lose: existing winner is strictly newer
  IF v_winner_date IS NOT NULL AND v_winner_date > v_filing.filing_date THEN
    UPDATE public._stg_nbb_filings
    SET parse_status = 'superseded'
    WHERE filing_id = p_filing_id;

    RETURN jsonb_build_object(
      'outcome', 'superseded',
      'filing_id', p_filing_id,
      'reason', format(
        'Existing winner has filing_date %s, this filing has %s',
        v_winner_date, v_filing.filing_date
      ),
      'fact_financial_id', NULL,
      'demoted_older_filings', 0
    );
  END IF;

  -- 4. CASE A — we win: demote older parsed filings + UPSERT fact_financials
  UPDATE public._stg_nbb_filings
  SET parse_status      = 'superseded',
      fact_financial_id = NULL
  WHERE kbo_nr           = v_filing.kbo_nr
    AND fiscal_year_end  = v_filing.fiscal_year_end
    AND parse_status     = 'parsed'
    AND filing_id        <> p_filing_id;

  GET DIAGNOSTICS v_demoted = ROW_COUNT;

  -- UPSERT canonical fact
  INSERT INTO public.fact_financials (
    party_id, period_label, period_end, period_type,
    revenue_eur_m, ebitda_eur_m, ebit_eur_m, net_income_eur_m,
    total_assets_eur_m, total_equity_eur_m, cash_eur_m,
    total_debt_eur_m, net_debt_eur_m, working_capital_eur_m,
    employees,
    amount_currency, fx_rate_to_eur, fx_date,
    nbb_model_type, nbb_filing_date,
    fiscal_year_start, fiscal_year_end,
    source_code, confidence, notes
  )
  VALUES (
    v_filing.party_id,
    p_canonical->>'period_label',
    NULLIF(p_canonical->>'period_end', '')::date,
    COALESCE(p_canonical->>'period_type', 'Annual'),
    NULLIF(p_canonical->>'revenue_eur_m','')::numeric,
    NULLIF(p_canonical->>'ebitda_eur_m','')::numeric,
    NULLIF(p_canonical->>'ebit_eur_m','')::numeric,
    NULLIF(p_canonical->>'net_income_eur_m','')::numeric,
    NULLIF(p_canonical->>'total_assets_eur_m','')::numeric,
    NULLIF(p_canonical->>'total_equity_eur_m','')::numeric,
    NULLIF(p_canonical->>'cash_eur_m','')::numeric,
    NULLIF(p_canonical->>'total_debt_eur_m','')::numeric,
    NULLIF(p_canonical->>'net_debt_eur_m','')::numeric,
    NULLIF(p_canonical->>'working_capital_eur_m','')::numeric,
    NULLIF(p_canonical->>'employees','')::integer,
    COALESCE(p_canonical->>'amount_currency', 'EUR'),
    COALESCE(NULLIF(p_canonical->>'fx_rate_to_eur','')::numeric, 1.0),
    NULLIF(p_canonical->>'fx_date','')::date,
    COALESCE(p_canonical->>'nbb_model_type', v_filing.nbb_model_type),
    v_filing.filing_date,
    v_filing.fiscal_year_start,
    v_filing.fiscal_year_end,
    'SRC_NBB',
    COALESCE(p_canonical->>'confidence', 'Confirmed'),
    p_canonical->>'notes'
  )
  ON CONFLICT (party_id, period_label, source_code) DO UPDATE SET
    period_end             = EXCLUDED.period_end,
    period_type            = EXCLUDED.period_type,
    revenue_eur_m          = EXCLUDED.revenue_eur_m,
    ebitda_eur_m           = EXCLUDED.ebitda_eur_m,
    ebit_eur_m             = EXCLUDED.ebit_eur_m,
    net_income_eur_m       = EXCLUDED.net_income_eur_m,
    total_assets_eur_m     = EXCLUDED.total_assets_eur_m,
    total_equity_eur_m     = EXCLUDED.total_equity_eur_m,
    cash_eur_m             = EXCLUDED.cash_eur_m,
    total_debt_eur_m       = EXCLUDED.total_debt_eur_m,
    net_debt_eur_m         = EXCLUDED.net_debt_eur_m,
    working_capital_eur_m  = EXCLUDED.working_capital_eur_m,
    employees              = EXCLUDED.employees,
    amount_currency        = EXCLUDED.amount_currency,
    fx_rate_to_eur         = EXCLUDED.fx_rate_to_eur,
    fx_date                = EXCLUDED.fx_date,
    nbb_model_type         = EXCLUDED.nbb_model_type,
    nbb_filing_date        = EXCLUDED.nbb_filing_date,
    fiscal_year_start      = EXCLUDED.fiscal_year_start,
    fiscal_year_end        = EXCLUDED.fiscal_year_end,
    confidence             = EXCLUDED.confidence,
    notes                  = EXCLUDED.notes,
    updated_at             = now()
  RETURNING financial_id INTO v_fact_id;

  -- 5. Mark our filing as parsed and link to fact
  UPDATE public._stg_nbb_filings
  SET parse_status      = 'parsed',
      parse_error       = NULL,
      parsed_at         = now(),
      fact_financial_id = v_fact_id
  WHERE filing_id = p_filing_id;

  RETURN jsonb_build_object(
    'outcome', 'parsed',
    'filing_id', p_filing_id,
    'fact_financial_id', v_fact_id,
    'demoted_older_filings', v_demoted
  );
END;
$$;

COMMENT ON FUNCTION public.fn_promote_nbb_filing(uuid, jsonb) IS
'Lane B canonical promotion: moves a _stg_nbb_filings row to fact_financials with conflict-resolution. Latest filing_date wins per (kbo_nr, fiscal_year_end); older parsed filings demoted to ''superseded''. UPSERT op (party_id, period_label, source_code=SRC_NBB). Worker passes parsed metrics in p_canonical jsonb. Returns outcome jsonb (parsed|superseded). SECURITY DEFINER — pipeline-only, never exposed to anon/authenticated.';

GRANT EXECUTE ON FUNCTION public.fn_promote_nbb_filing(uuid, jsonb) TO service_role;

-- ----- Refresh comment on fact_financials.source_code ------------------------
COMMENT ON COLUMN public.fact_financials.source_code IS
'Source code of this fact. SRC_NBB = Belgian Central Balance Sheet. Two ingestion lanes: Lane A (CBSO JSON-XBRL API, 3-4 jaar history per entity, live via gs_enrichment worker_type=nbb_financials), Lane B (CBSO consult bulk-XBRL ZIP, 10+ jaar history, via _stg_nbb_filings → fn_promote_nbb_filing). SRC_PB = PitchBook (longer history, lower granularity on BE entities). SRC_NBB wins on conflict for BE. FK → ref_sources(source_code).';

-- ----- Changelog -------------------------------------------------------------
INSERT INTO public.changelog (change_type, version, description, affected_tables)
VALUES (
  'function',
  'GS-MIGRATE-LB-002',
  'Lane B canonical promotion: fn_promote_nbb_filing(uuid, jsonb). Encodes "latest filing_date wins per (kbo_nr, fiscal_year_end)" doctrine in SQL. UPSERT into fact_financials with full conflict-resolution. Comment on fact_financials.source_code refreshed to document both Lane A and Lane B paths.',
  ARRAY['_stg_nbb_filings','fact_financials']
);

COMMIT;

-- ============================================================================
-- PART 2: VERIFY
-- ============================================================================
SELECT
  p.proname,
  pg_get_function_identity_arguments(p.oid) AS args,
  pg_get_function_result(p.oid)              AS returns,
  CASE WHEN p.prosecdef THEN 'DEFINER' ELSE 'INVOKER' END AS security_type,
  obj_description(p.oid)                     AS function_comment_present
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proname = 'fn_promote_nbb_filing';

SELECT has_function_privilege(
  'service_role',
  'public.fn_promote_nbb_filing(uuid, jsonb)',
  'EXECUTE'
) AS service_role_can_execute;

SELECT col_description(
  'public.fact_financials'::regclass,
  (SELECT attnum FROM pg_attribute
    WHERE attrelid='public.fact_financials'::regclass AND attname='source_code')
) AS source_code_comment;

SELECT version, change_type, change_date
FROM public.changelog
WHERE version = 'GS-MIGRATE-LB-002';

-- ============================================================================
-- PART 3: ROLLBACK  (commented out — run manually if needed)
-- ============================================================================
/*
BEGIN;

DROP FUNCTION IF EXISTS public.fn_promote_nbb_filing(uuid, jsonb);

COMMENT ON COLUMN public.fact_financials.source_code IS
'Source code of this fact. SRC_NBB = Belgian Central Balance Sheet via NBB CBSO JSON-XBRL API (max 3-4 years per entity; deeper history via ZIP-download path not yet wired). SRC_PB = PitchBook (longer history, lower granularity on BE entities). SRC_NBB wins on conflict for BE. FK → ref_sources(source_code).';

DELETE FROM public.changelog WHERE version = 'GS-MIGRATE-LB-002';

COMMIT;
*/
