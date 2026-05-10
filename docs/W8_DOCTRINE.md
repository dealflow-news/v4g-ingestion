# V4G Golden Safe — Financial Data Doctrine

**Status**: canonical from W8/W11 (2026-05)
**Place**: GOLDEN_SAFE_SOP.md, CLAUDE.md, all migration script headers, IMPORT-NOTES

---

## Bronnen-typologie

### PRIMARY (line-granulair, bron-of-truth voor BE)

**SRC_NBB** — Belgian Central Balance Sheet via NBB CBSO API
- Filings 04/2022 → as JSON-XBRL (line-granulair via `fact_filings` + `fact_financials_lines`)
- Filings 2007 → 04/2022 as XBRL via Authentic Archive (W10 — toekomstige PR)
- 397 Rubrics per filing, gefilterd op Period='N' levert ~50 unieke PCMN codes
- Bewaart point-in-time: address, legal form, directors, participations, shareholders

### SECONDARY (KPI-level, 3rd-party aggregators)

**SRC_PB** — PitchBook
- Internationale aggregator, KPI-level
- Primaire kracht: non-BE entities, deal-flow signals
- Voor BE entities alleen als fallback (lower granularity dan NBB direct)

**SRC_ODB** — Open The Box
- BE-specialist aggregator (3M+ Belgische bedrijven)
- Doorvertaalt NBB + KBO + Belgisch Staatsblad
- **Primaire lane in V4G**: ownership/participations graph (`fact_participations`)
- Financials zijn doorvertaling van NBB; gebruik als cross-check, niet als primaire bron als NBB direct beschikbaar
- Refresh-pattern: delete-then-reimport per investor (zie `fn_reset_odb_participations`)

**SRC_V4G** — V4G eigen entries
- Handmatige analist-collected data uit publieke bronnen
- Append met audit
- Klein volume (47 rijen historisch)

### NARRATIVE (analyst-curated)

**PARTY_INTERVIEW** — Management adjustments via inputsheet
- Analyst captures management-claimed values na interview
- **Categorieën**: NORMALIZATION (eenmalige posten), PROFORMA (acquisitie-effect),
  CORRECTION (echte fout in filings, zeldzaam), OTHER
- **Nooit** een "verbetering" van NBB; altijd parallel narratief
- Required: `reason`, `interview_date`, `contact_name`, `recorded_by`

**ANALYST_DECISION** — Niet-interview overrides
- Voor zeldzame gevallen waar de analist een waarde forceert zonder management-input
- Required: `reason`, `recorded_by`
- Audit-spoor strenger gehandhaafd

---

## Bron-precedence in `fact_financials` view

```
BE-entity (country_iso2='BE'):
  override > NBB_derived > ODB > PB > V4G > NULL

non-BE entity:
  override > PB > V4G > NULL  (NBB/ODB BE-only)
```

Implementatie via window-ranked CTE in `fact_financials` view definitie.

---

## Onveranderlijke principes

1. **Golden Safe verandert NOOIT historische jaarrekeningen.**
   We voegen alleen interpretatie- en scenario-lagen toe.

2. **NBB is de foto, PB/ODB zijn externe lenzen, inputsheets zijn het verhaal**
   dat management erbij vertelt.
   Duidelijk gescheiden, netjes naast elkaar leesbaar.

3. **Hard delete is niet toegestaan** op overrides.
   Versies via `superseded_by` mechanisme; geschiedenis blijft queryable voor audit.

4. **INSERT-rechten op overrides** verlopen via het standaard V4G RLS-patroon:
   SELECT voor authenticated, ALL voor service_role. De applicatie-laag (adjustment
   ingester) verifieert de senior-analist rol vóór het connecten via service_role voor write.
   `recorded_by` NOT NULL, audit-trail via timestamp.

5. **Doctrine voor "as reported" vs "adjusted"**:
   - **Screening / portfolio scan**: default "as reported" (NBB-derived of best evidence)
   - **DD / waardering**: default "as adjusted" (overrides bovenop evidence)
   - **Excel-export** toont altijd kolommen: A (as_reported) | B (effective/adjusted) | C (verschil + reason)

---

## Terminologie — UI vs DB

In de **DB**: technisch correct namen (`fact_financials_overrides`, `metric_kind`, etc.)

In **UI/Excel/templates**: gebruiker-vriendelijke termen:
- "Adjustments" (niet "overrides")
- "As reported" / "Management-adjusted" / "Analyst view"
- "Effective value" voor de blended kolom
- "Adjustment reason" voor de narrative

