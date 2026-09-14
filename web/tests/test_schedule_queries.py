from datetime import date, datetime, time

from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Player, Slot, SwapRequest, Team
from duty_web.schedule_queries import (
    slots_for_parent,
    slots_for_team_month,
    swaps_pending_for_parent,
)


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def _family(session, team, player_name, *parent_emails):
    player = Player(name=player_name, team_id=team.id)
    player.parents = [
        Person(name=email.split("@")[0], email=email) for email in parent_emails
    ]
    session.add(player)
    session.flush()
    return player


def _slot(team_id, player_id, day, station="Cafe"):
    return Slot(
        team_id=team_id, date=date(2026, 1, day), start_time=time(18, 0),
        end_time=time(21, 0), station=station, duty_name="Arena värdskap",
        venue="Wallenstam arena", player_id=player_id,
    )


def test_slots_for_parent_are_chronological_and_scoped_to_their_player():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    a = _family(session, team, "Klara", "a@exempel.se")
    b = _family(session, team, "Moa", "b@exempel.se")
    session.add_all([
        _slot(team.id, a.id, 30),
        _slot(team.id, a.id, 16),
        _slot(team.id, b.id, 9),
    ])
    session.commit()

    result = slots_for_parent(session, a.parents[0].id)

    assert [s.date.day for s in result] == [16, 30]


def test_both_parents_of_a_player_see_the_same_slots():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    player = _family(session, team, "Klara", "hans@exempel.se", "lena@exempel.se")
    session.add_all([_slot(team.id, player.id, 16), _slot(team.id, player.id, 30)])
    session.commit()
    hans, lena = player.parents

    assert [s.id for s in slots_for_parent(session, hans.id)] == [
        s.id for s in slots_for_parent(session, lena.id)
    ]


def test_a_parent_with_no_player_sees_nothing():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    stranger = Person(name="Utomstående", email="x@exempel.se")
    session.add_all([team, stranger])
    session.flush()
    player = _family(session, team, "Klara", "hans@exempel.se")
    session.add(_slot(team.id, player.id, 16))
    session.commit()

    assert slots_for_parent(session, stranger.id) == []


def test_slots_for_team_month_includes_unfilled_slots():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    session.add_all([
        _slot(team.id, None, 16),
        _slot(team.id, None, 23, station="Entré"),
    ])
    session.commit()

    result = slots_for_team_month(session, team.id, 2026, 1)

    assert len(result) == 2
    assert all(s.player is None for s in result)


def test_slots_for_team_month_excludes_other_months():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    session.add(_slot(team.id, None, 16))
    session.add(Slot(
        team_id=team.id, date=date(2026, 2, 6), start_time=time(18, 0),
        end_time=time(21, 0), station="Kiosk", duty_name="Arena värdskap",
        venue="Wallenstam arena",
    ))
    session.commit()

    result = slots_for_team_month(session, team.id, 2026, 1)

    assert len(result) == 1


def test_swaps_pending_for_parent_returns_only_pending_requests_targeting_them():
    session = make_session()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    session.add(team)
    session.flush()
    a = _family(session, team, "Klara", "a@exempel.se")
    b = _family(session, team, "Moa", "b@exempel.se", "b2@exempel.se")
    slot_a = _slot(team.id, a.id, 16)
    slot_b = _slot(team.id, b.id, 23, station="Entré")
    session.add_all([slot_a, slot_b])
    session.flush()
    pending = SwapRequest(
        proposer_slot_id=slot_a.id, target_slot_id=slot_b.id,
        status="pending", created_at=datetime(2026, 1, 1),
    )
    declined = SwapRequest(
        proposer_slot_id=slot_b.id, target_slot_id=slot_a.id,
        status="declined", created_at=datetime(2026, 1, 1),
    )
    session.add_all([pending, declined])
    session.commit()

    # Either of Moa's parents is shown the same waiting request.
    for parent in b.parents:
        assert [r.id for r in swaps_pending_for_parent(session, parent.id)] == [
            pending.id
        ]
