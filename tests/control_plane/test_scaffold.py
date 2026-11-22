import logging

from fastapi.testclient import TestClient
from xmux_control.api import create_app
from xmux_control.logging import SafeJsonFormatter
from xmux_control.settings import Settings


def test_liveness_does_not_require_database(monkeypatch):
    monkeypatch.setattr("xmux_control.health.dependencies", lambda _: {"postgres": False, "redis": False})
    client = TestClient(create_app(Settings("postgresql://unused", "redis://unused")))
    assert client.get("/health/live").status_code == 200
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert "unused" not in response.text


def test_safe_logs_drop_message_and_exception_content():
    record = logging.LogRecord("test", logging.ERROR, "", 1, "secret password", (), None)
    record.authorization = "Bearer secret"
    record.event = "request_failed"
    assert "secret" not in SafeJsonFormatter().format(record)


def test_settings_do_not_expose_credentials_in_repr():
    value = Settings("postgresql://secret", "redis://secret", cursor_secret="secret")
    assert "secret" not in repr(value)
