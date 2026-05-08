"""Web route blueprints.

Each module here defines a Flask `bp = Blueprint(...)` registered in
`src.web.app.create_app()`. Blueprints are organized by domain resource:

  parties      — party lookup + search (Web-α sprint 1)
  financials   — party detail page + Excel export (Web-α sprint 1)

Auth + admin routes (later sprints) get their own blueprints alongside.
"""
