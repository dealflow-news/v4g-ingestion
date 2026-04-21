-- ═══════════════════════════════════════════════════════════════════════════
-- GS-MIGRATE-022c · run_log RPCs + doctrine hardening (lazy-open pattern)
-- ═══════════════════════════════════════════════════════════════════════════
-- Purpose: close governance-entry fn_open_close_run_log_future. Replaces
--   direct service_role writes in Python with SECURITY DEFINER RPCs, matching
--   the pattern set by fn_claim_enrichment_task + fn_complete_task.
--
-- Lazy-open pattern: run_id is generated CLIENT-SIDE by the Python runner
-- and passed into both fn_claim_enrichment_task (existing) and
-- fn_open_run_log (new). The runner opens run_log ONLY after a successful
-- claim, so empty polls produce zero run_log rows.
--
-- Scope: 2 new functions. No table/column/grant changes.
-- Prior: GS-MIGRATE-021, 022a, 022b
-- Owner: Chris · V4G Data Ops

BEGIN;

-- ─── PART 1: fn_open_run_log ───────────────────────────────────────────────

CREATE OR REPLACE FUNCTION gs_enrichment.fn_open_run_log(
    p_run_id       uuid,
    p_worker_type  text,
    p_host         text DEFAULT NULL,
    p_app_version  text DEFAULT NULL
)
RETURNS gs_enrichment.run_log
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = gs_enrichment, public, pg_temp
AS $$
DECLARE
    v_row gs_enrichment.run_log;
BEGIN
    IF p_worker_type IS NULL OR length(p_worker_type) = 0 THEN
        RAISE EXCEPTION 'fn_open_run_log: p_worker_type required';
    END IF;

    INSERT INTO gs_enrichment.run_log
        (run_id, worker_type, status, host, app_version)
    VALUES
        (p_run_id, p_worker_type, 'running', p_host, p_app_version)
    RETURNING * INTO v_row;

    RETURN v_row;
END;
$$;

COMMENT ON FUNCTION gs_enrichment.fn_open_run_log(uuid, text, text, text) IS
    'Opens a run_log row with client-provided run_id and status=running. '
    'Called by the runner AFTER a successful claim, so empty polls produce '
    'no run_log rows (lazy-open pattern). run_id must match the one passed '
    'to fn_claim_enrichment_task so the queue row and run_log are linked.';

GRANT EXECUTE ON FUNCTION gs_enrichment.fn_open_run_log(uuid, text, text, text) TO anon;


-- ─── PART 2: fn_close_run_log ──────────────────────────────────────────────

CREATE OR REPLACE FUNCTION gs_enrichment.fn_close_run_log(
    p_run_id        uuid,
    p_status        text,
    p_tasks_ok      integer DEFAULT 0,
    p_tasks_failed  integer DEFAULT 0,
    p_tasks_skipped integer DEFAULT 0,
    p_error_summary text    DEFAULT NULL
)
RETURNS gs_enrichment.run_log
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = gs_enrichment, public, pg_temp
AS $$
DECLARE
    v_row gs_enrichment.run_log;
BEGIN
    IF p_status NOT IN ('completed', 'failed', 'aborted') THEN
        RAISE EXCEPTION
            'fn_close_run_log: invalid status %, expected completed/failed/aborted',
            p_status;
    END IF;

    IF p_tasks_ok < 0 OR p_tasks_failed < 0 OR p_tasks_skipped < 0 THEN
        RAISE EXCEPTION
            'fn_close_run_log: task counts must be non-negative (ok=%, failed=%, skipped=%)',
            p_tasks_ok, p_tasks_failed, p_tasks_skipped;
    END IF;

    UPDATE gs_enrichment.run_log
       SET status        = p_status,
           finished_at   = now(),
           tasks_ok      = p_tasks_ok,
           tasks_failed  = p_tasks_failed,
           tasks_skipped = p_tasks_skipped,
           tasks_total   = p_tasks_ok + p_tasks_failed + p_tasks_skipped,
           error_summary = p_error_summary
     WHERE run_id = p_run_id
       AND status = 'running'
    RETURNING * INTO v_row;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'fn_close_run_log: run_id % not found or not in running state',
            p_run_id;
    END IF;

    RETURN v_row;
