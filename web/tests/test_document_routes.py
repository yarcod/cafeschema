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


@pytest.fixture
def empty_docs_app(tmp_path):
    """An app whose documents directory doesn't exist — nothing uploaded yet."""
    config = AppConfig(
        secret_key="test-secret", db_path=":memory:", api_key="test-api-key",
        smtp_host="localhost", smtp_port=587, smtp_user="u", smtp_password="p",
        from_address="noreply@exempel.se",
        documents_dir=str(tmp_path / "finns-inte"),
    )
    application = create_app(config)
    application.config.update(TESTING=True)
    return application


def test_missing_documents_directory_renders_an_empty_page(empty_docs_app):
    from duty_web.models import Person

    session = empty_docs_app.extensions["duty_web_session_factory"]()
    person = Person(name="Tova", email="tova@exempel.se")
    session.add(person)
    session.commit()
    client = empty_docs_app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True

    response = client.get("/dokument")

    assert response.status_code == 200
    assert "Inga dokument" in response.get_data(as_text=True)


def test_a_duty_links_straight_to_its_own_instruction(app):
    from duty_web.routes.document_routes import document_url_for_duty

    with app.test_request_context():
        url = document_url_for_duty("Bästkustcupen")

    assert url == "/dokument/Cafeteria_Instruktion_MIH.pdf"


def test_a_duty_without_an_instruction_links_to_the_index(app):
    from duty_web.routes.document_routes import document_url_for_duty

    with app.test_request_context():
        assert document_url_for_duty("Åby Julmarknad") == "/dokument"


def test_a_duty_whose_instruction_is_not_uploaded_yet_links_to_the_index(
    empty_docs_app,
):
    """Better the index than a 404 on the morning of the shift."""
    from duty_web.routes.document_routes import document_url_for_duty

    with empty_docs_app.test_request_context():
        assert document_url_for_duty("Bästkustcupen") == "/dokument"


def test_an_instruction_link_can_be_built_for_a_mail(app):
    from duty_web.routes.document_routes import document_url_for_duty

    with app.test_request_context(base_url="https://duty-swap-webapp.fly.dev"):
        url = document_url_for_duty("Cafépass", external=True)

    assert url == (
        "https://duty-swap-webapp.fly.dev/dokument/"
        "Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf"
    )


def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def _team_file(documents_dir, slug, name, content=b"%PDF-1.4 team"):
    folder = documents_dir / slug
    folder.mkdir(exist_ok=True)
    (folder / name).write_bytes(content)


def test_a_teams_own_instruction_wins_over_the_shared_one(app, documents_dir):
    from duty_web.routes.document_routes import document_url_for_duty

    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")

    with app.test_request_context():
        assert document_url_for_duty("Bästkustcupen", "p13") == (
            "/dokument/p13/Cafeteria_Instruktion_MIH.pdf"
        )
        assert document_url_for_duty("Bästkustcupen", "f14-bla") == (
            "/dokument/Cafeteria_Instruktion_MIH.pdf"
        )


def test_a_team_without_its_own_copy_uses_the_shared_instruction(app):
    from duty_web.routes.document_routes import document_url_for_duty

    with app.test_request_context():
        assert document_url_for_duty("Cafépass", "p13") == (
            "/dokument/Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf"
        )


def test_an_instruction_only_one_team_has_is_not_offered_to_the_others(
    app, documents_dir
):
    from duty_web.routes.document_routes import document_url_for_duty

    (documents_dir / "Cafeteria_Instruktion_MIH.pdf").unlink()
    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")

    with app.test_request_context():
        assert document_url_for_duty("Bästkustcupen", "p13").startswith("/dokument/p13/")
        assert document_url_for_duty("Bästkustcupen", "f14-bla") == "/dokument"


def test_a_parent_can_download_their_teams_document(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["person"])

    assert client.get("/dokument/p13/Kiosk_P13.pdf").status_code == 200


def test_another_teams_document_is_not_found_by_any_route(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["other_parent"])  # F14 Blå only

    for url in (
        "/dokument/p13/Kiosk_P13.pdf",
        "/dokument/p13%2FKiosk_P13.pdf",
        "/dokument/f14-bla/../p13/Kiosk_P13.pdf",
        "/dokument/f14-bla/..%2Fp13%2FKiosk_P13.pdf",
    ):
        assert client.get(url).status_code == 404, url


def test_document_list_groups_team_and_shared_documents(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["person"])

    body = client.get("/dokument").get_data(as_text=True)

    assert "Kiosk P13" in body
    assert "/dokument/p13/Kiosk_P13.pdf" in body
    assert "Cafeteria Instruktion MIH" in body
    assert "Gemensamma" in body


def test_other_teams_documents_are_not_listed(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Kiosk_P13.pdf")
    _login(client, seeded["other_parent"])

    assert "Kiosk P13" not in client.get("/dokument").get_data(as_text=True)


def test_a_shared_document_your_only_team_replaces_is_listed_once(
    client, seeded, second_team, documents_dir, session_factory
):
    from duty_web.models import Person

    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")
    nora = session_factory().query(Person).filter_by(email="nora@exempel.se").one()
    _login(client, nora)  # P13 only

    body = client.get("/dokument").get_data(as_text=True)

    assert body.count("Cafeteria Instruktion MIH") == 1


def test_a_shared_document_only_one_of_your_teams_replaces_stays_listed(
    client, seeded, second_team, documents_dir
):
    _team_file(documents_dir, "p13", "Cafeteria_Instruktion_MIH.pdf")
    _login(client, seeded["person"])  # F14 Blå (uses shared) and P13 (own copy)

    body = client.get("/dokument").get_data(as_text=True)

    assert body.count("Cafeteria Instruktion MIH") == 2