---

## Wanneer welke laag te gebruiken — quick reference

| Use case | Bron-rangorde |
|---|---|
| Belgian company financials over 5+ years | NBB direct → ODB → PB |
| Non-BE company financials | PB → (eventueel V4G) |
| Participations / ownership graph | ODB → NBB (Administrators sectie) → KBO |
| Director history point-in-time | NBB (via fact_board_snapshot, W9-α) → ODB → KBO |
| DCF inputs (revenue/EBITDA/CapEx/WC) | NBB-derived (line-granulair); overrides voor management adjustments |
| Comparable companies screen | "as reported" view default; geen overrides toepassen tenzij DD-context |
| M&A valuation memo | "as adjusted" view; expliciet via has_overrides=true filter |
| Audit / due diligence trail | Beide kolommen tonen + reason + recorded_by |

---

## Schema lifecycle

`dim_pcmn_codes` evolueert mee met NBB taxonomy versies. Hoe:

- Nieuwe code in nieuwe taxonomy → INSERT met huidige timestamp
- Code wordt vervangen → UPDATE `deprecated_at`, NOOIT DELETE
- Nieuwe taxonomy versie tracked in `fact_filings.taxonomy_version`
- `fact_financials_lines` accepteert ELKE pcmn_code (geen FK lock); description-lookup via LEFT JOIN

Dit verzekert dat ingestion blijft werken bij elke NBB taxonomy update zonder schema-wijziging.

---

## Phase 2/3 backlog — explicit non-scope items

Deze zaken zijn **bewust uitgesteld** uit W8-core. Ze zijn geen vergeten items maar deliberate scope-discipline. Pak ze op wanneer ze knellen, niet preventief.

### `metric_key` value guard (Phase 2)

`fact_financials_overrides.metric_key` is vandaag een **vrije text-kolom** zonder constraint. Dit was een bewuste keuze om W8-core klein te houden:

- `metric_kind='derived'` → mag elke string zijn die in `fact_financials` view bestaat (bv. `'ebitda_eur_m'`, `'revenue_eur_m'`, etc.)
- `metric_kind='raw_line'` → mag elke string in `dim_pcmn_codes.pcmn_code` zijn

Wat dit kan opleveren in Phase 2:
- **Typo's**: analist typt `'ebita_eur_m'` (mist t) → override gaat de view in maar matcht nooit, dus stilletjes genegeerd
- **Stale references**: een metric column wordt later hernoemd, bestaande overrides verwijzen naar oude naam
- **Inconsistent casing**: `'EBITDA_eur_m'` vs `'ebitda_eur_m'`

Mogelijke Phase-2 oplossingen (kies wanneer concreet probleem optreedt):
1. **Trigger-based validation** — BEFORE INSERT/UPDATE checkt metric_key tegen `fact_financials` kolomnamen of `dim_pcmn_codes.pcmn_code`
2. **Reference dim** — `dim_metrics` tabel met expliciet toegestane metric_keys; FK lock
3. **Application-layer validation** — adjustment ingester verifieert vóór INSERT
4. **Quarterly cleanup query** — vind overrides waarvan metric_key niet bestaat, log waarschuwing

Aanbeveling: **start met optie 3** (cheapest, in de adjustment ingester) zodra die gebouwd wordt. Optie 1 of 2 alleen als 3 niet voldoende blijkt.

### Andere Phase 2/3 items

- **W8-plus codes**: ~30 extra PCMN codes voor equity decompositie, debt by maturity, intangibles split, etc. → uitbreiden `dim_pcmn_codes` seed wanneer een analist erom vraagt
- **W9-α directors**: `fact_board_snapshot` uit Administrators sectie van JSON-XBRL
- **W9-γ participations**: cleanup en consolidatie van `fact_participations`
- **W10 archive**: pre-2022 filings via Authentic Archive endpoint
- **W11 auditor**: niet beschikbaar in NBB API; alternatieve bron-investigatie nodig (KBO publicatieblad of filing PDF)
- **PB-specific quirks**: PB rapporteert soms al "adjusted EBITDA" — moeten we die markeren als pre-normalized in evidence?
- **Multi-currency**: huidige stack is EUR-only via fx normalization; voor non-BE entities met USD/GBP rapportage zou expliciete `currency_native` op evidence helpen
