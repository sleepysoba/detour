import pytest

from detour import create_app


@pytest.fixture()
def app():
    return create_app({"TESTING": True, "AUTO_INIT_DB": False})


@pytest.fixture()
def client(app):
    return app.test_client()
