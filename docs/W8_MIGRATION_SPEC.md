# W8-core — Financial Evidence Layer Migration Spec

**Branch**: `feature/W8-core`
**Status**: design approved 2026-05-09, ready for DDL deploy
**Scope**: foundation DDL + view migration only; Python code laag in volgende PR

---

## Doel

Eén canonieke financiële evidence-laag waaruit alle bestaande KPI's én DCF/EV-multiples afgeleid kunnen worden, **vóór** schaal-ingestion. NBB krijgt line-granulaire opslag (zoals legacy V4G_Accounts had); 3rd-party aggregators (PB, ODB, V4G) blijven KPI-level; analyst-narratieven krijgen aparte tabel.

---

## Architectuur — vier lagen, één view

```
┌─ LAYER 1: NBB primary (line-granulair) ────────────────┐
│   fact_filings           1 rij per filing              │
│   fact_financials_lines  N rijen per filing            │
└────────────────────────────────────────────────────────┘
┌─ LAYER 2: 3rd-party aggregators (KPI-level) ───────────┐
│   fact_financials_evidence                              │
│     SRC_PB  international, fallback voor BE             │
│     SRC_ODB Open The Box — BE-specialist               │
│     SRC_V4G V4G manual entries                         │
│   (rename van bestaande fact_financials,                │
│    SRC_NBB rijen verwijderd na backfill)                │
└────────────────────────────────────────────────────────┘
┌─ LAYER 3: analyst narratives ──────────────────────────┐
│   fact_financials_overrides                             │
│     PARTY_INTERVIEW, ANALYST_DECISION                   │
│     met categorieën NORMALIZATION/PROFORMA/CORRECTION   │
└────────────────────────────────────────────────────────┘
┌─ LAYER 4: effective view ──────────────────────────────┐
│   fact_financials (VIEW)                                │
│     COALESCE(override, NBB_derived, ODB, PB, V4G, NULL) │
│     met BE-aware precedence                             │
└────────────────────────────────────────────────────────┘
```

Plus reference dictionary:
```
dim_pcmn_codes              MAR/PCMN labels (NL/EN), section, V4G priority
                            ~24 W8-core codes (uitbreidbaar, geen FK lock)
```

---

## Bron-precedence in fact_financials view

```
BE-entity     : override > NBB_derived > ODB > PB > V4G > NULL
non-BE entity : override > PB > V4G > NULL  (NBB/ODB BE-only)
```

Implementatie via window-ranked CTE — zie `009_create_fact_financials_view.sql`.

---

## File index

| # | File | Deploy order | Idempotent |
|---|---|---|---|
| 001 | `001_create_dim_pcmn_codes.sql` | 1st (geen FK deps) | ✅ |
| 002 | `002_create_fact_filings.sql` | 2nd | ✅ |
| 003 | `003_create_fact_financials_lines.sql` | 3rd (FK → filings) | ✅ |
| 004 | `004_create_fact_financials_overrides.sql` | 4th (FK → party) | ✅ |
| 005 | `005_seed_dim_pcmn_codes.sql` | 5th | ✅ (ON CONFLICT) |
| 006 | `006_rls_policies.sql` | 6th | ✅ (DROP/CREATE pattern) |
| 007 | `007_governance_entries.sql` | 7th | ✅ (ON CONFLICT) |
| 008 | `008_rename_fact_financials_to_evidence.sql` | 8th — **risk point** | ⚠️ once-only |
| 009 | `009_create_fact_financials_view.sql` | 9th | ✅ (CREATE OR REPLACE) |
| 010 | `010_validate_canary.sql` | post-deploy verification | read-only |
| 999 | `999_rollback_full.sql` | emergency only | ⚠️ destructive |

---

## Risk-ranked migration steps

### Low risk (steps 001-007)
Pure additive: nieuwe tabellen, indexes, RLS, governance entries. Geen bestaande objecten geraakt. **Rollback** = DROP de nieuwe tabellen.

### Medium risk (step 008 — rename)
`ALTER TABLE fact_financials RENAME TO fact_financials_evidence`. Alle FK's, RLS-policies, en triggers blijven automatisch werken (Postgres tracks via OID, niet name). **Maar**: views, functies, en application code die naar `fact_financials` verwijzen breken. Zie consumer-inventaris hieronder.

### High risk (step 009 — view recreation)
Nieuwe view `fact_financials` die de oude TABLE-rol vervangt. Moet exact dezelfde columns hebben (of subset met goede defaults) zodat downstream queries blijven werken.

