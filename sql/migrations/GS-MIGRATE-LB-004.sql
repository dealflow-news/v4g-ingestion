-- ============================================================================
-- GS-MIGRATE-LB-004 · Lane B trigger: auto-enqueue on staging arrival
-- ----------------------------------------------------------------------------
-- AFTER INSERT trigger op _stg_nbb_filings dat gs_enrichment.enqueue() roept
-- met enrichment_type='nbb_xbrl_parse' en policy_code='nbb_xbrl_staging_arrival'.
-- Skipt rijen die niet in 'pending' state aankomen (backfill safety).
-- ============================================================================

BEGIN;

CREATE OR REPLACE FUNCTION public.fn_stg_nbb_filing_enqueue()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, gs_enrichment, pg_temp
AS $$
BEGIN
  IF NEW.parse_status = 'pending' THEN
    PERFORM gs_enrichment.enqueue(
      p_party_id          := NEW.party_id,
      p_enrichment_types  := ARRAY['nbb_xbrl_parse'],
      p_policy_code       := 'nbb_xbrl_staging_arrival',
      p_trigger_payload   := jsonb_build_object(
        'filing_id',         NEW.filing_id,
        'kbo_nr',            NEW.kbo_nr,
        'fiscal_year_end',   NEW.fiscal_year_end,
        'taxonomy_format',   NEW.taxonomy_format,
        'filing_reference',  NEW.filing_reference
      ),
      p_priority          := 100
    );
  END IF;
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.fn_stg_nbb_filing_enqueue() IS
'Trigger function: AFTER INSERT op _stg_nbb_filings → gs_enrichment.enqueue met enrichment_type=nbb_xbrl_parse, policy_code=nbb_xbrl_staging_arrival. Trigger payload bevat filing_id (worker''s primary key), plus kbo_nr/fiscal_year_end/taxonomy_format/filing_reference voor logging-context. Skipt non-pending inserts (backfill safety).';

GRANT EXECUTE ON FUNCTION public.fn_stg_nbb_filing_enqueue() TO service_role;

DROP TRIGGER IF EXISTS trg_stg_nbb_filing_enqueue ON public._stg_nbb_filings;
CREATE TRIGGER trg_stg_nbb_filing_enqueue
  AFTER INSERT ON public._stg_nbb_filings
  FOR EACH ROW EXECUTE FUNCTION public.fn_stg_nbb_filing_enqueue();

INSERT INTO public.changelog (change_type, version, description, affected_tables)
VALUES (
  'function',
  'GS-MIGRATE-LB-004',
  'Lane B auto-enqueue: AFTER INSERT trigger trg_stg_nbb_filing_enqueue op _stg_nbb_filings calls fn_stg_nbb_filing_enqueue → gs_enrichment.enqueue (policy: nbb_xbrl_staging_arrival, type: nbb_xbrl_parse). Skipt non-pending inserts.',
  ARRAY['_stg_nbb_filings']
);

COMMIT;

-- VERIFY
SELECT t.tgname, pg_get_triggerdef(t.oid) AS trigger_def
FROM pg_trigger t
JOIN pg_class c ON c.oid = t.tgrelid
WHERE c.relname = '_stg_nbb_filings' AND NOT t.tgisinternal;

SELECT version, change_type, change_date FROM public.changelog WHERE version='GS-MIGRATE-LB-004';

-- ROLLBACK
/*
BEGIN;
DROP TRIGGER IF EXISTS trg_stg_nbb_filing_enqueue ON public._stg_nbb_filings;
DROP FUNCTION IF EXISTS public.fn_stg_nbb_filing_enqueue();
DELETE FROM public.changelog WHERE version='GS-MIGRATE-LB-004';
COMMIT;
*/
