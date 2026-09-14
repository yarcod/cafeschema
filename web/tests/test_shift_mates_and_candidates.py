"""Who you share a shift with, and who is therefore not a swap partner."""

from datetime import date, time

import pytest

from duty_web.models import Slot
from duty_web.schedule_queries import shift_mates_for, swap_candidates_for_slot

from .conftest import make_family


@pytest.fixture
def shift(session_factory, seeded):
    """Two families on one shift, plus a third family on a different shift."""
    session = session_factory()
    team = seeded["team"]
    mate_player = make_family(
        session, team, "Alva Andersson",
        [("Anna Andersson", "anna@exempel.se"), ("Ali Andersson", "ali@exempel.se")],
    )
    other_player = make_family(
        session, team, "Bea Bengtsson", [("Bo Bengtsson", "bo@exempel.se")]
    )

    shared = dict(
        team_id=team.id, date=date(2026, 10, 3), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Bästkustcupen",
        venue="Wallenstam arena",
    )
    mine = Slot(**shared, player_id=seeded["player"].id, child_name="Tova Exempel")
    mates_slot = Slot(
        **shared, player_id=mate_player.id, child_name="Alva Andersson"
    )
    elsewhere = Slot(
        team_id=team.id, date=date(2026, 10, 10), start_time=time(15, 0),
        end_time=time(18, 0), station="", duty_name="Bästkustcupen",
        venue="Wallenstam arena", player_id=other_player.id,
        child_name="Bea Bengtsson",
    )
    session.add_all([mine, mates_slot, elsewhere])
    session.commit()
    return {
        "session": session,
        "mine": mine,
        "mate": mates_slot,
        "mate_player": mate_player,
        "elsewhere": elsewhere,
    }


def test_shift_mates_lists_the_others_on_the_same_shift(shift):
    mates = shift_mates_for(shift["session"], shift["mine"])

    assert [m.id for m in mates] == [shift["mate"].id]


def test_shift_mates_excludes_the_same_duty_at_another_time(shift):
    mates = shift_mates_for(shift["session"], shift["mine"])

    assert shift["elsewhere"].id not in [m.id for m in mates]


def test_swap_candidates_exclude_families_already_on_the_same_shift(shift):
    candidates = [shift["mate"], shift["elsewhere"]]

    result = swap_candidates_for_slot(shift["mine"], candidates)

    assert [c.id for c in result] == [shift["elsewhere"].id]


def test_swap_candidates_exclude_every_slot_of_a_shift_mates_family(shift):
    """A mate's other slots are no use either — accepting would put their
    family on this shift twice over, whichever parent turns up."""
    session = shift["session"]
    mates_other_slot = Slot(
        team_id=shift["mine"].team_id, date=date(2026, 11, 7), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Åby Julmarknad",
        venue="", player_id=shift["mate_player"].id,
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
    # Both of the mate's parents are shown — either may turn up for the shift.
    assert "Anna Andersson &amp; Ali Andersson" in body
    # Both name forms ship in the HTML; CSS decides which one is visible.
    assert "Alva Andersson" in body


def test_the_other_parent_sees_the_same_shift_page(client, shift, seeded):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(seeded["person"].id)
        sess["_fresh"] = True
    first = client.get("/").get_data(as_text=True)

    with client.session_transaction() as sess:
        sess["_user_id"] = str(seeded["other_parent"].id)
        sess["_fresh"] = True
    second = client.get("/").get_data(as_text=True)

    assert first == second
