"""Safe Lakebase/PostgreSQL connectivity and schema initialization."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg2
from psycopg2.extras import RealDictCursor


class LakebaseError(RuntimeError):
    """Normalized database error that never includes credentials."""

    code = "DATABASE_ERROR"

    def __init__(self, message: str = "Lakebase operation failed."):
        super().__init__(message)
        self.message = message


def _validate_database_url(value: str) -> str:
    database_url = value.strip()
    if not database_url.startswith(("postgresql://", "postgres://")):
        raise LakebaseError("Lakebase connection configuration is invalid.")
    return database_url


def _lakebase_url(
    database_url: str = "",
    secret_scope: str = "database",
    secret_key: str = "lakebase-url",
) -> str:
    """Resolve a direct URL first, then fall back to Databricks Secrets."""
    if database_url.strip():
        return _validate_database_url(database_url)

    try:
        from databricks.sdk import WorkspaceClient

        secret = WorkspaceClient().secrets.get_secret(scope=secret_scope, key=secret_key)
        encoded_value = secret.value
        if not encoded_value:
            raise ValueError("empty secret")
        decoded_value = base64.b64decode(encoded_value).decode("utf-8")
        return _validate_database_url(decoded_value)
    except LakebaseError:
        raise
    except Exception as exc:
        raise LakebaseError("Could not read the Lakebase connection secret.") from exc


@contextmanager
def get_connection(
    *,
    database_url: str = "",
    secret_scope: str = "database",
    secret_key: str = "lakebase-url",
    connect_timeout: int = 10,
) -> Iterator[Any]:
    """Yield a psycopg2 connection with dictionary-like rows."""
    resolved_url = _lakebase_url(database_url, secret_scope, secret_key)
    try:
        connection = psycopg2.connect(
            resolved_url,
            cursor_factory=RealDictCursor,
            connect_timeout=connect_timeout,
        )
    except Exception as exc:
        raise LakebaseError("Could not connect to Lakebase.") from exc

    try:
        yield connection
    finally:
        connection.close()


def run_query(
    sql: str,
    params: tuple | list | dict | None = None,
    **connection_options: Any,
) -> list[dict]:
    """Run a parameterized read query and return dictionaries."""
    try:
        with get_connection(**connection_options) as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
    except LakebaseError:
        raise
    except Exception as exc:
        raise LakebaseError("Lakebase query failed.") from exc


def run_write(
    sql: str,
    params: tuple | list | dict | None = None,
    **connection_options: Any,
) -> int:
    """Run a parameterized write and return the affected row count."""
    try:
        with get_connection(**connection_options) as connection, connection.cursor() as cursor:
            cursor.execute(sql, params)
            connection.commit()
            return cursor.rowcount
    except LakebaseError:
        raise
    except Exception as exc:
        raise LakebaseError("Lakebase write failed.") from exc


def check_connection(**connection_options: Any) -> dict[str, bool]:
    """Verify Lakebase is reachable without returning connection details."""
    rows = run_query("SELECT %s AS ok", (1,), **connection_options)
    return {"ok": bool(rows and rows[0].get("ok") == 1)}


def init_schema(schema_path: Path | None = None, **connection_options: Any) -> None:
    """Apply the idempotent Detour schema under a transaction-scoped lock."""
    path = schema_path or Path(__file__).resolve().parents[1] / "sql" / "01_schema.sql"

    try:
        schema_sql = path.read_text(encoding="utf-8")
        with get_connection(**connection_options) as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("detour-schema-v1",))
            cursor.execute(schema_sql)
            connection.commit()
    except LakebaseError:
        raise
    except (OSError, UnicodeError) as exc:
        raise LakebaseError("Could not read the Detour schema.") from exc
    except Exception as exc:
        raise LakebaseError("Could not initialize the Lakebase schema.") from exc
