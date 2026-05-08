-- ============================================================================
-- GS-MIGRATE-LB-001 · Lane B staging: _stg_nbb_filings
-- (Re-numbered from initial GS-MIGRATE-023 due to version-collision with the
--  2026-03-12 fact_participations migration. Cleanup-DELETE+INSERT executed
--  live; this file is the canonical reference for the LB-001 schema.)
-- ----------------------------------------------------------------------------
-- Purpose:  Persistent raw-storage voor NBB XBRL filings uit de CBSO bulk-
--           download (consult.cbso.nbb.be). Geeft fact_financials toegang
--           tot 10+ jaar fiscal history per entity (vs ~3-4 jaar via Lane A
--           JSON API).
--
-- Doctrine: Idempotent op filing_reference. Format-aware (pfs-old vs
--           cbso-new). Conflict-resolutie per (kbo_nr, fiscal_year_end):
--           hoogste filing_date wint en wordt naar fact_financials
--           gepromoot, oudere filings krijgen parse_status='superseded'.
--
-- SOP-009 deviation: We houden een nullable FK naar fact_financials voor
--           audit-trail (raw XBRL → canonical row). Strikt genomen wijkt
--           dit af van de "staging is transient, no FK" default, maar
--           deze tabel is geen transient landing — het is persistente
--           raw layer (we behouden bytes voor reprocessing). Documented.
-- ============================================================================

-- ============================================================================
-- PART 1: MIGRATION
-- ============================================================================
BEGIN;

CREATE TABLE IF NOT EXISTS public._stg_nbb_filings (
  filing_id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  -- entity link
  party_id            uuid NOT NULL REFERENCES public.party_registry(party_id),
  kbo_nr              text NOT NULL,

  -- NBB filing identity
  filing_reference    text NOT NULL,
  filing_date         date,
  filing_year         integer,

  -- fiscal period (from XBRL <period>, NOT from filename)
  fiscal_year_end     date NOT NULL,
  fiscal_year_start   date,

  -- format & schema
  taxonomy_format     text NOT NULL
                        CHECK (taxonomy_format IN ('pfs-old','cbso-new')),
  nbb_model_type      text,    -- m01 verkort | m02 volledig | m03 micro

  -- raw payload + integrity
  raw_xbrl            text NOT NULL,
  raw_xbrl_sha256     text NOT NULL,
  source_filename     text,    -- e.g. "2025-00231176.xbrl"
  source_zip_name     text,    -- e.g. "Export_consult_xbrl_20260508091537.zip"

  -- lifecycle
  parse_status        text NOT NULL DEFAULT 'pending'
                        CHECK (parse_status IN ('pending','parsed','failed','superseded','skipped')),
  parse_error         text,
  parsed_at           timestamptz,

  -- audit back-link to canonical fact (nullable until parsed)
  fact_financial_id   uuid REFERENCES public.fact_financials(financial_id),

  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT uq_stg_nbb_filing_reference
    UNIQUE (filing_reference),                        -- idempotency: re-upload is no-op
  CONSTRAINT uq_stg_nbb_kbo_fy_filing_date
    UNIQUE (kbo_nr, fiscal_year_end, filing_date)     -- mathematische unicity in NBB
);

-- ----- Comments (SOP-002 mandatory) ------------------------------------------
COMMENT ON TABLE public._stg_nbb_filings IS
'[STAGING] Raw NBB XBRL filings van CBSO consult-bulk-download (Lane B). Persistente raw layer voor 10+ jaar fiscal history per entity. Idempotent op filing_reference. Format-aware: pfs-old (2007-2021) vs cbso-new (2022+). Conflict-resolutie per (kbo_nr, fiscal_year_end): hoogste filing_date wint en wordt naar fact_financials gepromoot via fn_promote_nbb_filing (zie GS-MIGRATE-LB-002). Atypische staging: behoudt bytes voor reprocessing → nullable FK naar fact_financials voor audit-trail. RLS enabled (service_role only).';

COMMENT ON COLUMN public._stg_nbb_filings.filing_reference IS
'NBB filing reference (bv. "2025-00231176"). Globaal uniek in NBB. Primaire idempotency-key: re-upload van een ZIP met dezelfde filing is een no-op.';

COMMENT ON COLUMN public._stg_nbb_filings.filing_date IS
'Depot-datum uit XBRL. Bepaalt "winner" bij meerdere filings voor zelfde fiscal year (latest wins).';

COMMENT ON COLUMN public._stg_nbb_filings.filing_year IS
'Year-prefix uit filing_reference. NIET gelijk aan fiscal_year — een 2014-deposit kan een fiscal year 2012 amendment zijn.';

COMMENT ON COLUMN public._stg_nbb_filings.fiscal_year_end IS
'Fiscal year end uit XBRL <period>. NOOIT uit filename — filename pattern <filing_year>-<reference>.xbrl is misleidend.';

COMMENT ON COLUMN public._stg_nbb_filings.taxonomy_format IS
'XBRL namespace family: "pfs-old" voor 2007-2021 filings (xmlns:pfs=nbb.be/.../pfs/), "cbso-new" voor 2022+ filings (xmlns:bas=nbb.be/.../cbso/). Bepaalt parser dispatch in nbb_xbrl_parse worker.';

COMMENT ON COLUMN public._stg_nbb_filings.nbb_model_type IS
'NBB depot model: m01 (verkort), m02 (volledig), m03 (micro). Bepaalt welke BAS_MAP rubrieken aanwezig zijn.';

