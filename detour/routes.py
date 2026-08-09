"""Phase 0 page and health routes."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template

from detour.db import LakebaseError, check_connection

pages = Blueprint("pages", __name__)


def _connection_options() -> dict:
    return {
        "database_url": current_app.config["LAKEBASE_URL"],
        "secret_scope": current_app.config["LAKEBASE_SECRET_SCOPE"],
        "secret_key": current_app.config["LAKEBASE_SECRET_KEY"],
        "connect_timeout": current_app.config["LAKEBASE_CONNECT_TIMEOUT"],
    }


@pages.get("/")
def index():
    return render_template("index.html")


@pages.get("/health")
def health():
    """Return application and database health without configuration details."""
    try:
        database = check_connection(**_connection_options())
    except LakebaseError:
        current_app.logger.warning("Lakebase health check failed.")
        return jsonify(status="degraded", application="ok", database="unavailable"), 503

    if not database["ok"]:
        return jsonify(status="degraded", application="ok", database="unavailable"), 503
    return jsonify(status="ok", application="ok", database="ok")
