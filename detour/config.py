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


def _databricks_ai_base_url() -> str:
    explicit_base_url = os.getenv("DATABRICKS_AI_BASE_URL", "").strip()
    if explicit_base_url:
        return explicit_base_url

    databricks_host = os.getenv("DATABRICKS_HOST", "").strip()
    if databricks_host:
        return f"{databricks_host.rstrip('/')}/ai-gateway/mlflow/v1"
    return ""


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
        "OPEN_METEO_TIMEOUT_SECONDS": _positive_int("OPEN_METEO_TIMEOUT_SECONDS", 10),
        "WIKIMEDIA_TIMEOUT_SECONDS": _positive_int("WIKIMEDIA_TIMEOUT_SECONDS", 15),
        "WIKIMEDIA_USER_AGENT": os.getenv(
            "WIKIMEDIA_USER_AGENT", "DetourCapstone/0.1 (educational project)"
        ).strip(),
        "DATABRICKS_TOKEN": os.getenv("DATABRICKS_TOKEN", "").strip(),
        "DATABRICKS_AI_BASE_URL": _databricks_ai_base_url(),
        "DATABRICKS_CHAT_MODEL": os.getenv(
            "DATABRICKS_CHAT_MODEL", "system.ai.meta-llama-3-3-70b-instruct"
        ).strip(),
        "DATABRICKS_LLM_TIMEOUT_SECONDS": _positive_int(
            "DATABRICKS_LLM_TIMEOUT_SECONDS", 120
        ),
        "EMBEDDING_MODEL": os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
        ).strip(),
        "EMBEDDING_DIMENSIONS": embedding_dimensions,
        "EMBEDDING_THREADS": _positive_int("EMBEDDING_THREADS", 2),
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
    noisy_loggers = (
        "httpcore",
        "httpx",
        "huggingface_hub",
        "sentence_transformers",
        "transformers",
    )
    for noisy_logger in noisy_loggers:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)