COMMENT ON COLUMN public._stg_nbb_filings.raw_xbrl_sha256 IS
'SHA-256 van raw_xbrl bytes. Detecteert content-changes als NBB ooit een filing silent re-issued onder dezelfde reference.';

COMMENT ON COLUMN public._stg_nbb_filings.parse_status IS
'Lifecycle: pending → parsed (canonical row in fact_financials) | failed (parse error in parse_error) | superseded (newer filing voor zelfde fiscal year wint) | skipped (intentioneel niet gepromoot, bv. corrupt of out-of-scope).';

COMMENT ON COLUMN public._stg_nbb_filings.fact_financial_id IS
'FK naar fact_financials.financial_id wanneer parse_status=parsed. Nullable tijdens pending/failed/superseded. Audit-trail van canonical fact terug naar bron-XBRL.';

-- ----- Indexes ---------------------------------------------------------------
CREATE INDEX IF NOT EXISTS ix_stg_nbb_filings_party_fy
  ON public._stg_nbb_filings (party_id, fiscal_year_end DESC);

CREATE INDEX IF NOT EXISTS ix_stg_nbb_filings_kbo_fy
  ON public._stg_nbb_filings (kbo_nr, fiscal_year_end);

CREATE INDEX IF NOT EXISTS ix_stg_nbb_filings_pending
  ON public._stg_nbb_filings (created_at)
  WHERE parse_status = 'pending';

CREATE INDEX IF NOT EXISTS ix_stg_nbb_filings_failed
  ON public._stg_nbb_filings (created_at)
  WHERE parse_status = 'failed';

-- ----- updated_at trigger ----------------------------------------------------
CREATE OR REPLACE FUNCTION public.fn_stg_nbb_filings_set_updated()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

COMMENT ON FUNCTION public.fn_stg_nbb_filings_set_updated() IS
'Stamps updated_at on every UPDATE to _stg_nbb_filings.';

DROP TRIGGER IF EXISTS trg_stg_nbb_filings_updated ON public._stg_nbb_filings;
CREATE TRIGGER trg_stg_nbb_filings_updated
  BEFORE UPDATE ON public._stg_nbb_filings
  FOR EACH ROW EXECUTE FUNCTION public.fn_stg_nbb_filings_set_updated();

-- ----- RLS + grants (Stage 2 lockdown doctrine) ------------------------------
ALTER TABLE public._stg_nbb_filings ENABLE ROW LEVEL SECURITY;
-- No policies: service_role bypasses RLS (BYPASSRLS attribute), anon and
-- authenticated have no access. Internal pipeline state — never client-facing.

GRANT ALL ON TABLE public._stg_nbb_filings TO service_role;
GRANT EXECUTE ON FUNCTION public.fn_stg_nbb_filings_set_updated() TO service_role;

-- ----- Changelog -------------------------------------------------------------
INSERT INTO public.changelog (change_type, version, description, affected_tables)
VALUES (
  'schema_add',
  'GS-MIGRATE-LB-001',
  'Lane B staging: _stg_nbb_filings table for bulk NBB XBRL ingestion. Stores raw XBRL + parsed metadata per filing. Idempotent on filing_reference. Format-aware (pfs-old | cbso-new). Will feed fact_financials via fn_promote_nbb_filing (GS-MIGRATE-LB-002). Closes the "ZIP-download path not yet wired" caveat on fact_financials.source_code comment.',
  ARRAY['_stg_nbb_filings']
);

COMMIT;

-- ============================================================================
-- PART 2: VERIFY
-- ============================================================================
-- Expected:
--   _stg_nbb_filings exists, rls_enabled=true, policies=0,
--   indexes=5 (1 PK + 4 explicit), user_triggers=1
SELECT
  c.relname,
  c.relrowsecurity AS rls_enabled,
  (SELECT count(*) FROM pg_policies
     WHERE schemaname='public' AND tablename='_stg_nbb_filings')          AS policies,
  (SELECT count(*) FROM pg_indexes
     WHERE schemaname='public' AND tablename='_stg_nbb_filings')          AS indexes,
  (SELECT count(*) FROM pg_trigger
     WHERE tgrelid = c.oid AND NOT tgisinternal)                          AS user_triggers,
  obj_description(c.oid)                                                  AS table_comment_present
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = '_stg_nbb_filings';

-- Expected: 8 column comments (table-level + 7 column-level documented above)
SELECT
  a.attname AS column_name,
  col_description(a.attrelid, a.attnum) AS column_comment
FROM pg_attribute a
WHERE a.attrelid = 'public._stg_nbb_filings'::regclass
  AND a.attnum > 0
  AND NOT a.attisdropped
  AND col_description(a.attrelid, a.attnum) IS NOT NULL
ORDER BY a.attnum;

-- Expected: 1 row, change_type='schema_add'
SELECT version, change_type, change_date, affected_tables
FROM public.changelog
WHERE version = 'GS-MIGRATE-LB-001';

-- Expected: 1 row, all_grants_present=true
SELECT
  has_table_privilege('service_role','public._stg_nbb_filings','SELECT, INSERT, UPDATE, DELETE') AS all_grants_present;

-- ============================================================================
-- PART 3: ROLLBACK  (commented out — run manually if needed)
-- ============================================================================
/*
BEGIN;

DROP TRIGGER IF EXISTS trg_stg_nbb_filings_updated ON public._stg_nbb_filings;
DROP FUNCTION IF EXISTS public.fn_stg_nbb_filings_set_updated();
DROP TABLE IF EXISTS public._stg_nbb_filings CASCADE;

DELETE FROM public.changelog WHERE version = 'GS-MIGRATE-LB-001';

COMMIT;
*/
