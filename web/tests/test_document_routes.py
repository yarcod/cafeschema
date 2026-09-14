"""The documents parents are pointed at from the reminder mail."""

import pytest

from duty_web.app import create_app
from duty_web.config import AppConfig


@pytest.fixture
def docs_app(tmp_path):
    directory = tmp_path / "dokument"
    directory.mkdir()
    (directory / "Kiosk_instruktioner.pdf").write_bytes(b"%PDF-1.4 fake")
    (directory / "hemligheter.env").write_text("SECRET=nej")
    config = AppConfig(
        secret_key="test-secret", db_path=":memory:", api_key="test-api-key",
        smtp_host="localhost", smtp_port=587, smtp_user="u", smtp_password="p",
        from_address="noreply@exempel.se", documents_dir=str(directory),
    )
    application = create_app(config)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def logged_in(docs_app):
    from duty_web.models import Person

    session = docs_app.extensions["duty_web_session_factory"]()
    person = Person(name="Tova", email="tova@exempel.se")
    session.add(person)
    session.commit()
    client = docs_app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True
    return client


def test_document_list_requires_login(docs_app):
    response = docs_app.test_client().get("/dokument")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_downloading_a_document_requires_login(docs_app):
    response = docs_app.test_client().get("/dokument/Kiosk_instruktioner.pdf")

    assert response.status_code == 302


def test_list_shows_readable_names(logged_in):
    body = logged_in.get("/dokument").get_data(as_text=True)

    assert "Kiosk instruktioner" in body
    assert "PDF" in body


def test_list_hides_files_of_other_types(logged_in):
    body = logged_in.get("/dokument").get_data(as_text=True)

    assert "hemligheter" not in body


def test_a_logged_in_parent_can_download(logged_in):
    response = logged_in.get("/dokument/Kiosk_instruktioner.pdf")

    assert response.status_code == 200
    assert response.data == b"%PDF-1.4 fake"


def test_a_file_outside_the_allowlist_is_not_served(logged_in):
    response = logged_in.get("/dokument/hemligheter.env")

    assert response.status_code == 404


def test_path_traversal_is_refused(logged_in):
    response = logged_in.get("/dokument/../config.py")

    assert response.status_code in (403, 404)


def test_missing_documents_directory_renders_an_empty_page(app):
    from duty_web.models import Person

    session = app.extensions["duty_web_session_factory"]()
    person = Person(name="Tova", email="tova@exempel.se")
    session.add(person)
    session.commit()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True

    response = client.get("/dokument")

    assert response.status_code == 200
    assert "Inga dokument" in response.get_data(as_text=True)
