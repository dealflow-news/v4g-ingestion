"""Service layer — business logic shared between CLI and web surfaces.

Use-case orchestration lives here. CLI scripts (`src.cli.*`) and Flask
routes (`src.web.routes.*`) should be thin wrappers around these services.

Reading party data:        src.services.party_query
Reading & exporting finan: src.services.financial_export
"""
