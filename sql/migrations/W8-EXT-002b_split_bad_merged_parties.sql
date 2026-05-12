-- ============================================================================
-- W8-EXT-002b · Split 13 bad-merged multi-KBO parties
-- ============================================================================
-- Problem (confirmed via KBO portal lookups on 2026-05-12):
--   Each of these 13 party_ids has 2 KBOs that refer to UNRELATED legal
--   entities. Examples:
--     * Lips: 0406904508 = NV Knokke-Heist  vs  1017736074 = DIVERS Bilzen-Hoeselt
--     * MCB:  0410282977 = Mojsdis Chaside Belze VZW Antwerpen (a synagogue!)
--             vs the other KBO claiming to be MCB Nederland B.V.
--   The dedup logic that created them used name-only matching without
--   country/legal_form/address verification. This is one-time historical
--   damage -- party_merge_log is empty for these 13 (the merges bypassed
--   the proper audit mechanism).
--
-- Strategy: SPLIT (not DELETE).
--   DELETE was off the table due to 35 FK refs to party_registry.party_id
--   across many tables (canonical_deals, party_relationships, party_aliases,
--   party_kbo_*, gs_enrichment.queue, etc.). DELETE would either CASCADE
--   too much or fail with FK violations.
--
--   SPLIT keeps each original party_id intact (all 35 FK refs preserved),
--   moves ONE KBO to a NEW party_id, and clones metadata into the new row
--   with a display_name suffix so analysts can identify the split.
--
-- Selection rule for which KBO STAYS with the original party_id:
--   * If a KBO has is_primary=TRUE -> it stays (analyst already curated):
--       - Voxdale: 0749635695 stays, 0435552467 moves to new
--       - Winmar:  0728475641 stays, 0728571948 moves to new
--   * Otherwise: lower numeric KBO stays, higher moves (deterministic,
--     arbitrary -- analyst will need to decide which entity is "the real"
--     one per case afterwards based on the suffixed display_name).
--
-- After split:
--   * Voxdale + Winmar: original party_id retains its evidence rows
--     (Voxdale 1 row, Winmar 6 rows), new party_id is empty.
--   * Sunrise: original retains its 1 OTB-Latest evidence row, new is empty.
--     (Sunrise has no curation marker -- analyst should verify on KBO portal
--      whether 0433209027 or 0632607373 is the entity with the OTB-Latest data.)
--   * MCB special: KBO 0410282977 is confirmed to be a totally different
--     entity (Joodse VZW). After split, follow-up cleanup may detach this
--     KBO entirely. Logged as a separate task.
--   * Other 10 parties: empty data on both sides, fresh stubs created.
--
-- Logging: changelog as 'ref_data' (data correction, not schema change).
-- Idempotency: WHERE NOT EXISTS guards on changelog. DO block re-runs are
-- naturally no-op because there are no multi-KBO parties left to split.
-- ============================================================================

BEGIN;

DO $$
DECLARE
    src_party_id  UUID;
    keep_kbo      TEXT;
    move_kbo      TEXT;
    new_party_id  UUID;
    splits_done   INT := 0;
BEGIN
    -- Iterate over each multi-KBO party.
    -- ranked CTE assigns rn=1 to the KBO that should STAY:
    --   * is_primary=TRUE first (NULLS LAST)
    --   * then lower id_value first
    FOR src_party_id, keep_kbo, move_kbo IN
        WITH multi_kbo AS (
            SELECT party_id FROM party_identifiers
            WHERE id_type = 'KBO'
            GROUP BY party_id
            HAVING COUNT(*) > 1
        ),
        ranked AS (
            SELECT
                pi.party_id,
                pi.id_value,
                ROW_NUMBER() OVER (
                    PARTITION BY pi.party_id
                    ORDER BY pi.is_primary DESC NULLS LAST, pi.id_value ASC
                ) AS rn
            FROM party_identifiers pi
            JOIN multi_kbo USING (party_id)
            WHERE pi.id_type = 'KBO'
        )
        SELECT
            party_id,
            MAX(id_value) FILTER (WHERE rn = 1) AS keep_kbo,
            MAX(id_value) FILTER (WHERE rn = 2) AS move_kbo
        FROM ranked
        GROUP BY party_id
    LOOP
        -- Create a new party_registry row cloning metadata from the original.
        -- display_name is suffixed so analysts can identify the splits.
        -- All NOT NULL fields filled; CHECK constraints respected.
        INSERT INTO party_registry (
            party_id,
            party_type,
            party_subtype,
            legal_name,
            normalized_name,
            display_name,
            country_iso2,
            status,
            enrichment_tier,
            enrichment_status,
            persistence_class
        )
        SELECT
            uuid_generate_v4(),
            party_type,
            party_subtype,
            legal_name,
            normalized_name || '_kbo_' || move_kbo,
            COALESCE(display_name, legal_name) || ' [split: KBO ' || move_kbo || ']',
            country_iso2,
            'Active',
            'P3',
            'raw',
            COALESCE(persistence_class, 'Unknown')
        FROM party_registry
        WHERE party_id = src_party_id
        RETURNING party_id INTO new_party_id;

        -- Move the secondary KBO row from original party to new party
        UPDATE party_identifiers
        SET party_id = new_party_id
        WHERE party_id = src_party_id
          AND id_type = 'KBO'
          AND id_value = move_kbo;

        splits_done := splits_done + 1;
        RAISE NOTICE 'Split %: kept KBO %, moved KBO % to new party %',
            src_party_id, keep_kbo, move_kbo, new_party_id;
    END LOOP;

    RAISE NOTICE '----------------------------------------';
    RAISE NOTICE 'Total splits performed: %', splits_done;
    RAISE NOTICE '----------------------------------------';
