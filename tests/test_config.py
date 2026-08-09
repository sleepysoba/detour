import pytest

from detour.config import ConfigError, load_config


def test_config_uses_locked_defaults(monkeypatch):
    for name in (
        "DATABRICKS_CHAT_MODEL",
        "EMBEDDING_MODEL",
        "EMBEDDING_DIMENSIONS",
        "AUTO_INIT_DB",
    ):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config["DATABRICKS_CHAT_MODEL"] == "system.ai.meta-llama-3-3-70b-instruct"
    assert config["EMBEDDING_MODEL"] == "sentence-transformers/all-MiniLM-L6-v2"
    assert config["EMBEDDING_DIMENSIONS"] == 384
    assert config["AUTO_INIT_DB"] is True


def test_config_parses_false_auto_init(monkeypatch):
    monkeypatch.setenv("AUTO_INIT_DB", "false")

    assert load_config()["AUTO_INIT_DB"] is False


def test_config_rejects_embedding_dimension_drift(monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "768")

    with pytest.raises(ConfigError, match="must be 384"):
        load_config()
