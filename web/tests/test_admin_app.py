import io

from openpyxl import Workbook

from duty_web.admin_app import create_admin_app
from duty_web.config import AppConfig
from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Slot, Team


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
               "tid", "namn", "epost", "anteckning"])
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer


def test_admin_import_creates_slots():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (xlsx_bytes([
            [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
             "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se", ""],
        ]), "schema.xlsx"),
    }
    response = client.post("/admin/import", data=data, content_type="multipart/form-data")

    assert response.status_code == 200
    assert session.query(Slot).count() == 1


def test_admin_import_reports_seed_errors_as_400():
    app, session_factory = make_app_and_session()
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    client = app.test_client()

    data = {
        "team_id": str(team.id),
        "file": (xlsx_bytes([
            [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026/01/16",
             "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se", ""],
        ]), "schema.xlsx"),
    }
    response = client.post("/admin/import", data=data, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "datum" in response.get_data(as_text=True)
