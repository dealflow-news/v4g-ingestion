# IMPORT-NOTES — Lane B leverpakket (2026-05-08)

**Read me first.** This is the entrypoint for the Lane B build session. `MANIFEST.md` lists every artifact with its status; this file explains *why* and *in what order* to apply them.

---

## What's in this drop

```
v4g-ingestion/
├── IMPORT-NOTES.md                              ← you are here
├── MANIFEST.md                                  ← per-file truth-as-intent register
├── sql/
│   ├── migrations/                              ← forward-only schema changes
│   │   ├── GS-MIGRATE-LB-001.sql                ← ✅ live · _stg_nbb_filings schema
│   │   ├── GS-MIGRATE-LB-002.sql                ← ✅ live · fn_promote_nbb_filing
│   │   ├── GS-MIGRATE-LB-003.sql                ← ✅ live · policy seed
│   │   └── GS-MIGRATE-LB-004.sql                ← ⚠️ OPTIONAL (β4 fallback only)
│   └── governance/                              ← schema_register batches
│       └── GS-GOVERNANCE-2026-05-08.sql         ← ⏳ pending · 5 schema_register entries
├── src/
│   └── cli/
│       ├── __init__.py                          ← package marker (three-act doctrine)
│       ├── export_financials_xlsx.py            ← β0 · read-only Excel exporter
│       └── ingest_nbb_zip.py                    ← β1 · revised Lane B staging-loader
└── docs/
    └── LB-005-filing-date-patches.md            ← β2 · DepositDate wiring (3 small patches)
```

The `sql/` split into `migrations/` (forward-only) and `governance/` (batches & repair) is new in this drop and is a recommendation worth committing into the repo's `.editorconfig`-style conventions.

---

## Architecture decision recorded this session

**Lane B's main path is sync-in-CLI**, not queue+worker. Reasons:

- Lane B is bulk-CLI driven (analyst uploads a ZIP, expects feedback), not event-driven like Lane A
- `gs_enrichment.enqueue()` deduplicates per `(party_id, enrichment_type)` — for 20 filings of one party that would coalesce into a single task, defeating per-filing tracking
- Avoids extending the runner contract to pass `trigger_payload` to workers (Lane A wouldn't need it)
- Gives the analyst immediate feedback in the same terminal session

**Consequence**: `GS-MIGRATE-LB-004` (the AFTER INSERT trigger) is downgraded from "must-run" to "optional β4 fallback". Useful only if Lane B ever needs an async re-processing path. The CLI `cli/ingest_nbb_zip.py` (β1) and the upcoming β3 sync-promote do all the work without it.

---

## Three-act doctrine (codified for the repo)

This codebase has three roles, and CLI code stays out of #2:

```
1. Ingest into Supabase   ← cli/ingest_*.py + workers under src/enrichment/
2. Analyse in Supabase    ← DB views (vw_target_financials, vw_*) — SQL only
3. Export out of Supabase ← cli/export_*.py
```

If you find yourself reaching for pandas in a CLI to compute YoY deltas, stop — that logic belongs in a view. The exporter just queries and formats. This is what `src/cli/__init__.py` documents inline.

---

## Recommended order from a clean checkout

| # | Action | Why |
|---|---|---|
| 1 | `git add` everything in this drop, `git commit` | Snapshot of intent. LB-001..LB-003 files are documentation + rollback source even though they're already live. |
| 2 | Apply the β2 patches from `docs/LB-005-filing-date-patches.md` to `src/domain/nbb/fetcher.py` and `src/enrichment/workers/nbb_financials.py` | Wires `DepositDate` from NBB API through to `fact_financials.nbb_filing_date` for both lanes. |
| 3 | `pip install openpyxl`, add `"openpyxl>=3.1"` to `pyproject.toml [project] dependencies` | Required by β0 exporter. |
| 4 | Run `sql/governance/GS-GOVERNANCE-2026-05-08.sql` against Supabase | Logs 5 outstanding governance findings. Idempotent only if no rows exist for snapshot `20260424_150949` from `source='manual'` — re-running blindly will create duplicates. |
| 5 | Smoke-test β0 against V4G's own party_id (4 SRC_NBB rows from Lane A) | Validates the analytical → Excel architecture. Read-only, zero risk. |
| 6 | Run β1 against AB LENS MOTOR ZIP | Expect: 20 rows in `_stg_nbb_filings`. Re-run is a no-op (idempotent on `filing_reference`). |
| 7 | (Skip for now) `sql/migrations/GS-MIGRATE-LB-004.sql` | Optional β4 — only run if you decide you need the async re-processing fallback. |

---

## Two reminders for the commit

- **LB-001..LB-003 are already live**, so the files are primarily documentation + rollback source. Do *not* re-execute them against production. The VERIFY blocks in each file are safe to re-run any time and should still pass.
- **LB-004 is optional** under the sync-in-CLI architecture. The CLI (β1) writes staging rows; β3 (next session) extends it to also call `fn_promote_nbb_filing` per row. The trigger only matters if you want async re-processing.

---

## Open after this session

| Code | Scope |
|---|---|
| **β3 sync-promote** | Extend `cli/ingest_nbb_zip.py` to also call `fn_promote_nbb_filing(filing_id, canonical_jsonb)` per newly-staged row. Output: "20 staged, 18 promoted, 2 failed (X reason)". This makes Lane B end-to-end without a worker. |
| LB-006 (optional) | Python worker `src/enrichment/workers/nbb_xbrl_parse.py` for the async fallback. Only build if β3 turns out to be insufficient. |
| Exporter v1.2 | Add `gs_enrichment` to `SCHEMAS_TO_INTROSPECT` in `automation/gs_dict_export.py`, bump VERSION to 1.2, regenerate dictionary. Tracked by governance entry #1. |
| PROJECT_INSTRUCTIONS v2.2 | Refresh Critical-constants tables to reflect live CHECK values for `changelog.change_type`, `schema_register.object_type`, `schema_register.source`. Tracked by governance entry #5. |
| **API key rotation** | The NBB key was pasted in chat-history during this session. Regenerate via developer.cbso.nbb.be → My subscriptions → Regenerate primary key, update `.env` and `src/domain/nbb/config.json`. |

---

## Architecture recap

```
                   ┌─ Lane A: CBSO JSON-XBRL API ───────────────────┐
                   │       (3-4 jaar, live since 2026-04-21)        │
                   │       async via gs_enrichment queue+worker     │
                   │                                                │
NBB CBSO ──────────┤                                                ├──► fact_financials
                   │                                                │   (SRC_NBB)
                   │                                                │
                   └─ Lane B: CBSO consult bulk-ZIP ────────────────┘
                          (10+ jaar, this session's build)
                          sync via cli/ingest_nbb_zip.py
                                        ↓
                              _stg_nbb_filings (raw + metadata)
                                        ↓
                              [β3 next] fn_promote_nbb_filing
```

Both lanes write to the same `fact_financials` table with `source_code='SRC_NBB'` and conflict-resolve via the unique constraint on `(party_id, period_label, source_code)`. The "latest filing_date wins" doctrine is encoded in `fn_promote_nbb_filing` (LB-002), so neither lane has to re-implement it client-side.
