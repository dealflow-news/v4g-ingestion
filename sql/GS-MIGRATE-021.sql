-- ═══════════════════════════════════════════════════════════════════════════
-- GS-MIGRATE-021 · gs_enrichment schema for V4G enrichment platform
-- ═══════════════════════════════════════════════════════════════════════════
-- Rationale: V4G has been quietly building component tools (v4g_accounts NBB
--   pipeline, kbo-proxy Edge Function, entity_linker dedup, Ingestion Platform)
--   that need to be unified into an enrichment platform. This migration lays
--   the foundation: a queue for pending enrichment tasks, a policy table
--   documenting what triggers enrichment, and a 3-tier audit log (run → object
--   → step).
-- Scope: schema + tables + seed policies + manual enqueue function +
--   cadence-sweep function. No event-triggers on source tables yet — those
--   come in GS-MIGRATE-022 once we have a worker-path end-to-end verified.
-- Owner: Chris · V4G Data Ops
-- Prior: GS-MIGRATE-018 (dim_actor_types read), 019 (read grants), 020 (RPCs)

BEGIN;

-- ─── PART 1: SCHEMA ────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS gs_enrichment;
COMMENT ON SCHEMA gs_enrichment IS
    'V4G enrichment platform: queue, policies, and 3-tier audit (run/object/step) '
    'for automated party enrichment (NBB financials, KBO directors, '
    'actor classification). Canonical home for orchestration state.';


-- ─── PART 2: POLICY TABLE ──────────────────────────────────────────────────
-- Documents WHAT triggers enrichment. Does not itself execute triggers —
-- those live in GS-MIGRATE-022 as actual AFTER INSERT triggers on source
-- tables, reading config from here.

CREATE TABLE gs_enrichment.policy (
    policy_id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    policy_code       text NOT NULL UNIQUE,
    description       text NOT NULL,
    trigger_kind      text NOT NULL,
    is_active         boolean NOT NULL DEFAULT true,
    target_types      text[] NOT NULL,
    config            jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT policy_trigger_kind_check
        CHECK (trigger_kind IN ('event', 'cadence', 'manual')),
    CONSTRAINT policy_target_types_nonempty
        CHECK (array_length(target_types, 1) >= 1)
);

COMMENT ON TABLE gs_enrichment.policy IS
    'Policies that govern when a party should be enriched. trigger_kind: '
    '''event'' = reactive (triggered by activity on source tables), '
    '''cadence'' = proactive (periodic sweep), ''manual'' = enqueued by hand. '
    'target_types = which enrichment_type(s) to enqueue when policy fires.';
COMMENT ON COLUMN gs_enrichment.policy.config IS
    'Free-form policy parameters. Examples: {"threshold": 0.7} for event '
    'materiality, {"stale_days": 90} for cadence.';


-- ─── PART 3: QUEUE TABLE ───────────────────────────────────────────────────

CREATE TABLE gs_enrichment.queue (
    queue_id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    party_id                 uuid NOT NULL REFERENCES public.party_registry(party_id),
    enrichment_type          text NOT NULL,
    triggered_by_policy_code text REFERENCES gs_enrichment.policy(policy_code),
    trigger_payload          jsonb DEFAULT '{}'::jsonb,
    status                   text NOT NULL DEFAULT 'pending',
    priority                 integer NOT NULL DEFAULT 100,
    attempts                 integer NOT NULL DEFAULT 0,
    run_id                   uuid,
    enqueued_at              timestamptz NOT NULL DEFAULT now(),
    started_at               timestamptz,
    finished_at              timestamptz,
    last_error               text,
    CONSTRAINT queue_status_check
        CHECK (status IN ('pending', 'running', 'done', 'failed', 'skipped')),
    CONSTRAINT queue_enrichment_type_check
        CHECK (enrichment_type IN (
            'nbb_financials', 'kbo_directors', 'actor_classification',
            'profile_completion'
        ))
);

-- Prevent double-enqueue of pending/running tasks for the same party+type
CREATE UNIQUE INDEX uq_queue_active_task
    ON gs_enrichment.queue (party_id, enrichment_type)
    WHERE status IN ('pending', 'running');

CREATE INDEX ix_queue_status_priority
    ON gs_enrichment.queue (status, priority, enqueued_at)
    WHERE status IN ('pending', 'running');

CREATE INDEX ix_queue_party
    ON gs_enrichment.queue (party_id);

COMMENT ON TABLE gs_enrichment.queue IS
    'Individual enrichment tasks. One row per (party_id, enrichment_type, '
    'lifecycle). Partial unique index prevents double-enqueue of active tasks.';


-- ─── PART 4: RUN-LEVEL LOG ─────────────────────────────────────────────────

