"""Detour Flask application factory."""

from __future__ import annotations

import logging

from dotenv import load_dotenv
from flask import Flask

from detour.config import configure_logging, load_config
from detour.db import LakebaseError, init_schema


def create_app(test_config: dict | None = None) -> Flask:
    """Create and configure the Detour application."""
    load_dotenv()

    app = Flask(
        __name__,
        template_folder="../templates",
        static_folder="../static",
    )
    app.config.from_mapping(load_config())
    if test_config:
        app.config.update(test_config)

    configure_logging(app.config["LOG_LEVEL"])

    from detour.routes import pages

    app.register_blueprint(pages)

    if app.config["AUTO_INIT_DB"]:
        try:
            init_schema(
                database_url=app.config["LAKEBASE_URL"],
                secret_scope=app.config["LAKEBASE_SECRET_SCOPE"],
                secret_key=app.config["LAKEBASE_SECRET_KEY"],
                connect_timeout=app.config["LAKEBASE_CONNECT_TIMEOUT"],
            )
            app.logger.info("Lakebase schema initialization completed.")
        except LakebaseError as exc:
            # Keep the process alive so /health can report a safe degraded state.
            app.logger.error("Lakebase schema initialization failed: %s", exc)

    return app


__all__ = ["create_app"]
