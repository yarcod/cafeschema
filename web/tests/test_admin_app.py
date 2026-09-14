import io

from openpyxl import Workbook

from duty_web.admin_app import create_admin_app
from duty_web.config import AppConfig
from duty_web.models import Person, Player, Slot, Team


def make_app_and_session():
    config = AppConfig(
        secret_key="s", db_path=":memory:", api_key="k",
        smtp_host="h", smtp_port=587, smtp_user="u", smtp_password="p",
        from_address="noreply@exempel.se",
    )
    app = create_admin_app(config)
    session_factory = app.extensions["duty_web_session_factory"]
    return app, session_factory


def xlsx_bytes(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["ar", "syssla", "arena", "station", "vecka", "datum", "veckodag",
               "tid", "barn", "anteckning"])
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def csv_bytes(rows):
    lines = ["parent_name,parent_email,children_on_team"]
    lines += [",".join(row) for row in rows]
    return io.BytesIO("\n".join(lines).encode("utf-8"))


def seeded_team(session, player_name="Tova Exempel"):
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    player = Player(name=player_name, team_id=team.id)
    player.parents = [Person(name="Alva Exempel", email="tova@exempel.se")]
    session.add(player)
    session.commit()
    return team


def test_admin_import_creates_slots():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = seeded_team(session)
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (xlsx_bytes([
            [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
             "Fredag", "18:00-21:00", "Tova Exempel", ""],
        ]), "schema.xlsx"),
    }
    response = client.post("/admin/import", data=data, content_type="multipart/form-data")

    assert response.status_code == 200
    assert session.query(Slot).count() == 1


def test_admin_import_reports_seed_errors_as_400():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = seeded_team(session)
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (xlsx_bytes([
            [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026/01/16",
             "Fredag", "18:00-21:00", "Tova Exempel", ""],
        ]), "schema.xlsx"),
    }
    response = client.post("/admin/import", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "datum" in response.get_data(as_text=True)


def test_admin_roster_creates_players_and_parents():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (csv_bytes([
            ["Hans Ahlqvist", "hans@exempel.se", "Klara Ahlqvist"],
            ["Lena Ahlqvist", "lena@exempel.se", "Klara Ahlqvist"],
        ]), "roster.csv"),
    }
    response = client.post("/admin/roster", data=data, content_type="multipart/form-data")

    assert response.status_code == 200
    assert response.get_json()["players_added"] == 1
    assert response.get_json()["parents_added"] == 2
    assert session.query(Player).one().name == "Klara Ahlqvist"
    assert session.query(Person).count() == 2


def test_admin_roster_reports_a_bad_file_as_400():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (io.BytesIO(b"fel,rubriker\n1,2\n"), "roster.csv"),
    }
    response = client.post("/admin/roster", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "kolumn" in response.get_data(as_text=True)