CREATE TABLE gs_enrichment.run_log (
    run_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    worker_type     text NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    status          text NOT NULL DEFAULT 'running',
    tasks_total     integer NOT NULL DEFAULT 0,
    tasks_ok        integer NOT NULL DEFAULT 0,
    tasks_failed    integer NOT NULL DEFAULT 0,
    tasks_skipped   integer NOT NULL DEFAULT 0,
    host            text,
    app_version     text,
    summary         jsonb DEFAULT '{}'::jsonb,
    error_summary   text,
    CONSTRAINT run_log_status_check
        CHECK (status IN ('running', 'completed', 'failed', 'aborted'))
);

CREATE INDEX ix_run_log_started ON gs_enrichment.run_log (started_at DESC);
CREATE INDEX ix_run_log_status  ON gs_enrichment.run_log (status, started_at DESC);

COMMENT ON TABLE gs_enrichment.run_log IS
    'Run-level audit: one row per invocation of a worker, regardless of how '
    'many tasks it processes. Chris Q2 answer, level 1.';


-- ─── PART 5: OBJECT-LEVEL LOG ──────────────────────────────────────────────

CREATE TABLE gs_enrichment.object_log (
    object_log_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id           uuid NOT NULL REFERENCES gs_enrichment.run_log(run_id) ON DELETE CASCADE,
    party_id         uuid NOT NULL REFERENCES public.party_registry(party_id),
    enrichment_type  text NOT NULL,
    outcome          text NOT NULL,
    rows_written     integer NOT NULL DEFAULT 0,
    change_summary   jsonb DEFAULT '{}'::jsonb,
    error            text,
    duration_ms      integer,
    logged_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT object_log_outcome_check
        CHECK (outcome IN ('ok', 'failed', 'skipped', 'no_change'))
);

CREATE INDEX ix_object_log_run        ON gs_enrichment.object_log (run_id);
CREATE INDEX ix_object_log_party_type ON gs_enrichment.object_log (party_id, enrichment_type, logged_at DESC);
CREATE INDEX ix_object_log_outcome    ON gs_enrichment.object_log (outcome, logged_at DESC);

COMMENT ON TABLE gs_enrichment.object_log IS
    'Object-level audit: what happened to each (party_id, enrichment_type) '
    'within a run. Always written. Chris Q2 answer, level 2. change_summary '
    'holds table→row_count deltas, e.g. {"fact_financials": 10}.';


-- ─── PART 6: STEP-LEVEL LOG (failure/debug only) ───────────────────────────

CREATE TABLE gs_enrichment.step_log (
    step_log_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    object_log_id  uuid REFERENCES gs_enrichment.object_log(object_log_id) ON DELETE CASCADE,
    run_id         uuid REFERENCES gs_enrichment.run_log(run_id) ON DELETE CASCADE,
    step_name      text NOT NULL,
    step_index     integer,
    level          text NOT NULL,
    message        text,
    context        jsonb DEFAULT '{}'::jsonb,
    logged_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT step_log_level_check
        CHECK (level IN ('debug', 'info', 'warning', 'error'))
);

CREATE INDEX ix_step_log_run        ON gs_enrichment.step_log (run_id, level, logged_at);
CREATE INDEX ix_step_log_object     ON gs_enrichment.step_log (object_log_id);
CREATE INDEX ix_step_log_errors
    ON gs_enrichment.step_log (run_id, logged_at DESC)
    WHERE level = 'error';

COMMENT ON TABLE gs_enrichment.step_log IS
    'Step-level audit: internal transformations and API calls. Only written '
    'on failure or when worker is in debug mode. Chris Q2 answer, level 3. '
    'The partial error-index supports cheap "show me errors for run X".';


-- ─── PART 7: SEED POLICIES ─────────────────────────────────────────────────
-- The 4 policies Chris described. Triggers are not yet wired (→ GS-MIGRATE-022).

INSERT INTO gs_enrichment.policy (policy_code, description, trigger_kind, target_types, config)
VALUES
    ('signal_material',
     'Material signal observation arrives for party. Materiality defined by '
     'significance_score threshold in party_signal_observations.',
     'event',
     ARRAY['nbb_financials', 'kbo_directors', 'actor_classification'],
     '{"significance_threshold": 0.7}'::jsonb),

    ('canonical_match_upgrade',
     'Party is newly linked to a confirmed canonical deal. Valuable signal '
     'that this party is deal-relevant.',
     'event',
     ARRAY['nbb_financials', 'kbo_directors'],
     '{"match_status_targets": ["confirmed", "auto_matched"]}'::jsonb),

    ('alert_threshold',
     'fact_alert crosses severity threshold for party. Implies active '
     'surveillance interest.',
     'event',
     ARRAY['nbb_financials', 'actor_classification'],
     '{"severity_threshold": "high"}'::jsonb),

    ('cadence_stale',
     'High-priority (P1/P2) party has no successful enrichment in the '
     'configured stale window. Periodic sweep re-enqueues.',
     'cadence',
     ARRAY['nbb_financials', 'kbo_directors'],
     '{"tier_whitelist": ["P1", "P2"], "stale_days": 90}'::jsonb),

    ('manual',
     'Operator-triggered enqueue via CLI, admin UI, or direct INSERT. '
     'Bypasses all policy gates.',
     'manual',
     ARRAY['nbb_financials', 'kbo_directors', 'actor_classification', 'profile_completion'],
     '{}'::jsonb);


