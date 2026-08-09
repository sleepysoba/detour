from unittest.mock import patch

from detour.db import LakebaseError


def test_landing_page_renders_jinja_and_static_assets(client):
    response = client.get("/")

    assert response.status_code == 200
    assert b"Build a trip ready for the unexpected" in response.data
    assert b"/static/css/app.css" in response.data
    assert b"/static/js/app.js" in response.data

    stylesheet = client.get("/static/css/app.css")
    script = client.get("/static/js/app.js")
    assert stylesheet.status_code == 200
    assert stylesheet.content_type.startswith("text/css")
    assert script.status_code == 200
    assert "javascript" in script.content_type


def test_health_reports_application_and_database(client):
    with patch("detour.routes.check_connection", return_value={"ok": True}):
        response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "application": "ok",
        "database": "ok",
        "status": "ok",
    }


def test_health_failure_is_sanitized(client):
    with patch(
        "detour.routes.check_connection",
        side_effect=LakebaseError("Could not connect to Lakebase."),
    ):
        response = client.get("/health")

    assert response.status_code == 503
    assert response.get_json() == {
        "application": "ok",
        "database": "unavailable",
        "status": "degraded",
    }
    assert b"postgres" not in response.data.lower()