END $$;

-- Changelog entry
INSERT INTO changelog (
    version, change_type, description, affected_tables, breaking, migration_plan
)
SELECT
    'W8-EXT-002b',
    'ref_data',
    'Split 13 bad-merged multi-KBO parties. Each party_id had 2 KBOs '
    'pointing to unrelated legal entities (confirmed via KBO portal: e.g. '
    'Lips NV Knokke-Heist vs Lips DIVERS Bilzen-Hoeselt; MCB metadata vs '
    'Mojsdis Chaside Belze VZW). Bad merges bypassed party_merge_log. '
    'Strategy: keep one KBO with original party_id (preserves 35 FK refs), '
    'move secondary KBO to new party_id with cloned metadata + suffixed '
    'display_name. For curated cases (Voxdale, Winmar) the is_primary=TRUE '
    'KBO stays; for the 11 others the lower numeric KBO stays.',
    ARRAY['party_registry', 'party_identifiers'],
    FALSE,
    'No DELETE; all 35 FK refs to original party_ids preserved. Follow-up '
    'analyst work: (1) MCB - KBO 0410282977 is a synagogue VZW, likely '
    'detach entirely; (2) Sunrise - verify which KBO carries the OTB-Latest '
    'evidence row; (3) per-case display_name cleanup of split parties.'
WHERE NOT EXISTS (
    SELECT 1 FROM changelog WHERE version = 'W8-EXT-002b'
);

COMMIT;

-- ============================================================================
-- Verification
-- ============================================================================

-- 1. No more multi-KBO parties (expect: 0 rows)
SELECT party_id, COUNT(*) AS kbo_count
FROM party_identifiers
WHERE id_type = 'KBO'
GROUP BY party_id
HAVING COUNT(*) > 1;

-- 2. 13 new party_registry rows with split marker
SELECT display_name, party_type, status, enrichment_tier, party_id
FROM party_registry
WHERE display_name LIKE '%[split: KBO%'
ORDER BY display_name;

-- 3. Original 13 parties each have exactly 1 KBO now
SELECT pr.display_name, COUNT(pi.id_value) AS kbo_count, array_agg(pi.id_value) AS kbos
FROM party_registry pr
LEFT JOIN party_identifiers pi
  ON pi.party_id = pr.party_id AND pi.id_type = 'KBO'
WHERE pr.party_id IN (
    '0021b244-af57-44c9-a538-d05d00eeaeab',
    '07abe992-372f-4f7a-9705-9d05545ec7c1',
    '0bc257f1-af82-494d-8c50-623fbd5db3c1',
    '0be78fdc-59f4-58a0-8936-9bd5aefc17a6',
    '0febe416-dfaa-4c7e-9756-3737b9d3912c',
    '268f3d62-b318-45c4-a316-238160a47d93',
    '49d7a19e-00ce-482e-9a21-447ee84dc65b',
    '8227441e-91f4-4755-9eeb-15a0cf3b28ed',
    'c9ee9a47-225f-457c-907f-7b9c7edb8109',
    'cb965ef8-d806-4bdc-ba65-8f5121505445',
    'd9ae5852-ecfe-409d-b739-266bb1e633cf',
    'ece4d72a-33ff-4f7b-9a82-e618f3e87030',
    'f7b87508-0a53-4d8c-a1ae-3f0b1ce762eb'
)
GROUP BY pr.display_name
ORDER BY pr.display_name;

-- 4. Evidence rows preserved for Voxdale + Winmar + Sunrise on original party_ids
SELECT pr.display_name, COUNT(*) AS evidence_rows
FROM fact_financials_evidence fe
JOIN party_registry pr USING (party_id)
WHERE pr.party_id IN (
    '268f3d62-b318-45c4-a316-238160a47d93',  -- Voxdale
    'cb965ef8-d806-4bdc-ba65-8f5121505445',  -- Winmar
    'ece4d72a-33ff-4f7b-9a82-e618f3e87030'   -- Sunrise
)
GROUP BY pr.display_name;

-- 5. Changelog entry recorded
SELECT id, version, change_type, change_date FROM changelog WHERE version = 'W8-EXT-002b';
