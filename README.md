# v4g-ingestion

V4G enrichment platform — unified pipeline for Golden Safe party enrichment.
Consolidates NBB financials ingestion, KBO director mapping, and actor
classification into one event-driven + cadence-driven runner.

## Status

**Phase 1 skeleton** — infrastructure and `gs_enrichment` schema.
Domain modules (NBB parsing, aggregation, writers) arrive in the next commits.

## Architecture

```
Render (Python/Flask)          Supabase Edge (Deno)       Supabase Postgres
─────────────────────          ─────────────────────      ─────────────────
• Flask analyst UI             • kbo-proxy (existing)     • Golden Safe (GS)
• Enrichment workers              /search, /directors     • gs_enrichment
  (nbb_financials,                                          (queue, policy,
   kbo_directors,                                           run_log,
   actor_classification)                                    object_log,
• CLI (cli/enrich.py)                                       step_log)
• Cron sweep
```

## Directory structure

```
src/
├── domain/              Pure logic, no side effects
│   ├── nbb/             NBB XBRL parsing + taxonomy + aggregation
│   └── kbo/             KBO SOAP wrappers (calls kbo-proxy Edge Function)
├── canonical/           Pydantic models for each GS fact table
├── persistence/         Supabase clients + writers
├── enrichment/          Queue + runner + workers
│   └── workers/
├── web/                 Flask UI
│   ├── routes/
│   └── templates/
└── cli/                 Developer + operator commands

sql/                     Database migrations (GS-MIGRATE-021+)
tests/                   pytest
```

## Local development

Prerequisites: Python 3.11+, Docker (optional).

```bash
# 1. Clone
git clone git@github.com:dealflow-news/v4g-ingestion.git
cd v4g-ingestion

# 2. Environment
cp .env.example .env
# Edit .env with real credentials:
#   - SUPABASE_URL (https://rirkgpsdcaxnowwmliof.supabase.co)
#   - SUPABASE_SERVICE_ROLE_KEY (service role — NEVER commit)
#   - SUPABASE_ANON_KEY (public, but .env anyway)
#   - KBO_PROXY_PATH  (default: /functions/v1/kbo-proxy)
#   - NBB_API_KEY (CBSO subscription key)

# 3. Install dependencies
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 4. Run tests
pytest

# 5. Run database migration (in Supabase SQL editor)
# Execute sql/GS-MIGRATE-021.sql against production GS.
# Verify section at the bottom should show 5 tables, 5 policies, 2 functions.

# 6. Run the web UI
flask --app src.web.app run --port 5000
# Open http://localhost:5000

# 7. Run the worker (separate terminal)
python -m src.enrichment.runner

# 8. Manual enrichment via CLI
python -m src.cli.enrich 0459499688 --types nbb_financials
```

## Docker

```bash
docker compose up --build
# Web UI at http://localhost:5000
# Worker runs in background service
```

## Deployment (Render)

Push to `main` triggers auto-deploy via `render.yaml` declarative config.
Services:
- **Web service** — Flask UI, exposed HTTPS endpoint
- **Worker service** — enrichment runner, background process
- **Cron job** — nightly stale-party sweep

Preview environments are created per PR.

## Phase roadmap

- **Phase 1 (current)**: skeleton, `gs_enrichment` schema, deploy pipeline
- **Phase 2**: NBB domain port from `v4g_accounts` → first worker live
- **Phase 3**: KBO directors worker via existing Edge Function
- **Phase 4**: Actor classification worker (SRC_KBO auto-derive)
- **Phase 5**: Event triggers (GS-MIGRATE-022) on party_signal_observations,
              canonical_deals, fact_alert
- **Phase 6**: Supabase Auth, rollback UI, Valuatrix API consumer

## Related documents

- **`CLAUDE.md`** in the separate `golden-safe-repo` — doctrine
- **`GOLDEN_SAFE_SOP.md`** — operational playbooks
- **`GOLDEN_SAFE_PROJECT_INSTRUCTIONS.md`** — Claude Project config

## License

Private. V4G internal.
