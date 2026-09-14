from datetime import datetime, timedelta

import pytest

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, SwapRequest, Team
from duty_web.swaps import (
    SwapError,
    accept_swap,
    decline_swap,
    expire_stale_swaps,
    propose_swap,
)


def make_fixtures():
    engine = make_engine(":memory:")
    init_db(engine)
    session = make_session_factory(engine)()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    a = Person(name="A", email="a@exempel.se")
    b = Person(name="B", email="b@exempel.se")
    session.add_all([team, a, b])
    session.flush()
    from datetime import date, time

    slot_a = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=a.id,
    )
    slot_b = Slot(
        team_id=team.id, date=date(2026, 1, 23), start_time=time(17, 30),
        end_time=time(20, 30), station="Entré", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=b.id,
    )
    session.add_all([slot_a, slot_b])
    session.commit()
    return session, a, b, slot_a, slot_b


def test_propose_swap_creates_a_pending_request():
    session, a, b, slot_a, slot_b = make_fixtures()

    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    assert request.status == "pending"


def test_propose_swap_rejects_a_slot_you_do_not_own():
    session, a, b, slot_a, slot_b = make_fixtures()

    with pytest.raises(SwapError, match="ditt eget"):
        propose_swap(
            session, proposer_slot_id=slot_b.id, target_slot_id=slot_a.id,
            proposer_person_id=a.id, now=datetime(2026, 1, 1),
        )


def test_propose_swap_rejects_a_nonexistent_target_slot():
    session, a, b, slot_a, slot_b = make_fixtures()

    with pytest.raises(SwapError, match="hittades inte"):
        propose_swap(
            session, proposer_slot_id=slot_a.id, target_slot_id=999_999,
            proposer_person_id=a.id, now=datetime(2026, 1, 1),
        )


def test_propose_swap_rejects_an_unfilled_target_slot():
    session, a, b, slot_a, slot_b = make_fixtures()
    slot_b.person_id = None
    session.commit()

    with pytest.raises(SwapError, match="ledigt"):
        propose_swap(
            session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
            proposer_person_id=a.id, now=datetime(2026, 1, 1),
        )


def test_propose_swap_rejects_a_self_swap():
    session, a, b, slot_a, slot_b = make_fixtures()

    with pytest.raises(SwapError, match="eget pass"):
        propose_swap(
            session, proposer_slot_id=slot_a.id, target_slot_id=slot_a.id,
            proposer_person_id=a.id, now=datetime(2026, 1, 1),
        )


def test_propose_swap_rejects_a_target_slot_the_proposer_already_owns():
    session, a, b, slot_a, slot_b = make_fixtures()
    from datetime import date, time

    other_slot_a = Slot(
        team_id=slot_a.team_id, date=date(2026, 2, 1), start_time=time(18, 0),
        end_time=time(21, 0), station="Kiosk", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=a.id,
    )
    session.add(other_slot_a)
    session.commit()

    with pytest.raises(SwapError, match="redan har"):
        propose_swap(
            session, proposer_slot_id=slot_a.id, target_slot_id=other_slot_a.id,
            proposer_person_id=a.id, now=datetime(2026, 1, 1),
        )


def test_accept_swap_exchanges_the_two_slots_person_id():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    accept_swap(session, request.id, accepting_person_id=b.id, now=datetime(2026, 1, 2))

    session.refresh(slot_a)
    session.refresh(slot_b)
    assert slot_a.person_id == b.id
    assert slot_b.person_id == a.id
    assert session.get(SwapRequest, request.id).status == "accepted"


def test_accept_swap_rejects_the_wrong_person():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    with pytest.raises(SwapError, match="inte ditt"):
        accept_swap(session, request.id, accepting_person_id=a.id, now=datetime(2026, 1, 2))


def test_decline_swap_marks_it_declined_without_moving_anyone():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    decline_swap(session, request.id, declining_person_id=b.id, now=datetime(2026, 1, 2))

    session.refresh(slot_a)
    assert slot_a.person_id == a.id
    assert session.get(SwapRequest, request.id).status == "declined"


def test_expire_stale_swaps_expires_only_old_pending_requests():
    session, a, b, slot_a, slot_b = make_fixtures()
    old_request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    count = expire_stale_swaps(session, now=datetime(2026, 1, 9))

    assert count == 1
    assert session.get(SwapRequest, old_request.id).status == "expired"


def test_accepting_a_second_offer_on_an_already_swapped_slot_is_rejected():
    """Reproduces the C2 TOCTOU scenario from the final review.

    A proposes slot A1 to both B (via slot B1) and C (via slot C1). B accepts
    first, legitimately receiving A1. C then accepts their still-pending
    request; accept_swap must detect that the proposer slot (A1) is no
    longer owned by the original proposer (A) and refuse, rather than
    silently taking A1 away from B (who never agreed to anything with C)
    and handing it to C.
    """
    session, a, b, slot_a, slot_b = make_fixtures()
    c = Person(name="C", email="c@exempel.se")
    session.add(c)
    session.flush()
    from datetime import date, time

    slot_c = Slot(
        team_id=slot_a.team_id, date=date(2026, 1, 30), start_time=time(17, 0),
        end_time=time(20, 0), station="Kiosk", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=c.id,
    )
    session.add(slot_c)
    session.commit()

    request_to_b = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )
    request_to_c = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_c.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    # B accepts first: A1 -> B, B1 -> A.
    accept_swap(session, request_to_b.id, accepting_person_id=b.id, now=datetime(2026, 1, 2))
    session.refresh(slot_a)
    session.refresh(slot_b)
    assert slot_a.person_id == b.id
    assert slot_b.person_id == a.id

    # C accepting the stale request must not silently take A1 from B.
    with pytest.raises(SwapError):
        accept_swap(
            session, request_to_c.id, accepting_person_id=c.id, now=datetime(2026, 1, 3)
        )

    session.refresh(slot_a)
    session.refresh(slot_c)
    assert slot_a.person_id == b.id, "B's legitimately-received slot must not move"
    assert slot_c.person_id == c.id
    assert session.get(SwapRequest, request_to_c.id).status == "declined"


def test_accepting_one_offer_supersedes_other_pending_offers_on_the_same_slot():
    """Once one of A's offers on slot A1 is accepted, A's other pending
    offer on A1 should be auto-declined rather than left accept-able."""
    session, a, b, slot_a, slot_b = make_fixtures()
    c = Person(name="C", email="c@exempel.se")
    session.add(c)
    session.flush()
    from datetime import date, time

    slot_c = Slot(
        team_id=slot_a.team_id, date=date(2026, 1, 30), start_time=time(17, 0),
        end_time=time(20, 0), station="Kiosk", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=c.id,
    )
    session.add(slot_c)
    session.commit()

    request_to_b = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )
    request_to_c = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_c.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )

    accept_swap(session, request_to_b.id, accepting_person_id=b.id, now=datetime(2026, 1, 2))

    assert session.get(SwapRequest, request_to_c.id).status == "declined"


def test_accepting_an_expired_swap_is_rejected():
    session, a, b, slot_a, slot_b = make_fixtures()
    request = propose_swap(
        session, proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        proposer_person_id=a.id, now=datetime(2026, 1, 1),
    )
    expire_stale_swaps(session, now=datetime(2026, 1, 9))

    with pytest.raises(SwapError, match="upphört"):
        accept_swap(session, request.id, accepting_person_id=b.id, now=datetime(2026, 1, 10))
