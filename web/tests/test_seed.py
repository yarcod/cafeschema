from datetime import date, time

import pytest
from openpyxl import Workbook

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, Team
from duty_web.seed import SeedError, import_schedule


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def write_xlsx(tmp_path, rows):
    wb = Workbook()
    ws = wb.active
    ws.append(
        ["ar", "syssla", "arena", "station", "vecka", "datum", "veckodag",
         "tid", "namn", "epost", "anteckning"]
    )
    for row in rows:
        ws.append(row)
    path = tmp_path / "schema.xlsx"
    wb.save(path)
    return path


def test_import_creates_person_and_slot(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()

    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se",
         "Hämta nyckel helgen innan"],
    ])

    count = import_schedule(session, path, team_id=team.id)

    assert count == 1
    slot = session.query(Slot).one()
    assert slot.date == date(2026, 1, 16)
    assert slot.start_time == time(18, 0)
    assert slot.end_time == time(21, 0)
    assert slot.station == "Cafe"
    assert slot.note == "Hämta nyckel helgen innan"
    person = session.query(Person).one()
    assert person.email == "tova@exempel.se"
    assert person.name == "Alva Exempel"
    assert slot.person_id == person.id


def test_import_reuses_existing_person_by_email(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()

    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se", ""],
        [2026, "Arena värdskap", "Wallenstam arena", "Entré", 4, "2026-01-23",
         "Fredag", "17:30-20:30", "Alva Exempel", "tova@exempel.se", ""],
    ])

    import_schedule(session, path, team_id=team.id)

    assert session.query(Person).count() == 1
    assert session.query(Slot).count() == 2


def test_import_wipes_previous_slots_but_keeps_people(tmp_path):
    """A person's id is what a logged-in session stores, and SQLite reissues
    the ids of deleted rows, so a re-import must never recreate people."""
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Gammal Person", "gammal@exempel.se", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)
    gammal_id = session.query(Person).one().id

    new_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Kiosk", 5, "2026-02-06",
         "Fredag", "18:00-21:00", "Ny Person", "ny@exempel.se", ""],
    ])
    import_schedule(session, new_path, team_id=team.id)

    assert session.query(Slot).count() == 1
    assert session.query(Slot).one().station == "Kiosk"
    gammal = session.get(Person, gammal_id)
    assert gammal.email == "gammal@exempel.se"
    assert session.query(Person).filter_by(email="ny@exempel.se").one().id != gammal_id


def test_import_updates_the_name_of_an_existing_contact(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Gammalt Namn", "samma@exempel.se", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)
    person_id = session.query(Person).one().id

    new_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Nytt Namn", "samma@exempel.se", ""],
    ])
    import_schedule(session, new_path, team_id=team.id)

    person = session.query(Person).one()
    assert person.id == person_id
    assert person.name == "Nytt Namn"


def test_import_rejects_a_nonexistent_team_id_without_wiping_existing_data(tmp_path):
    """I8: nothing else creates a Team row, and SQLite's FK enforcement is
    off by default, so importing against a bogus team_id used to silently
    write slots with a dangling team_id. It must now fail loudly, and
    before any existing Slot/Person data is wiped."""
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Gammal Person", "gammal@exempel.se", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)

    bad_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-02-06",
         "Fredag", "18:00-21:00", "Ny Person", "ny@exempel.se", ""],
    ])

    with pytest.raises(SeedError, match="team_id"):
        import_schedule(session, bad_path, team_id=team.id + 999)

    assert session.query(Person).count() == 1
    assert session.query(Slot).count() == 1
    assert session.query(Person).one().email == "gammal@exempel.se"


def test_import_leaves_slot_unassigned_when_email_missing(tmp_path):
    """A row with no e-post (no contact on file for that person yet) is a
    genuinely unfilled slot, not an import error — Slot.person_id is
    nullable for exactly this case, so the row is kept with no person."""
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Alva Exempel", "", ""],
    ])

    count = import_schedule(session, path, team_id=team.id)

    assert count == 1
    assert session.query(Person).count() == 0
    slot = session.query(Slot).one()
    assert slot.person_id is None
    assert "Alva Exempel" in slot.note


def test_import_rejects_missing_namn(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "", "tova@exempel.se", ""],
    ])

    with pytest.raises(SeedError, match="namn"):
        import_schedule(session, path, team_id=team.id)


def test_import_rejects_malformed_date(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026/01/16",
         "Fredag", "18:00-21:00", "Alva Exempel", "tova@exempel.se", ""],
    ])

    with pytest.raises(SeedError, match="datum"):
        import_schedule(session, path, team_id=team.id)


def test_import_rejects_malformed_time(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18.00-21.00", "Alva Exempel", "tova@exempel.se", ""],
    ])

    with pytest.raises(SeedError, match="tid"):
        import_schedule(session, path, team_id=team.id)


def test_import_rolls_back_on_error(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()

    # First, successfully import one person/slot
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Gammal Person", "gammal@exempel.se", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)
    assert session.query(Person).count() == 1
    assert session.query(Slot).count() == 1
    old_person = session.query(Person).one()

    # Try to import with a malformed date (should fail)
    bad_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "invalid-date",
         "Fredag", "18:00-21:00", "Ny Person", "ny@exempel.se", ""],
    ])

    with pytest.raises(SeedError, match="datum"):
        import_schedule(session, bad_path, team_id=team.id)

    # Database should be rolled back to the previous state
    # The wipe + failed import is rolled back, so old data is preserved
    assert session.query(Person).count() == 1
    assert session.query(Slot).count() == 1
    assert session.query(Person).one().email == old_person.email


def test_import_keeps_a_slot_whose_date_is_not_set_yet(tmp_path):
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()

    path = write_xlsx(tmp_path, [
        [2027, "Arena värdskap vinter", "Wallenstam arena", "", "", "",
         "", "18:00-21:00", "Alva Exempel", "tova@exempel.se", ""],
    ])

    count = import_schedule(session, path, team_id=team.id)

    assert count == 1
    slot = session.query(Slot).one()
    assert slot.date is None
    assert slot.duty_name == "Arena värdskap vinter"
    assert slot.person_id == session.query(Person).one().id
