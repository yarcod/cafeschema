"""Requests carrying a Host we don't serve are refused.

Without this, ProxyFix's trust in X-Forwarded-Host lets an attacker choose
the domain a victim's magic link points at.
"""

import pytest

from duty_web.app import create_app
from duty_web.config import AppConfig


@pytest.fixture
def hosted_app():
    config = AppConfig(
        secret_key="test-secret", db_path=":memory:", api_key="test-api-key",
        smtp_host="localhost", smtp_port=587, smtp_user="u", smtp_password="p",
        from_address="noreply@exempel.se",
        public_hosts=("cafeschema.example", "duty-swap-webapp.fly.dev"),
    )
    application = create_app(config)
    application.config.update(TESTING=True)
    return application


def test_a_served_host_is_accepted(hosted_app):
    response = hosted_app.test_client().get("/login", headers={"Host": "cafeschema.example"})

    assert response.status_code == 200


def test_a_second_served_host_is_accepted(hosted_app):
    response = hosted_app.test_client().get(
        "/login", headers={"Host": "duty-swap-webapp.fly.dev"}
    )

    assert response.status_code == 200


def test_an_unknown_host_is_refused(hosted_app):
    response = hosted_app.test_client().get("/login", headers={"Host": "evil.example"})

    assert response.status_code == 400


def test_a_forwarded_host_header_cannot_smuggle_an_unknown_host(hosted_app):
    response = hosted_app.test_client().get(
        "/login",
        headers={"Host": "cafeschema.example", "X-Forwarded-Host": "evil.example"},
    )

    assert response.status_code == 400


def test_no_allowlist_configured_serves_any_host(app):
    """Local development sets no PUBLIC_HOSTS and must keep working."""
    response = app.test_client().get("/login", headers={"Host": "localhost:8080"})

    assert response.status_code == 200
