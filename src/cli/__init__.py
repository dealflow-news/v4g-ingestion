"""V4G ingestion CLI tools — operator commands.

Three-act doctrine (see docs/three_act_doctrine.md):
  • ingest_*  → "Ingest into Supabase"  : source → staging/canonical
  • export_*  → "Export out of Supabase" : DB views → analyst delivery
  • Analysis itself lives in DB views, not in client code.
"""