-- ─── PART 8: HELPER FUNCTIONS ──────────────────────────────────────────────

-- Manual enqueue — used by CLI, admin UI, and by GS-MIGRATE-022 event triggers.
CREATE OR REPLACE FUNCTION gs_enrichment.enqueue(
    p_party_id           uuid,
    p_enrichment_types   text[] DEFAULT ARRAY['nbb_financials'],
    p_policy_code        text DEFAULT 'manual',
    p_trigger_payload    jsonb DEFAULT '{}'::jsonb,
    p_priority           integer DEFAULT 100
) RETURNS SETOF gs_enrichment.queue
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = gs_enrichment, public, pg_temp
AS $$
BEGIN
    -- Validate party exists
    IF NOT EXISTS (SELECT 1 FROM public.party_registry WHERE party_id = p_party_id) THEN
        RAISE EXCEPTION 'Party not found: %', p_party_id USING ERRCODE = 'P0002';
    END IF;

    -- Validate policy exists and is active
    IF NOT EXISTS (
        SELECT 1 FROM gs_enrichment.policy
        WHERE policy_code = p_policy_code AND is_active = true
    ) THEN
        RAISE EXCEPTION 'Unknown or inactive policy: %', p_policy_code
            USING ERRCODE = 'P0002';
    END IF;

    -- Insert one row per type; skip duplicates (pending+running already exists)
    RETURN QUERY
    INSERT INTO gs_enrichment.queue (
        party_id, enrichment_type, triggered_by_policy_code,
        trigger_payload, priority
    )
    SELECT p_party_id, et, p_policy_code, p_trigger_payload, p_priority
    FROM unnest(p_enrichment_types) AS et
    ON CONFLICT DO NOTHING
    RETURNING *;
END;
$$;

COMMENT ON FUNCTION gs_enrichment.enqueue(uuid, text[], text, jsonb, integer) IS
    'Enqueue one or more enrichment tasks for a party. Idempotent: already-'
    'active tasks (pending/running) are silently skipped. Returns inserted rows.';

GRANT EXECUTE ON FUNCTION gs_enrichment.enqueue(uuid, text[], text, jsonb, integer) TO anon;


-- Cadence sweep — called by cron (Render cron or pg_cron later)
CREATE OR REPLACE FUNCTION gs_enrichment.sweep_stale_parties()
RETURNS TABLE(enqueued_count integer, policy_code text)
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = gs_enrichment, public, pg_temp
AS $$
DECLARE
    v_policy     gs_enrichment.policy;
    v_stale_days integer;
    v_tiers      text[];
    v_count      integer;
BEGIN
    -- Fetch the active cadence_stale policy
    SELECT * INTO v_policy
    FROM gs_enrichment.policy
    WHERE policy_code = 'cadence_stale' AND is_active = true;

    IF NOT FOUND THEN
        RETURN;  -- policy disabled → no-op
    END IF;

    v_stale_days := COALESCE((v_policy.config->>'stale_days')::integer, 90);
    v_tiers      := COALESCE(
        ARRAY(SELECT jsonb_array_elements_text(v_policy.config->'tier_whitelist')),
        ARRAY['P1','P2']
    );

    -- For each target enrichment_type configured on the policy,
    -- enqueue parties that haven't had a successful run in the stale window.
    RETURN QUERY
    WITH enqueue_results AS (
        INSERT INTO gs_enrichment.queue (
            party_id, enrichment_type, triggered_by_policy_code,
            trigger_payload, priority
        )
        SELECT pr.party_id, et, 'cadence_stale',
               jsonb_build_object('stale_days', v_stale_days),
               200  -- lower priority than event-driven
        FROM public.party_registry pr
        CROSS JOIN unnest(v_policy.target_types) AS et
        WHERE pr.enrichment_tier = ANY(v_tiers)
          AND pr.status = 'Active'
          AND NOT EXISTS (
              SELECT 1 FROM gs_enrichment.object_log ol
              WHERE ol.party_id = pr.party_id
                AND ol.enrichment_type = et
                AND ol.outcome = 'ok'
                AND ol.logged_at > now() - (v_stale_days || ' days')::interval
          )
        ON CONFLICT DO NOTHING
        RETURNING enrichment_type
    )
    SELECT count(*)::integer, 'cadence_stale'::text
    FROM enqueue_results;