---

## Consumer inventaris — wie leest fact_financials nu?

**Vóór step 008 deployment, run deze inventaris:**

```sql
-- Views
SELECT viewname, definition 
FROM pg_views 
WHERE definition ILIKE '%fact_financials%' 
  AND schemaname = 'public';

-- Functions
SELECT proname, prosrc 
FROM pg_proc 
WHERE prosrc ILIKE '%fact_financials%' 
  AND pronamespace = 'public'::regnamespace;

-- Materialized views
SELECT matviewname FROM pg_matviews 
WHERE definition ILIKE '%fact_financials%';

-- RLS policies
SELECT tablename, policyname, qual, with_check
FROM pg_policies 
WHERE qual::text ILIKE '%fact_financials%' 
   OR with_check::text ILIKE '%fact_financials%';
```

Bekend uit dictionary: `vw_target_financials` is een hoofd-consumer. Inventaris zal meer aan het licht brengen.

**Mitigation**: na step 008 (rename) draait de view (step 009) onder dezelfde naam `fact_financials`. Consumers die SELECT'en blijven werken zolang de nieuwe view dezelfde kolommen heeft. Consumers die INSERT/UPDATE doen — die breken (een view is niet schrijfbaar). **Schrijf-consumers moeten omgeleid worden naar `fact_financials_evidence` direct.**

---

## Doctrine block — voor migration headers

Zie `docs/W8_DOCTRINE.md`. Eén-pagina samenvatting:

```
NBB = primary canonical (line-granulair via fact_filings + lines)
PB/ODB/V4G = secondary evidence (KPI-level via fact_financials_evidence)
PARTY_INTERVIEW = narrative (parallel via fact_financials_overrides)
fact_financials = VIEW met BE-aware precedence
```

---

## Wat NIET in deze PR

Expliciet uitgesloten — komt in volgende branches:

| Item | Branch |
|---|---|
| NBB worker uitbreiding (extract_filing_data, dual-write) | `feature/W8-worker` |
| Adjustment ingester CLI (Excel/CSV → overrides) | `feature/W8-adjustments` |
| Tests (parser, worker, view-correctness) | met bovenstaande |
| Canary backfill (AB LENS MOTOR) | `feature/W8-canary-backfill` |
| Volledige backfill (~22k filings) | `feature/W8-full-backfill` |
| Cleanup SRC_NBB rows uit fact_financials_evidence | `feature/W8-cleanup-evidence` (na validatie) |
| W9-α directors via fact_board_snapshot | `feature/W9-alpha-directors` |
| W9-γ participations cleanup | `feature/W9-gamma-participations` |
| W10 Authentic Archive integratie (pre-2022) | `feature/W10-archive` |

---

## Deployment volgorde

```bash
# 1. Inventaris (read-only)
psql -f migrations/W8-core/inventory_consumers.sql > consumers.txt
# Review consumers.txt — identify any write-pad to fact_financials

# 2. Apply DDL (low risk)
psql -f migrations/W8-core/001_create_dim_pcmn_codes.sql
psql -f migrations/W8-core/002_create_fact_filings.sql
psql -f migrations/W8-core/003_create_fact_financials_lines.sql
psql -f migrations/W8-core/004_create_fact_financials_overrides.sql
psql -f migrations/W8-core/005_seed_dim_pcmn_codes.sql
psql -f migrations/W8-core/006_rls_policies.sql
psql -f migrations/W8-core/007_governance_entries.sql

# 3. Verify (read-only)
psql -c "SELECT count(*) FROM fact_filings;"   -- should be 0
psql -c "SELECT count(*) FROM dim_pcmn_codes;" -- should be 24

# 4. RENAME (medium risk — coordination point)
# IMPORTANT: stop NBB worker writes BEFORE this step
psql -f migrations/W8-core/008_rename_fact_financials_to_evidence.sql

# 5. CREATE VIEW (high risk — verifies before)
psql -f migrations/W8-core/009_create_fact_financials_view.sql

# 6. Validate
psql -f migrations/W8-core/010_validate_canary.sql

# 7. (Restart NBB worker — but it still writes to fact_financials_evidence
#    until the W8-worker branch lands. Dual-truth is OK during transition.)
```

---

## Rollback plan

Als step 008 of 009 onverwacht problemen geeft:

```bash
# Reverse order
psql -c "DROP VIEW IF EXISTS public.fact_financials;"
psql -c "ALTER TABLE public.fact_financials_evidence RENAME TO fact_financials;"
psql -f migrations/W8-core/999_rollback_full.sql  # drops new tables
```

