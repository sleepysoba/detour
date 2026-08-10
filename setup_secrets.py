"""One-time Databricks Secrets setup for Detour.

Run after ``databricks auth login``. The Lakebase URL is read with a secure
prompt and is never printed.
"""

from __future__ import annotations

import getpass
import os

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace

SCOPE = os.getenv("LAKEBASE_SECRET_SCOPE", "database")
KEY = os.getenv("LAKEBASE_SECRET_KEY", "lakebase-url")
READ_PRINCIPAL = os.getenv("DETOUR_SECRET_READ_PRINCIPAL", "users")
TOKEN_SCOPE = os.getenv("DETOUR_TOKEN_SECRET_SCOPE", "detour-app")
TOKEN_KEY = os.getenv("DETOUR_TOKEN_SECRET_KEY", "databricks-token")


def main() -> None:
    if TOKEN_SCOPE == SCOPE:
        raise ValueError("The Databricks token secret scope must be separate from the Lakebase scope.")

    client = WorkspaceClient()

    existing_scopes = {scope.name for scope in client.secrets.list_scopes()}
    if SCOPE not in existing_scopes:
        client.secrets.create_scope(scope=SCOPE)
        print(f"Created secret scope: {SCOPE}")
    else:
        print(f"Secret scope already exists: {SCOPE}")

    if TOKEN_SCOPE not in existing_scopes:
        client.secrets.create_scope(scope=TOKEN_SCOPE)
        print(f"Created secret scope: {TOKEN_SCOPE}")
    else:
        print(f"Secret scope already exists: {TOKEN_SCOPE}")

    lakebase_url = getpass.getpass("Paste your Lakebase PostgreSQL connection URL: ").strip()
    if not lakebase_url.startswith(("postgresql://", "postgres://")):
        raise ValueError("Expected a PostgreSQL connection URL beginning with postgresql:// or postgres://")

    databricks_token = getpass.getpass("Paste your Databricks personal access token: ").strip()
    if not databricks_token:
        raise ValueError("Databricks personal access token cannot be empty.")

    client.secrets.put_secret(scope=SCOPE, key=KEY, string_value=lakebase_url)
    client.secrets.put_acl(
        scope=SCOPE,
        principal=READ_PRINCIPAL,
        permission=workspace.AclPermission.READ,
    )
    client.secrets.put_secret(
        scope=TOKEN_SCOPE,
        key=TOKEN_KEY,
        string_value=databricks_token,
    )
    print(f"Stored {SCOPE}/{KEY}. The secret value was not printed.")
    print(f"Stored {TOKEN_SCOPE}/{TOKEN_KEY}. The secret value was not printed.")


if __name__ == "__main__":
    main()
