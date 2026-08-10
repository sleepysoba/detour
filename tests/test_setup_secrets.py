from types import SimpleNamespace

import pytest

import setup_secrets


class FakeSecrets:
    def __init__(self, scopes=()):
        self.scopes = list(scopes)
        self.created_scopes = []
        self.stored_secrets = []
        self.acls = []

    def list_scopes(self):
        return [SimpleNamespace(name=name) for name in self.scopes]

    def create_scope(self, *, scope):
        self.created_scopes.append(scope)

    def put_secret(self, *, scope, key, string_value):
        self.stored_secrets.append((scope, key, string_value))

    def put_acl(self, *, scope, principal, permission):
        self.acls.append((scope, principal, permission))


def test_setup_stores_token_in_separate_scope_without_acl(monkeypatch, capsys):
    secrets = FakeSecrets(scopes=(setup_secrets.SCOPE,))
    prompts = iter(("postgresql://db-user:db-password@example.com/detour", "secret-token"))
    monkeypatch.setattr(setup_secrets, "WorkspaceClient", lambda: SimpleNamespace(secrets=secrets))
    monkeypatch.setattr(setup_secrets.getpass, "getpass", lambda prompt: next(prompts))

    setup_secrets.main()

    assert secrets.created_scopes == [setup_secrets.TOKEN_SCOPE]
    assert secrets.stored_secrets == [
        (
            setup_secrets.SCOPE,
            setup_secrets.KEY,
            "postgresql://db-user:db-password@example.com/detour",
        ),
        (setup_secrets.TOKEN_SCOPE, setup_secrets.TOKEN_KEY, "secret-token"),
    ]
    assert len(secrets.acls) == 1
    assert secrets.acls[0][0] == setup_secrets.SCOPE
    output = capsys.readouterr().out
    assert "db-password" not in output
    assert "secret-token" not in output


def test_setup_rejects_empty_token_before_storing_secrets(monkeypatch):
    secrets = FakeSecrets(scopes=(setup_secrets.SCOPE, setup_secrets.TOKEN_SCOPE))
    prompts = iter(("postgresql://example.com/detour", "  "))
    monkeypatch.setattr(setup_secrets, "WorkspaceClient", lambda: SimpleNamespace(secrets=secrets))
    monkeypatch.setattr(setup_secrets.getpass, "getpass", lambda prompt: next(prompts))

    with pytest.raises(ValueError, match="cannot be empty"):
        setup_secrets.main()

    assert secrets.stored_secrets == []