Geen data loss: rename is atomic, view drop is atomic, nieuwe tabellen zijn nog leeg.

---

## Validation — go/no-go criteria

Na step 009, deze 5 testen moeten slagen:

```sql
-- T1: All new tables exist + RLS enabled
SELECT tablename, rowsecurity 
FROM pg_tables 
WHERE schemaname='public' 
  AND tablename IN ('fact_filings','fact_financials_lines',
                    'fact_financials_overrides','dim_pcmn_codes',
                    'fact_financials_evidence');
-- Expect: 5 rows, all rowsecurity=true

-- T2: dim_pcmn_codes seed correct
SELECT count(*) FROM dim_pcmn_codes WHERE v4g_priority='HIGH';
-- Expect: 18+

-- T3: fact_financials view exists and queryable
SELECT count(*) FROM fact_financials;
-- Expect: ~2,124 (alle PB+ODB+V4G rijen via evidence; geen NBB-derived nog)

-- T4: existing consumer vw_target_financials still works
SELECT count(*) FROM vw_target_financials;
-- Expect: same as before migration

-- T5: governance entries logged
SELECT count(*) FROM gs_governance.schema_register 
WHERE migration_id LIKE 'W8-core-%';
-- Expect: 5+ entries

```

Als één test faalt → rollback. Geen "we lossen het later op".

---

## Schema register entries

Per GS-SOP-002 / GS-SOP-010 — één entry per nieuw object:

```sql
INSERT INTO gs_governance.schema_register
  (migration_id, object_type, object_name, severity, status, description, created_by)
VALUES
  ('W8-core-001', 'table', 'public.dim_pcmn_codes', 'info', 'resolved',
   'New reference dim for PCMN/MAR codes; W8-core scope 24 codes seeded.', 'chris'),
  ('W8-core-002', 'table', 'public.fact_filings', 'info', 'resolved',
   'New filing-level metadata table; replaces NBB-source rows in fact_financials.', 'chris'),
  ('W8-core-003', 'table', 'public.fact_financials_lines', 'info', 'resolved',
   'New granular financial line items (PCMN-coded); FK to fact_filings.', 'chris'),
  ('W8-core-004', 'table', 'public.fact_financials_overrides', 'info', 'resolved',
   'New analyst-narrative table (interview adjustments); RLS-restricted.', 'chris'),
  ('W8-core-005', 'rename', 'public.fact_financials → fact_financials_evidence', 'warning', 'resolved',
   'Renamed to clarify role: KPI-level evidence from non-NBB sources. View fact_financials replaces.', 'chris'),
  ('W8-core-006', 'view', 'public.fact_financials', 'info', 'resolved',
   'Replaced TABLE with VIEW; blends NBB-derived + evidence + overrides per BE-aware precedence.', 'chris');
```

---

## Open vragen / aandachtspunten voor Chris

1. **Consumer inventaris** moet pre-deploy gedraaid worden. Resultaat bepaalt of step 008-009 echt veilig zijn.
2. **NBB worker stop** tijdens step 008 — moeten we hiervoor een feature flag instellen, of expliciet de Render service stoppen tijdens migratie?
3. **`fact_financials_evidence` writes** blijven werken (tabel is gewoon hernoemd). Maar de view `fact_financials` is read-only. Existing INSERT-paden naar `fact_financials` moeten wijzigen. Mogelijk niet veel — dit komt uit consumer inventaris.
4. **NBB rows in fact_financials_evidence** blijven aanwezig na deze PR. Pas wanneer backfill (W8-canary-backfill) gevalideerd is, ruimen we die op (W8-cleanup-evidence). Tot dan zit er voor NBB-parties **dubbele data** in het systeem (één rij in evidence, plus bij toekomstig dual-write ook in lines). View resolveert dit via precedence (NBB-derived wint over evidence-NBB), maar query-purity is nog niet 100%.

---

## Acceptance criteria

- [ ] Alle 10 SQL files reviewed
- [ ] Consumer inventaris draait, output reviewed
- [ ] DDL deployed in dev/staging, validation tests T1-T5 slagen
- [ ] Rollback plan getest in dev (drop & recreate fresh)
- [ ] schema_register entries logged
- [ ] doctrine block toegevoegd aan `GOLDEN_SAFE_SOP.md`
- [ ] CLAUDE.md updated met nieuwe tabellen + view rol
- [ ] PR merged naar main → automatische gs_dict_export.py run werkt → dictionary.md updated
