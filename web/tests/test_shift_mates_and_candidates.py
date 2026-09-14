"""Who you share a shift with, and who is therefore not a swap partner."""

from datetime import date, time

import pytest

from duty_web.models import Person, Slot
from duty_web.schedule_queries import shift_mates_for, swap_candidates_for_slot


@pytest.fixture
def shift(session_factory, seeded):
    """Two parents on one shift, plus one parent on a different shift."""
    session = session_factory()
    team_id = seeded["team"].id
    mate = Person(name="Anna Andersson", email="anna@exempel.se")
    other = Person(name="Bo Bengtsson", email="bo@exempel.se")
    session.add_all([mate, other])
    session.flush()

    shared = dict(
        team_id=team_id, date=date(2026, 10, 3), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Bästkustcupen",
        venue="Wallenstam arena",
    )
    mine = Slot(**shared, person_id=seeded["person"].id, child_name="Moa Exempel")
    mates_slot = Slot(**shared, person_id=mate.id, child_name="Alva Andersson")
    elsewhere = Slot(
        team_id=team_id, date=date(2026, 10, 10), start_time=time(15, 0),
        end_time=time(18, 0), station="", duty_name="Bästkustcupen",
        venue="Wallenstam arena", person_id=other.id, child_name="Bea Bengtsson",
    )
    session.add_all([mine, mates_slot, elsewhere])
    session.commit()
    return {"session": session, "mine": mine, "mate": mates_slot, "elsewhere": elsewhere}


def test_shift_mates_lists_the_others_on_the_same_shift(shift):
    mates = shift_mates_for(shift["session"], shift["mine"])

    assert [m.id for m in mates] == [shift["mate"].id]


def test_shift_mates_excludes_the_same_duty_at_another_time(shift):
    mates = shift_mates_for(shift["session"], shift["mine"])

    assert shift["elsewhere"].id not in [m.id for m in mates]


def test_swap_candidates_exclude_people_already_on_the_same_shift(shift):
    candidates = [shift["mate"], shift["elsewhere"]]

    result = swap_candidates_for_slot(shift["mine"], candidates)

    assert [c.id for c in result] == [shift["elsewhere"].id]


def test_swap_candidates_exclude_every_slot_of_a_shift_mate(shift):
    """A mate's other slots are no use either — accepting would put them on
    this shift twice over."""
    session = shift["session"]
    mate_person_id = shift["mate"].person_id
    mates_other_slot = Slot(
        team_id=shift["mine"].team_id, date=date(2026, 11, 7), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Åby Julmarknad",
        venue="", person_id=mate_person_id,
    )
    session.add(mates_other_slot)
    session.commit()

    result = swap_candidates_for_slot(
        shift["mine"], [shift["mate"], mates_other_slot, shift["elsewhere"]]
    )

    assert [c.id for c in result] == [shift["elsewhere"].id]


def test_mine_page_shows_who_you_share_the_shift_with(client, shift, seeded):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(seeded["person"].id)
        sess["_fresh"] = True

    body = client.get("/").get_data(as_text=True)

    assert "Tillsammans med" in body
    assert "Anna Andersson" in body
    # Both name forms ship in the HTML; CSS decides which one is visible.
    assert "Alva Andersson" in body
