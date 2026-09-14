from datetime import date, time

import pytest
from openpyxl import Workbook

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Player, Slot, Team
from duty_web.seed import SeedError, import_schedule

SEED_HEADER = [
    "ar", "syssla", "arena", "station", "vecka", "datum", "veckodag",
    "tid", "barn", "anteckning",
]


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def seeded_team(session, players=("Tova Exempel",)):
    """A team with a roster already imported — import_schedule's precondition."""
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    for index, name in enumerate(players):
        player = Player(name=name, team_id=team.id)
        player.parents = [
            Person(name=f"Förälder {index}", email=f"foralder{index}@exempel.se")
        ]
        session.add(player)
    session.commit()
    return team


def write_xlsx(tmp_path, rows, name="schema.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.append(SEED_HEADER)
    for row in rows:
        ws.append(row)
    path = tmp_path / name
    wb.save(path)
    return path


def test_import_assigns_the_slot_to_the_named_player(tmp_path):
    session = make_session()
    team = seeded_team(session)

    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Tova Exempel", "Hämta nyckel helgen innan"],
    ])

    count = import_schedule(session, path, team_id=team.id)

    assert count == 1
    slot = session.query(Slot).one()
    assert slot.date == date(2026, 1, 16)
    assert slot.start_time == time(18, 0)
    assert slot.end_time == time(21, 0)
    assert slot.station == "Cafe"
    assert slot.note == "Hämta nyckel helgen innan"
    assert slot.player.name == "Tova Exempel"
    assert slot.child_name == "Tova Exempel"


def test_import_matches_a_player_despite_spacing_and_case(tmp_path):
    session = make_session()
    team = seeded_team(session, players=("Lo BÄCKSON",))

    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "  lo   bäckson ", ""],
    ])

    import_schedule(session, path, team_id=team.id)

    assert session.query(Slot).one().player.name == "Lo BÄCKSON"


def test_import_never_creates_or_changes_people(tmp_path):
    """The roster owns players and parents; the schedule only owns slots."""
    session = make_session()
    team = seeded_team(session)
    before = {(p.id, p.name, p.email) for p in session.query(Person).all()}

    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ])
    import_schedule(session, path, team_id=team.id)

    assert {(p.id, p.name, p.email) for p in session.query(Person).all()} == before
    assert session.query(Player).count() == 1


def test_import_wipes_previous_slots_but_keeps_the_roster(tmp_path):
    """A person's id is what a logged-in session stores, and SQLite reissues
    the ids of deleted rows, so a re-import must never recreate people."""
    session = make_session()
    team = seeded_team(session, players=("Tova Exempel", "Moa Exempel"))
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)
    person_ids = {p.id for p in session.query(Person).all()}

    new_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Kiosk", 5, "2026-02-06",
         "Fredag", "18:00-21:00", "Moa Exempel", ""],
    ], name="ny.xlsx")
    import_schedule(session, new_path, team_id=team.id)

    assert session.query(Slot).count() == 1
    assert session.query(Slot).one().station == "Kiosk"
    assert {p.id for p in session.query(Person).all()} == person_ids


def test_import_rejects_a_nonexistent_team_id_without_wiping_existing_data(tmp_path):
    """I8: nothing else creates a Team row, and SQLite's FK enforcement is
    off by default, so importing against a bogus team_id used to silently
    write slots with a dangling team_id. It must now fail loudly, and
    before any existing slot data is wiped."""
    session = make_session()
    team = seeded_team(session)
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)

    bad_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-02-06",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ], name="fel.xlsx")

    with pytest.raises(SeedError, match="team_id"):
        import_schedule(session, bad_path, team_id=team.id + 999)

    assert session.query(Slot).count() == 1
    assert session.query(Slot).one().date == date(2026, 1, 16)


def test_import_refuses_to_run_before_the_roster_has_been_imported(tmp_path):
    """Otherwise the whole season imports unassigned and nobody can act."""
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.commit()
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ])

    with pytest.raises(SeedError, match="laglistan"):
        import_schedule(session, path, team_id=team.id)


def test_import_leaves_a_slot_unassigned_when_the_player_is_not_on_the_roster(tmp_path):
    """A player the roster hasn't caught up with yet still has a real shift —
    Slot.player_id is nullable for exactly this case, so the row is kept and
    the intended name survives in the note."""
    session = make_session()
    team = seeded_team(session)
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Okänd Spelare", ""],
    ])

    count = import_schedule(session, path, team_id=team.id)

    assert count == 1
    slot = session.query(Slot).one()
    assert slot.player_id is None
    assert "Okänd Spelare" in slot.note


def test_import_rejects_missing_barn(tmp_path):
    session = make_session()
    team = seeded_team(session)
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "", ""],
    ])

    with pytest.raises(SeedError, match="barn"):
        import_schedule(session, path, team_id=team.id)


def test_import_rejects_malformed_date(tmp_path):
    session = make_session()
    team = seeded_team(session)
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026/01/16",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ])

    with pytest.raises(SeedError, match="datum"):
        import_schedule(session, path, team_id=team.id)


def test_import_rejects_malformed_time(tmp_path):
    session = make_session()
    team = seeded_team(session)
    path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18.00-21.00", "Tova Exempel", ""],
    ])

    with pytest.raises(SeedError, match="tid"):
        import_schedule(session, path, team_id=team.id)


def test_import_rolls_back_on_error(tmp_path):
    session = make_session()
    team = seeded_team(session)
    old_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Cafe", 3, "2026-01-16",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ])
    import_schedule(session, old_path, team_id=team.id)

    bad_path = write_xlsx(tmp_path, [
        [2026, "Arena värdskap", "Wallenstam arena", "Kiosk", 3, "invalid-date",
         "Fredag", "18:00-21:00", "Tova Exempel", ""],
    ], name="trasig.xlsx")

    with pytest.raises(SeedError, match="datum"):
        import_schedule(session, bad_path, team_id=team.id)

    # The wipe and the failed import are rolled back together.
    assert session.query(Slot).count() == 1
    assert session.query(Slot).one().station == "Cafe"


def test_import_keeps_a_slot_whose_date_is_not_set_yet(tmp_path):
    session = make_session()
    team = seeded_team(session)

    path = write_xlsx(tmp_path, [
        [2027, "Arena värdskap vinter", "Wallenstam arena", "", "", "",
         "", "18:00-21:00", "Tova Exempel", ""],
    ])

    count = import_schedule(session, path, team_id=team.id)

    assert count == 1
    slot = session.query(Slot).one()
    assert slot.date is None
    assert slot.duty_name == "Arena värdskap vinter"
    assert slot.player.name == "Tova Exempel"
