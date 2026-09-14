"""The Byten page: what a pending swap looks like to both sides."""

from datetime import date, time

import pytest

from duty_web.clock import utcnow
from duty_web.models import Slot
from duty_web.swaps import propose_swap

from .conftest import make_family


def _login(client, person_id):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person_id)
        sess["_fresh"] = True


@pytest.fixture
def pending(session_factory, seeded):
    """Tova proposes her slot in exchange for Bo's."""
    session = session_factory()
    bea = make_family(
        session, seeded["team"], "Bea Bengtsson",
        [("Bo Bengtsson", "bo@exempel.se")],
    )
    bo = bea.parents[0]
    theirs = Slot(
        team_id=seeded["team"].id, date=date(2026, 12, 5), start_time=time(9, 0),
        end_time=time(12, 0), station="", duty_name="Bästkustcupen", venue="",
        player_id=bea.id, child_name="Bea Bengtsson",
    )
    session.add(theirs)
    session.commit()
    request_row = propose_swap(
        session,
        proposer_slot_id=seeded["future_slot"].id,
        target_slot_id=theirs.id,
        proposer_person_id=seeded["person"].id,
        now=utcnow(),
    )
    session.commit()
    return {"bo": bo, "theirs": theirs, "request": request_row}


def test_receiver_sees_both_sides_of_the_trade(client, pending, seeded):
    _login(client, pending["bo"].id)

    body = client.get("/byten").get_data(as_text=True)

    assert "Du ger" in body
    assert "Du får" in body
    assert "Bästkustcupen" in body
    assert "Arena värdskap" in body


def test_neither_side_of_the_trade_renders_a_python_object(client, pending, seeded):
    """Regression: the template read `item.get`, which Jinja resolves to
    dict.get — the page showed '<built-in method get of dict object ...>'
    where the slot you would receive should be."""
    for person_id in (pending["bo"].id, seeded["person"].id):
        _login(client, person_id)

        body = client.get("/byten").get_data(as_text=True)

        assert "built-in method" not in body
        assert "object at 0x" not in body


def test_receiver_gets_accept_and_decline_buttons(client, pending):
    _login(client, pending["bo"].id)

    body = client.get("/byten").get_data(as_text=True)

    assert "Acceptera" in body
    assert "Avböj" in body


def test_proposer_sees_it_as_waiting_not_actionable(client, pending, seeded):
    _login(client, seeded["person"].id)

    body = client.get("/byten").get_data(as_text=True)

    assert "Väntar på svar" in body
    assert "Acceptera" not in body


def test_a_waiting_request_is_flagged_on_every_page(client, pending):
    """Otherwise it is only discoverable by opening the Byten tab on spec."""
    _login(client, pending["bo"].id)

    body = client.get("/").get_data(as_text=True)

    assert 'class="tab-badge"' in body


def test_no_badge_when_nothing_is_waiting(client, pending, seeded):
    _login(client, seeded["person"].id)

    body = client.get("/").get_data(as_text=True)

    assert 'class="tab-badge"' not in body


def test_the_other_parent_sees_the_same_waiting_request(client, pending, seeded):
    """The proposal was made by one parent; both are answerable for it."""
    _login(client, seeded["person"].id)
    first = client.get("/byten").get_data(as_text=True)

    _login(client, seeded["other_parent"].id)
    second = client.get("/byten").get_data(as_text=True)

    assert "Väntar på svar" in second
    assert first == second
