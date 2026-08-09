"""Gunicorn configuration for Databricks Apps."""

import os


def positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


port = positive_int("DATABRICKS_APP_PORT", 8000)

bind = f"0.0.0.0:{port}"
workers = positive_int("GUNICORN_WORKERS", 2)
threads = positive_int("GUNICORN_THREADS", 4)
timeout = positive_int("GUNICORN_TIMEOUT", 120)

accesslog = "-"
errorlog = "-"
capture_output = True
