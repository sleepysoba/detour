"""Environment-backed configuration and logging for Detour."""

from __future__ import annotations

import logging
import os


class ConfigError(RuntimeError):
    """Raised when a Detour environment value is invalid."""


def _boolean(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    value = raw_value.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean value.")


def _positive_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be a positive integer.")
    return value


def load_config() -> dict:
    """Load application configuration without printing sensitive values."""
    embedding_dimensions = _positive_int("EMBEDDING_DIMENSIONS", 384)
    if embedding_dimensions != 384:
        raise ConfigError("EMBEDDING_DIMENSIONS must be 384 for the configured MiniLM model.")

    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in logging.getLevelNamesMapping():
        raise ConfigError("LOG_LEVEL is not a recognized Python logging level.")

    return {
        "LAKEBASE_URL": os.getenv("LAKEBASE_URL", "").strip(),
        "LAKEBASE_SECRET_SCOPE": os.getenv("LAKEBASE_SECRET_SCOPE", "database").strip(),
        "LAKEBASE_SECRET_KEY": os.getenv("LAKEBASE_SECRET_KEY", "lakebase-url").strip(),
        "LAKEBASE_CONNECT_TIMEOUT": _positive_int("LAKEBASE_CONNECT_TIMEOUT", 10),
        "DATABRICKS_TOKEN": os.getenv("DATABRICKS_TOKEN", "").strip(),
        "DATABRICKS_AI_BASE_URL": os.getenv("DATABRICKS_AI_BASE_URL", "").strip(),
        "DATABRICKS_CHAT_MODEL": os.getenv(
            "DATABRICKS_CHAT_MODEL", "system.ai.meta-llama-3-3-70b-instruct"
        ).strip(),
        "EMBEDDING_MODEL": os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ).strip(),
        "EMBEDDING_DIMENSIONS": embedding_dimensions,
        "LOG_LEVEL": log_level,
        "AUTO_INIT_DB": _boolean("AUTO_INIT_DB", True),
    }


def configure_logging(level_name: str) -> None:
    """Configure concise application logging to standard output/error."""
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger().setLevel(getattr(logging, level_name))
