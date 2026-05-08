"""V4G ingestion — Flask analyst UI.

Phase 1 skeleton: /healthz and / (minimal dashboard).
Web-α (sprint 1): parties blueprint registered — see src/web/routes/parties.py.
Real workers come online in subsequent phases.
"""
from __future__ import annotations

import logging
import os

from flask import Flask, render_template

log = logging.getLogger(__name__)


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET", "dev-only-change-me")

    # ─── Routes ──────────────────────────────────────────────────────────

    @app.route("/healthz")
    def healthz() -> tuple[dict, int]:
        """Liveness probe for Render."""
        return {"status": "ok", "version": "0.1.0"}, 200

    @app.route("/")
    def index() -> str:
        return render_template("dashboard.html", version="0.1.0")

    # Blueprints — resource-named (parties, financials, ...). Registered as
    # they land. See src/web/routes/__init__.py for the contract.
    from src.web.routes import parties
    app.register_blueprint(parties.bp)

    log.info("flask app ready · routes: %s", [str(r) for r in app.url_map.iter_rules()])
    return app


# Gunicorn entrypoint
app = create_app()


if __name__ == "__main__":
    # Local dev: `python -m src.web.app`
    app.run(host="0.0.0.0", port=5000, debug=bool(int(os.environ.get("FLASK_DEBUG", "0"))))
