-- ═══════════════════════════════════════════════════════════════════════════
-- GS-MIGRATE-022a · Claim/complete RPCs for enrichment runner
-- ═══════════════════════════════════════════════════════════════════════════
-- Purpose: close the claim-and-dispatch loop in runner.process_batch().
--   Introduces two SECURITY DEFINER functions that atomically transition
--   queue rows through their lifecycle with concurrency-safe locking.
-- Scope: functions only. No table/column changes. No RLS changes.
-- Prior: GS-MIGRATE-021 (gs_enrichment foundation)
-- Owner: Chris · V4G Data Ops

BEGIN;

-- ─── PART 1: fn_claim_enrichment_task ──────────────────────────────────────
-- Atomically claims the oldest pending task of a given worker_type,
-- transitions it to 'running', attaches run_id, increments attempts.
-- Uses FOR UPDATE SKIP LOCKED so multiple runner processes can poll in
-- parallel without blocking each other or double-claiming.

CREATE OR REPLACE FUNCTION gs_enrichment.fn_claim_enrichment_task(
    p_worker_type text,
    p_run_id      uuid
)
RETURNS TABLE (
    queue_id                 uuid,
    party_id                 uuid,
    enrichment_type          text,
    triggered_by_policy_code text,
    trigger_payload          jsonb,
    priority                 integer,
    attempts                 integer,
    enqueued_at              timestamptz
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = gs_enrichment, public, pg_temp
AS $$
DECLARE
    v_claimed_id uuid;
BEGIN
    -- Step 1: lock + pick the next eligible row
    SELECT q.queue_id INTO v_claimed_id
    FROM gs_enrichment.queue q
    WHERE q.status = 'pending'
      AND q.enrichment_type = p_worker_type
    ORDER BY q.priority ASC, q.enqueued_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1;

    -- Nothing to do
    IF v_claimed_id IS NULL THEN
        RETURN;
    END IF;

    -- Step 2: transition pending → running
    UPDATE gs_enrichment.queue q
       SET status     = 'running',
           run_id     = p_run_id,
           started_at = now(),
           attempts   = q.attempts + 1,
           last_error = NULL
     WHERE q.queue_id = v_claimed_id;

    -- Step 3: return the claimed row for the worker
    RETURN QUERY
    SELECT q.queue_id, q.party_id, q.enrichment_type,
           q.triggered_by_policy_code, q.trigger_payload,
           q.priority, q.attempts, q.enqueued_at
    FROM gs_enrichment.queue q
    WHERE q.queue_id = v_claimed_id;
END;
$$;

COMMENT ON FUNCTION gs_enrichment.fn_claim_enrichment_task(text, uuid) IS
    'Atomically claims the next pending task for a worker_type. Uses '
    'FOR UPDATE SKIP LOCKED so parallel runners do not block or double-claim. '
    'Transitions pending → running, attaches run_id, increments attempts, '
    'clears last_error. Returns the claimed row, or 0 rows if none eligible.';

GRANT EXECUTE ON FUNCTION gs_enrichment.fn_claim_enrichment_task(text, uuid) TO anon;


-- ─── PART 2: fn_complete_task ──────────────────────────────────────────────
-- Closes out a running task. Does NOT touch attempts (incremented at claim
-- time). On 'failed', last_error is set. On 'done'/'skipped', last_error is
-- cleared. Rejects transitions from non-running states to surface bugs early.

CREATE OR REPLACE FUNCTION gs_enrichment.fn_complete_task(
    p_queue_id uuid,
    p_outcome  text,
    p_error    text DEFAULT NULL
)
RETURNS gs_enrichment.queue
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = gs_enrichment, public, pg_temp
AS $$
DECLARE
    v_row gs_enrichment.queue;
BEGIN
    IF p_outcome NOT IN ('done', 'failed', 'skipped') THEN
        RAISE EXCEPTION
            'fn_complete_task: invalid outcome %, expected done/failed/skipped',
            p_outcome;
    END IF;

    UPDATE gs_enrichment.queue
       SET status      = p_outcome,
           finished_at = now(),
           last_error  = CASE WHEN p_outcome = 'failed' THEN p_error ELSE NULL END
     WHERE queue_id = p_queue_id
       AND status   = 'running'
    RETURNING * INTO v_row;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'fn_complete_task: queue_id % not found or not in running state',
            p_queue_id;
    END IF;

    RETURN v_row;
END;
$$;

COMMENT ON FUNCTION gs_enrichment.fn_complete_task(uuid, text, text) IS
    'Closes a running task. Outcome must be done/failed/skipped. Errors if '
    'queue_id is not in running state — surfaces stale state or double-dispatch '
    'bugs immediately instead of silently overwriting.';

GRANT EXECUTE ON FUNCTION gs_enrichment.fn_complete_task(uuid, text, text) TO anon;


-- ─── PART 3: Changelog + governance ────────────────────────────────────────

INSERT INTO public.changelog
    (version, author, change_type, description, affected_tables, breaking)
VALUES (
    'GS-MIGRATE-022a', 'Chris', 'function',
    'Added fn_claim_enrichment_task + fn_complete_task to gs_enrichment. '
    'Closes the claim-and-dispatch loop in runner.process_batch(). '
    'Uses FOR UPDATE SKIP LOCKED for safe parallel polling.',
    ARRAY['gs_enrichment.queue'],
    false
);

INSERT INTO gs_governance.schema_register
    (object_type, schema_name, object_name,
     issue_type, severity, description, recommended_action,
     status, owner, target_review_at,
     first_snapshot_id, last_snapshot_id, seen_count, source)
VALUES (
    'function', 'gs_enrichment', 'fn_claim_enrichment_task',
    'new_function', 'info',
    'Claim/complete RPCs deployed. Phase 2.7 of V4G enrichment platform. '
    'Runner will poll every 30s via fn_claim_enrichment_task and close tasks '
    'via fn_complete_task.',
    'Monitor first live run on Vela Group (queue_id 0e93f9db-...). Verify '
    'object_log + run_log rows land. Re-review after 1 week.',
    'in_progress', 'Chris',
    now() + interval '7 days',
    'MANUAL_20260421', 'MANUAL_20260421', 1, 'manual'
);


-- ─── PART 4: VERIFY ────────────────────────────────────────────────────────

-- Expect 2 new functions, both with security_definer=true
SELECT p.proname,
       pg_get_function_identity_arguments(p.oid) AS args,
       p.prosecdef AS security_definer
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'gs_enrichment'
  AND p.proname IN ('fn_claim_enrichment_task', 'fn_complete_task')
ORDER BY p.proname;

-- Expected:
--   fn_claim_enrichment_task | p_worker_type text, p_run_id uuid             | t
--   fn_complete_task         | p_queue_id uuid, p_outcome text, p_error text | t

COMMIT;


-- ═══════════════════════════════════════════════════════════════════════════
-- DRY-RUN TEST (execute separately after COMMIT; rolls back automatically)
-- ═══════════════════════════════════════════════════════════════════════════
-- BEGIN;
-- SELECT * FROM gs_enrichment.fn_claim_enrichment_task(
--     'nbb_financials',
--     gen_random_uuid()
-- );
-- -- Expected: 1 row with queue_id=0e93f9db-e4af-40c1-98a1-3952ff3e2e3e
-- ROLLBACK;
--
-- -- After rollback, verify Vela's entry is still pending:
-- SELECT queue_id, status, attempts, run_id, started_at
-- FROM gs_enrichment.queue
-- WHERE queue_id = '0e93f9db-e4af-40c1-98a1-3952ff3e2e3e';
-- -- Expected: status=pending, attempts=0, run_id=null, started_at=null


-- ═══════════════════════════════════════════════════════════════════════════
-- ROLLBACK (run manually if needed — commented)
-- ═══════════════════════════════════════════════════════════════════════════
-- BEGIN;
-- DROP FUNCTION IF EXISTS gs_enrichment.fn_claim_enrichment_task(text, uuid);
-- DROP FUNCTION IF EXISTS gs_enrichment.fn_complete_task(uuid, text, text);
-- DELETE FROM public.changelog WHERE version = 'GS-MIGRATE-022a';
-- UPDATE gs_governance.schema_register
--    SET status = 'superseded',
--        decision = 'Rolled back GS-MIGRATE-022a',
--        decision_at = now()
--  WHERE object_name = 'fn_claim_enrichment_task';
-- COMMIT;