END;
$$;

COMMENT ON FUNCTION gs_enrichment.sweep_stale_parties() IS
    'Cadence sweep: finds high-priority parties without recent successful '
    'enrichment and enqueues them. Called by scheduler (Render cron or pg_cron).';

GRANT EXECUTE ON FUNCTION gs_enrichment.sweep_stale_parties() TO anon;


-- ─── PART 9: GRANTS ────────────────────────────────────────────────────────
-- Service_role server-side has full access via ownership.
-- Anon gets SELECT on read-only tables (queue, logs) so browser UIs can
-- display status, but writes go exclusively via SECURITY DEFINER functions.

GRANT USAGE ON SCHEMA gs_enrichment TO anon;
GRANT SELECT ON gs_enrichment.policy     TO anon;
GRANT SELECT ON gs_enrichment.queue      TO anon;
GRANT SELECT ON gs_enrichment.run_log    TO anon;
GRANT SELECT ON gs_enrichment.object_log TO anon;
GRANT SELECT ON gs_enrichment.step_log   TO anon;


-- ─── PART 10: CHANGELOG + GOVERNANCE ───────────────────────────────────────

INSERT INTO public.changelog
    (version, author, change_type, description, affected_tables, breaking)
VALUES (
    'GS-MIGRATE-021', 'Chris', 'schema_add',
    'Added gs_enrichment schema: policy (5 seed rows), queue, run_log, '
    'object_log, step_log. Helper functions: enqueue(), sweep_stale_parties(). '
    'Foundation for V4G enrichment platform that absorbs v4g_accounts (NBB), '
    'kbo-proxy Edge Function (KBO SOAP), and actor-classification logic into '
    'one event-driven + cadence-driven enrichment runner.',
    ARRAY['gs_enrichment.policy','gs_enrichment.queue','gs_enrichment.run_log',
          'gs_enrichment.object_log','gs_enrichment.step_log'],
    false
);

INSERT INTO gs_governance.schema_register
    (object_type, schema_name, object_name,
     issue_type, severity, description, recommended_action,
     status, owner, decision, decision_at,
     first_snapshot_id, last_snapshot_id, seen_count, source)
VALUES (
    'schema', 'gs_enrichment', 'enrichment_platform_foundation',
    'new_schema', 'info',
    'Foundation schema for V4G enrichment platform. Queue + 3-tier audit + '
    'policy table. Event-triggers on source tables (party_signal_observations, '
    'canonical_deals, fact_alert) deferred to GS-MIGRATE-022.',
    'Build worker runner + first enrichment_type (nbb_financials) against '
    'this schema. Once verified end-to-end, add event-triggers.',
    'in_progress', 'Chris',
    'Phase 1 of V4G enrichment platform. GS-MIGRATE-022 will wire auto-triggers.',
    now(),
    'MANUAL_20260420', 'MANUAL_20260420', 1, 'manual'
);


-- ─── PART 11: VERIFY ──────────────────────────────────────────────────────

-- Expect 5 tables in gs_enrichment
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'gs_enrichment' ORDER BY table_name;
-- Expected: object_log, policy, queue, run_log, step_log

-- Expect 5 seed policies
SELECT policy_code, trigger_kind, is_active, target_types
FROM gs_enrichment.policy ORDER BY policy_code;
-- Expected: 5 rows (alert_threshold, cadence_stale, canonical_match_upgrade,
--                   manual, signal_material)

-- Expect 2 functions with anon EXECUTE
SELECT p.proname,
       EXISTS (
         SELECT 1 FROM information_schema.routine_privileges rp
         WHERE rp.routine_schema = 'gs_enrichment'
           AND rp.routine_name = p.proname
           AND rp.grantee = 'anon'
           AND rp.privilege_type = 'EXECUTE'
       ) AS anon_can_execute
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'gs_enrichment'
ORDER BY p.proname;
-- Expected: 2 rows: enqueue, sweep_stale_parties, both true

-- Smoke test: manual enqueue for V4G itself (using canonical party_id)
-- Uncomment to test (idempotent, re-run safe):
-- SELECT * FROM gs_enrichment.enqueue(
--     '38cff812-397f-5fb4-bf18-a0e8b42b2a69'::uuid,   -- V4G canonical party
--     ARRAY['nbb_financials'],
--     'manual'
-- );

COMMIT;

-- ═══════════════════════════════════════════════════════════════════════════
-- ROLLBACK (run manually if needed — commented)
-- ═══════════════════════════════════════════════════════════════════════════
-- BEGIN;
-- DROP SCHEMA IF EXISTS gs_enrichment CASCADE;
-- DELETE FROM public.changelog WHERE version = 'GS-MIGRATE-021';
-- DELETE FROM gs_governance.schema_register
--   WHERE object_name = 'enrichment_platform_foundation';
-- COMMIT;