END;
$$;

COMMENT ON FUNCTION gs_enrichment.fn_close_run_log(uuid, text, integer, integer, integer, text) IS
    'Closes a running run_log row with final task counts. Computes '
    'tasks_total = ok + failed + skipped. Errors if not in running state.';

GRANT EXECUTE ON FUNCTION gs_enrichment.fn_close_run_log(uuid, text, integer, integer, integer, text) TO anon;


-- ─── PART 3: Changelog + governance ────────────────────────────────────────

INSERT INTO public.changelog
    (version, author, change_type, description, affected_tables, breaking)
VALUES (
    'GS-MIGRATE-022c', 'Chris', 'function',
    'Added fn_open_run_log + fn_close_run_log to gs_enrichment. Completes '
    'the doctrine pattern: all queue + run_log writes via SECURITY DEFINER '
    'RPCs. Enables lazy-open pattern (empty polls produce no run_log rows). '
    'Runner generates run_id client-side, passes to both fn_claim_enrichment_task '
    'and fn_open_run_log so queue row and run_log are atomically linked.',
    ARRAY['gs_enrichment.run_log'],
    false
);

UPDATE gs_governance.schema_register
   SET status = 'resolved',
       decision = 'GS-MIGRATE-022c deployed: fn_open_run_log + fn_close_run_log '
                  'as SECURITY DEFINER RPCs. Python queue.py helpers now route '
                  'through these. Runner uses lazy-open: run_log rows created '
                  'only after successful claim, eliminating ~96 writes/hr idle-'
                  'worker noise.',
       decision_at = now(),
       last_seen_at = now()
 WHERE issue_id = 'fa218712-57b4-42b5-88c7-77c01fa21d19';


-- ─── PART 4: VERIFY ────────────────────────────────────────────────────────

SELECT p.proname,
       pg_get_function_identity_arguments(p.oid) AS args,
       p.prosecdef AS security_definer
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'gs_enrichment'
  AND p.proname LIKE 'fn_%'
ORDER BY p.proname;

-- Expected: 4 functions, all security_definer=true

COMMIT;


-- ═══════════════════════════════════════════════════════════════════════════
-- DRY-RUN TEST (execute separately; rolls back)
-- ═══════════════════════════════════════════════════════════════════════════
-- BEGIN;
-- DO $$
-- DECLARE
--     v_run_id uuid := gen_random_uuid();
--     v_row gs_enrichment.run_log;
-- BEGIN
--     SELECT * INTO v_row FROM gs_enrichment.fn_open_run_log(
--         v_run_id, 'test_worker', 'test-host', '0.0.0'
--     );
--     RAISE NOTICE 'Opened: run_id=% status=%', v_row.run_id, v_row.status;
--
--     SELECT * INTO v_row FROM gs_enrichment.fn_close_run_log(
--         v_run_id, 'completed', 1, 0, 0, NULL
--     );
--     RAISE NOTICE 'Closed: status=% tasks_total=%', v_row.status, v_row.tasks_total;
-- END;
-- $$;
-- ROLLBACK;


-- ═══════════════════════════════════════════════════════════════════════════
-- ROLLBACK (run manually if needed — commented)
-- ═══════════════════════════════════════════════════════════════════════════
-- BEGIN;
-- DROP FUNCTION IF EXISTS gs_enrichment.fn_open_run_log(uuid, text, text, text);
-- DROP FUNCTION IF EXISTS gs_enrichment.fn_close_run_log(uuid, text, integer, integer, integer, text);
-- DELETE FROM public.changelog WHERE version = 'GS-MIGRATE-022c';
-- UPDATE gs_governance.schema_register
--    SET status = 'open', decision = NULL, decision_at = NULL
--  WHERE issue_id = 'fa218712-57b4-42b5-88c7-77c01fa21d19';
-- COMMIT;
