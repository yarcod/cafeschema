"""schedule.import: the coach's Excel becomes a diff that keeps ids and swaps."""

import re
from datetime import date, datetime, time
from html import unescape
from io import BytesIO

import pytest

from duty_web.admin import slot_ops
from duty_web.admin.actions import REGISTRY, schedule_import
from duty_web.admin.actions.base import ValidationError
from duty_web.admin.actions.schedule_import import ScheduleImportInput
from duty_web.admin.executor import apply_plan, dry_run
from duty_web.admin.permissions import admin_context
from duty_web.clock import utcnow
from duty_web.models import ActivityStation, NameAlias, Person, Player, Slot, SwapRequest, Team
from duty_web.places import find_or_create_station
from duty_web.schedule_xlsx import read_schedule

from .conftest import bearer, login, make_family
from .test_schedule_xlsx import coach_workbook, seed_workbook

SECRET = "test-secret"
TODAY = date(2026, 10, 28)
SATURDAY = datetime(2027, 1, 16)
SEASON = [
    [46, "Cafépass", SATURDAY, "11:30-16:00", "Tova Exempel", "Moa Granne", None],
    [None, "Cafépass", "Datum ej satt (VT27)", "Fre 18-21", "Tova Exempel", None, None],
]


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    # The import never touches the past, so its tests live on a fixed day.
    monkeypatch.setattr(schedule_import, "_today", lambda: TODAY)


@pytest.fixture
def arena(session_factory):
    session = session_factory()
    station = find_or_create_station(session, "Wallenstam arena", "Café Arena B, nedre plan")
    session.commit()
    return station.id


@pytest.fixture
def mails(monkeypatch):
    sent = []
    monkeypatch.setattr(slot_ops, "send_swap_cancelled", lambda emails, **kw: sent.append(kw))
    return sent


def _rows(*rows):
    return read_schedule(coach_workbook(list(rows)))


def _dry(app, session_factory, person, inp, *, apply=False):
    session = session_factory()
    ctx = admin_context(session, session.get(Person, person.id), "f14-bla")
    with app.test_request_context():
        plan, token = dry_run(session, ctx, REGISTRY["schedule.import"], inp, secret_key=SECRET)
        if apply:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")
    return plan


def _run(app, session_factory, person, rows, *, apply=True, **answers):
    return _dry(app, session_factory, person, ScheduleImportInput(rows=rows, **answers), apply=apply)


def _ida(session_factory, seeded):
    session = session_factory()
    player = make_family(session, session.get(Team, seeded["team"].id), "Ida Ny",
                         [("Iris Ny", "iris@exempel.se")])
    session.commit()
    return player.id


def _seat(session_factory, player_name, day):
    session = session_factory()
    return (
        session.query(Slot).join(Player).filter(Player.name == player_name, Slot.duty_name == "Cafépass",
                                                 Slot.date == day).one().id
    )


def _cafe_slot(session_factory, team_id, day, **fields):
    session = session_factory()
    slot = Slot(team_id=team_id, date=day, start_time=time(11, 30), end_time=time(16),
                station="Café Arena B, nedre plan", venue="Wallenstam arena", duty_name="Cafépass",
                **fields)
    session.add(slot)
    session.commit()
    return slot.id


def _swap(session_factory, proposer, target, status="pending"):
    session = session_factory()
    swap = SwapRequest(proposer_slot_id=proposer, target_slot_id=target, created_at=utcnow(), status=status)
    session.add(swap)
    session.commit()
    return swap.id


def test_a_first_import_adds_the_seasons_seats(app, session_factory, admins, arena):
    plan = _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))

    assert plan.public()["counts"] == {"add": 3, "update": 0, "delete": 0}
    assert "Pass för Arena värdskap finns inte i filen och lämnas orörda." in plan.notes
    assert any(note.startswith("Importen gäller Cafépass, säsongen 2026/27") for note in plan.notes)
    seats = session_factory().query(Slot).filter_by(duty_name="Cafépass").all()
    assert {(s.station_id, s.venue, s.station, s.origin) for s in seats} == {
        (arena, "Wallenstam arena", "Café Arena B, nedre plan", "import")
    }
    assert {s.note for s in seats if s.date is None} == {"Fredagar"}


def test_the_same_file_twice_changes_nothing_and_keeps_every_swap(
    app, session_factory, admins, second_team, arena, mails
):
    _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))
    before = sorted(s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass"))
    tova = _seat(session_factory, "Tova Exempel", SATURDAY.date())
    pending = _swap(session_factory, tova, second_team["neighbour_slot"].id)
    accepted = _swap(session_factory, before[-1], second_team["neighbour_slot"].id, status="accepted")

    again = _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))

    assert again.changes == [] and again.warnings == [] and "3 pass oförändrade." in again.notes
    session = session_factory()
    assert sorted(s.id for s in session.query(Slot).filter_by(duty_name="Cafépass")) == before
    assert (session.get(SwapRequest, pending).status, session.get(SwapRequest, accepted).status) == (
        "pending", "accepted"
    )
    assert mails == []


def test_an_accepted_swap_survives_the_same_file(app, session_factory, admins, seeded, arena):
    ida = _ida(session_factory, seeded)
    _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))
    seat = _seat(session_factory, "Tova Exempel", SATURDAY.date())
    session = session_factory()
    # What an accepted swap does: the owner changes, the schedule's name stays.
    session.get(Slot, seat).player_id = ida
    session.commit()

    again = _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))

    assert again.changes == []
    slot = session_factory().get(Slot, seat)
    assert (slot.player_id, slot.child_name) == (ida, "Tova Exempel")


def test_a_new_name_on_a_seat_keeps_its_id_and_cancels_its_swap(
    app, session_factory, admins, seeded, second_team, arena, mails
):
    ida = _ida(session_factory, seeded)
    _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))
    seat = _seat(session_factory, "Tova Exempel", SATURDAY.date())
    _swap(session_factory, seat, second_team["neighbour_slot"].id)

    plan = _run(app, session_factory, admins["ansvarig"], _rows(
        [46, "Cafépass", SATURDAY, "11:30-16:00", "Ida Ny", "Moa Granne", None], SEASON[1],
    ))

    assert [(c.op, c.detail) for c in plan.changes] == [("update", "Tova Exempel → Ida Ny")]
    assert any("ställs in" in w for w in plan.warnings)
    session = session_factory()
    assert (session.get(Slot, seat).player_id, session.get(Slot, seat).child_name) == (ida, "Ida Ny")
    assert session.query(SwapRequest).one().status == "cancelled"
    assert len(mails) == 2


def test_a_respelled_name_only_changes_the_record_and_keeps_a_swap(
    app, session_factory, admins, seeded, second_team, arena, mails
):
    ida = _ida(session_factory, seeded)
    # As `just schedule-push` left it: the fixed name, and a swap since.
    seat = _cafe_slot(session_factory, seeded["team"].id, SATURDAY.date(), station_id=arena,
                      child_name="Tova Exempel", player_id=ida)
    swap = _swap(session_factory, seat, second_team["neighbour_slot"].id)

    plan = _run(app, session_factory, admins["ansvarig"],
                _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Tova E.", None, None]),
                names={"Tova E.": seeded["player"].id})

    assert [(c.op, c.detail) for c in plan.changes] == [
        ("update", "namnet i schemat: 'Tova Exempel' → 'Tova E.'")
    ]
    session = session_factory()
    assert (session.get(Slot, seat).player_id, session.get(Slot, seat).child_name) == (ida, "Tova E.")
    assert session.get(SwapRequest, swap).status == "pending" and mails == []


def test_two_respelled_names_in_reversed_order_keep_their_seats_owners_and_swaps(
    app, session_factory, admins, seeded, second_team, arena, mails
):
    ida = _ida(session_factory, seeded)
    moa = second_team["neighbour_slot"].player_id
    team_id, day = seeded["team"].id, SATURDAY.date()
    # As `just schedule-push` left it, after a swap gave Tova's seat to Ida.
    tova_seat = _cafe_slot(session_factory, team_id, day, station_id=arena,
                           child_name="Tova Exempel", player_id=ida)
    moa_seat = _cafe_slot(session_factory, team_id, day, station_id=arena,
                          child_name="Moa Granne", player_id=moa)
    swaps = [_swap(session_factory, seat, second_team["neighbour_slot"].id) for seat in (tova_seat, moa_seat)]

    plan = _run(app, session_factory, admins["ansvarig"],
                _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Moa G.", "Tova E.", None]),
                names={"Moa G.": moa, "Tova E.": seeded["player"].id})

    assert sorted(c.detail for c in plan.changes) == [
        "namnet i schemat: 'Moa Granne' → 'Moa G.'",
        "namnet i schemat: 'Tova Exempel' → 'Tova E.'",
    ]
    session = session_factory()
    assert [(session.get(Slot, s).player_id, session.get(Slot, s).child_name) for s in (tova_seat, moa_seat)] == [
        (ida, "Tova E."), (moa, "Moa G."),
    ]
    assert {session.get(SwapRequest, s).status for s in swaps} == {"pending"} and mails == []


def test_removing_one_of_two_undated_weekdays_removes_that_one(
    app, session_factory, admins, second_team, arena
):
    friday = [None, "Cafépass", "Datum ej satt (VT27)", "Fre 18-21", "Tova Exempel", None, None]
    saturday = [None, "Cafépass", "Datum ej satt (VT27)", "Lör 18-21", "Tova Exempel", None, None]
    _run(app, session_factory, admins["ansvarig"], _rows(friday, saturday))
    seats = {s.note: s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")}
    swap = _swap(session_factory, seats["Lördagar"], second_team["neighbour_slot"].id)

    plan = _run(app, session_factory, admins["ansvarig"], _rows(saturday))

    assert [(c.op, c.detail) for c in plan.changes] == [("delete", "Tova Exempel")]
    session = session_factory()
    assert session.get(Slot, seats["Fredagar"]) is None
    assert session.get(Slot, seats["Lördagar"]) is not None
    assert session.get(SwapRequest, swap).status == "pending"


def test_a_fredagar_shift_only_gets_a_friday(app, session_factory, admins, arena):
    _run(app, session_factory, admins["ansvarig"], _rows(SEASON[1]))
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]

    plan = _run(app, session_factory, admins["ansvarig"], _rows(
        [None, "Cafépass", datetime(2027, 2, 6), "18:00-21:00", "Tova Exempel", None, None],   # a Saturday
    ))

    assert sorted(c.op for c in plan.changes) == ["add", "delete"]
    assert session_factory().get(Slot, seat) is None


def test_dating_and_naming_an_unknown_seat_happen_in_one_import(app, session_factory, admins, seeded, arena):
    ida = _ida(session_factory, seeded)
    _run(app, session_factory, admins["ansvarig"],
         _rows([None, "Cafépass", "Datum ej satt (VT27)", "Fre 18-21", "Elin Exempel", None, None]))
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]
    dated = _rows([None, "Cafépass", datetime(2027, 2, 5), "18:00-21:00", "Elin Exempel", None, None])

    first = _run(app, session_factory, admins["ansvarig"], dated, names={"Elin Exempel": ida})
    second = _run(app, session_factory, admins["ansvarig"], dated)

    assert [c.detail for c in first.changes] == ["datum satt: fre 5 feb; Ledigt ('Elin Exempel') → Ida Ny"]
    assert second.changes == []
    slot = session_factory().get(Slot, seat)
    assert (slot.date, slot.player_id) == (date(2027, 2, 5), ida)


def test_correcting_a_saved_answer_while_dating_moves_the_seat_too(
    app, session_factory, admins, seeded, second_team, arena
):
    ida = _ida(session_factory, seeded)
    moa = second_team["neighbour_slot"].player_id
    undated = _rows([None, "Cafépass", "Datum ej satt (VT27)", "Fre 18-21", "Elin Exempel", None, None])
    _run(app, session_factory, admins["ansvarig"], undated, names={"Elin Exempel": ida})
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]

    _run(app, session_factory, admins["ansvarig"],
         _rows([None, "Cafépass", datetime(2027, 2, 5), "18:00-21:00", "Elin Exempel", None, None]),
         names={"Elin Exempel": moa})

    assert session_factory().get(Slot, seat).player_id == moa
    assert session_factory().query(NameAlias).one().player_id == moa


def test_a_swap_accepted_through_the_app_survives_the_same_file(
    app, session_factory, admins, seeded, second_team, arena
):
    from duty_web.swaps import accept_swap, propose_swap

    _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))
    tova = _seat(session_factory, "Tova Exempel", SATURDAY.date())
    session = session_factory()
    swap = propose_swap(session, proposer_slot_id=tova, target_slot_id=second_team["neighbour_slot"].id,
                        proposer_person_id=seeded["person"].id, now=utcnow())
    maja = session.query(Person).filter_by(email="maja@exempel.se").one()
    accept_swap(session, swap.id, accepting_person_id=maja.id, now=utcnow())

    again = _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))

    assert again.changes == []
    slot = session_factory().get(Slot, tova)
    assert (slot.player_id, slot.child_name) == (second_team["neighbour_slot"].player_id, "Tova Exempel")


def test_a_saved_answer_changed_after_review_is_a_conflict(app, session_factory, admins, seeded, second_team, arena):
    from duty_web.admin.plan_tokens import PlanTokenError

    ida = _ida(session_factory, seeded)
    moa = second_team["neighbour_slot"].player_id
    rows = _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None])
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    with app.test_request_context():
        _, token = dry_run(session, ctx, REGISTRY["schedule.import"],
                           ScheduleImportInput(rows=rows, names={"Elin Exempel": ida}), secret_key=SECRET)
        other = session_factory()
        other.add(NameAlias(team_id=seeded["team"].id, spelling_key="elin exempel",
                            spelling="Elin Exempel", player_id=moa))
        other.commit()

        with pytest.raises(PlanTokenError) as caught:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert caught.value.status == 409
    assert session_factory().query(NameAlias).one().player_id == moa


def test_an_undated_shift_that_gets_its_date_keeps_its_id_and_its_swaps(
    app, session_factory, admins, second_team, arena, mails
):
    _run(app, session_factory, admins["ansvarig"], _rows(SEASON[1]))
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]
    swap = _swap(session_factory, seat, second_team["neighbour_slot"].id)

    plan = _run(app, session_factory, admins["ansvarig"], _rows(
        [None, "Cafépass", datetime(2027, 2, 5), "18:00-21:00", "Tova Exempel", None, None],
    ))

    assert [(c.op, c.detail) for c in plan.changes] == [("update", "datum satt: fre 5 feb")]
    assert plan.warnings == []
    session = session_factory()
    assert session.get(Slot, seat).date == date(2027, 2, 5)
    assert session.get(SwapRequest, swap).status == "pending" and mails == []


def test_a_row_gone_from_the_file_is_removed_with_its_swap_history(
    app, session_factory, admins, second_team, arena
):
    _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))
    (undated,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass", date=None)]
    _swap(session_factory, undated, second_team["neighbour_slot"].id, status="accepted")

    plan = _run(app, session_factory, admins["ansvarig"], _rows(SEASON[0]))

    assert [(c.op, c.detail) for c in plan.changes] == [("delete", "Tova Exempel")]
    session = session_factory()
    assert session.get(Slot, undated) is None and session.query(SwapRequest).count() == 0


def test_another_seasons_shifts_are_left_alone(app, session_factory, admins, seeded, arena):
    last = _cafe_slot(session_factory, seeded["team"].id, date(2026, 3, 14), child_name="Tova Exempel")
    next_ = _cafe_slot(session_factory, seeded["team"].id, date(2027, 9, 4), child_name="Tova Exempel")

    plan = _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))

    assert plan.public()["counts"]["delete"] == 0
    session = session_factory()
    assert session.get(Slot, last) is not None and session.get(Slot, next_) is not None


def test_a_past_shift_is_never_changed_and_a_past_row_never_added(
    app, session_factory, admins, seeded, arena
):
    gone = _cafe_slot(session_factory, seeded["team"].id, date(2026, 10, 3), child_name="Tova Exempel")
    rows = _rows(
        [40, "Cafépass", datetime(2026, 10, 3), "11:30-16:00", "Moa Granne", None, None],
        [41, "Cafépass", datetime(2026, 10, 10), "11:30-16:00", "Tova Exempel", None, None],
        *SEASON,
    )

    plan = _run(app, session_factory, admins["ansvarig"], rows)

    assert plan.public()["counts"] == {"add": 3, "update": 0, "delete": 0}
    assert "2 pass före i dag i filen läggs inte till: historiken ändras inte." in plan.notes
    assert "1 tidigare pass finns inte i filen men lämnas orörda." in plan.notes
    slot = session_factory().get(Slot, gone)
    assert slot is not None and slot.child_name == "Tova Exempel"


def test_a_shift_made_under_pass_survives_and_is_not_doubled(app, session_factory, admins, seeded, arena):
    team_id = seeded["team"].id
    cup = _cafe_slot(session_factory, team_id, date(2027, 1, 23), origin="admin")
    twin = _cafe_slot(session_factory, team_id, SATURDAY.date(), origin="admin", station_id=arena,
                      player_id=seeded["player"].id, child_name="Tova Exempel")

    plan = _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))

    assert plan.public()["counts"] == {"add": 2, "update": 0, "delete": 0}   # Moa's seat and the undated one
    assert "1 pass som lagts till under Pass lämnas orörda." in plan.notes
    session = session_factory()
    assert session.get(Slot, cup) is not None and session.get(Slot, twin).origin == "admin"


def test_an_unknown_name_becomes_a_question_and_its_answer_places_the_player(
    app, session_factory, admins, seeded, arena
):
    ida = _ida(session_factory, seeded)
    rows = _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None])

    asked = _run(app, session_factory, admins["ansvarig"], rows, apply=False)
    answered = _dry(app, session_factory, admins["ansvarig"], REGISTRY["schedule.import"].refine(
        ScheduleImportInput(rows=rows), {"name::Elin Exempel": str(ida)}
    ))

    assert asked.questions[0]["field"] == "name::Elin Exempel"
    assert asked.changes[0].detail == "Ledigt ('Elin Exempel')"
    assert any("'Elin Exempel' finns inte i laglistan" in w for w in asked.warnings)
    assert answered.changes[0].detail == "Ida Ny" and answered.questions == []
    assert "Svaret sparas till nästa import: 'Elin Exempel' är Ida Ny." in answered.notes


def test_an_answered_name_is_saved_and_the_same_file_then_asks_nothing(
    app, session_factory, admins, seeded, arena
):
    ida = _ida(session_factory, seeded)
    rows = _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None])
    _run(app, session_factory, admins["ansvarig"], rows, names={"Elin Exempel": ida})
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]

    again = _run(app, session_factory, admins["ansvarig"], rows)

    assert again.changes == [] and again.warnings == []
    (question,) = again.questions
    assert (question["field"], question["selected"]) == ("name::Elin Exempel", ida)
    assert "sparat svar" in question["label"]
    assert session_factory().query(NameAlias).one().player_id == ida
    assert session_factory().get(Slot, seat).player_id == ida


def test_correcting_a_saved_answer_moves_the_seats_it_gave(
    app, session_factory, admins, seeded, second_team, arena
):
    ida = _ida(session_factory, seeded)
    moa = second_team["neighbour_slot"].player_id
    rows = _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None])
    _run(app, session_factory, admins["ansvarig"], rows, names={"Elin Exempel": ida})
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]

    plan = _run(app, session_factory, admins["ansvarig"], rows, names={"Elin Exempel": moa})

    assert [(c.op, c.detail) for c in plan.changes] == [("update", "Ida Ny → Moa Granne")]
    assert session_factory().get(Slot, seat).player_id == moa
    assert session_factory().query(NameAlias).one().player_id == moa


def test_answering_later_fills_the_seat_it_left_free(app, session_factory, admins, seeded, arena):
    ida = _ida(session_factory, seeded)
    rows = _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None])
    _run(app, session_factory, admins["ansvarig"], rows)
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]

    plan = _run(app, session_factory, admins["ansvarig"], rows, names={"Elin Exempel": ida})

    assert [(c.op, c.detail) for c in plan.changes] == [("update", "Ledigt ('Elin Exempel') → Ida Ny")]
    assert session_factory().get(Slot, seat).player_id == ida


def test_an_unanswered_name_is_imported_as_ledigt_with_its_spelling(app, session_factory, admins, arena):
    _run(app, session_factory, admins["ansvarig"],
         _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None]))

    slot = session_factory().query(Slot).filter_by(duty_name="Cafépass").one()
    assert (slot.player_id, slot.child_name) == (None, "Elin Exempel")
    assert session_factory().query(NameAlias).count() == 0


def test_a_place_the_club_lacks_stops_the_import(app, session_factory, admins):
    rows = read_schedule(seed_workbook([
        [2027, "Kioskpass", "Hindås", "Kiosken", "", SATURDAY, "", "09:00-12:00", "Tova Exempel", ""],
    ]))

    with pytest.raises(ValidationError) as caught:
        _run(app, session_factory, admins["ansvarig"], rows)

    assert caught.value.errors["file"] == (
        "Okänd station 'Kiosken' på 'Hindås': lägg till den under Platser eller rätta filen."
    )


def test_an_activity_without_a_place_asks_where_it_is_held(app, session_factory, admins, arena):
    rows = _rows([46, "Städning", SATURDAY, "9-12", "Tova Exempel", None, None])

    asked = _run(app, session_factory, admins["ansvarig"], rows, apply=False)
    answered = _dry(app, session_factory, admins["ansvarig"], REGISTRY["schedule.import"].refine(
        ScheduleImportInput(rows=rows), {"station::Städning": str(arena)}
    ))

    assert asked.questions[0]["field"] == "station::Städning"
    assert any("'Städning' har ingen plats" in w for w in asked.warnings)
    assert answered.changes[0].summary.endswith("Städning · Café Arena B, nedre plan")


def test_an_answered_place_is_saved_and_the_same_file_then_changes_nothing(
    app, session_factory, admins, arena
):
    rows = _rows([46, "Städning", SATURDAY, "9-12", "Tova Exempel", None, None])
    _run(app, session_factory, admins["ansvarig"], rows, stations={"Städning": arena})
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Städning")]

    again = _run(app, session_factory, admins["ansvarig"], rows)

    assert again.changes == [] and again.warnings == []
    assert again.questions[0]["selected"] == arena
    assert session_factory().query(ActivityStation).one().station_id == arena
    assert session_factory().get(Slot, seat).station_id == arena


def test_correcting_a_saved_place_moves_the_seat_and_cancels_its_pending_swap(
    app, session_factory, admins, seeded, second_team, arena, mails
):
    from duty_web.swaps import accept_swap, propose_swap

    session = session_factory()
    kiosk = find_or_create_station(session, "Hindås", "Kiosken").id
    session.commit()
    rows = _rows([46, "Städning", SATURDAY, "9-12", "Tova Exempel", None, None])
    _run(app, session_factory, admins["ansvarig"], rows, stations={"Städning": arena})
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Städning")]
    session = session_factory()
    accepted = propose_swap(session, proposer_slot_id=seat, target_slot_id=second_team["neighbour_slot"].id,
                            proposer_person_id=seeded["person"].id, now=utcnow())
    maja = session.query(Person).filter_by(email="maja@exempel.se").one()
    accept_swap(session, accepted.id, accepting_person_id=maja.id, now=utcnow())
    accepted = accepted.id
    moa = second_team["neighbour_slot"].player_id
    pending = _swap(session_factory, seat, seeded["future_slot"].id)

    plan = _run(app, session_factory, admins["ansvarig"], rows, stations={"Städning": kiosk})

    assert [(c.op, c.detail) for c in plan.changes] == [
        ("update", "station Café Arena B, nedre plan → Kiosken")
    ]
    assert any("ställs in" in w for w in plan.warnings)
    session = session_factory()
    slot = session.get(Slot, seat)
    assert (slot.player_id, slot.child_name, slot.station_id, slot.venue, slot.station) == (
        moa, "Tova Exempel", kiosk, "Hindås", "Kiosken"
    )
    assert (session.get(SwapRequest, pending).status, session.get(SwapRequest, accepted).status) == (
        "cancelled", "accepted"
    )
    assert session.query(ActivityStation).one().station_id == kiosk
    assert len(mails) == 2


def test_an_answer_that_changes_no_seat_says_it_is_not_saved(
    app, session_factory, admins, seeded, second_team, arena
):
    ida = _ida(session_factory, seeded)
    moa = second_team["neighbour_slot"].player_id
    rows = _rows([46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None])
    _run(app, session_factory, admins["ansvarig"], rows, names={"Elin Exempel": ida})
    (seat,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass")]
    session = session_factory()
    # A swap gave the seat to Tova since: a corrected answer leaves it with her.
    session.get(Slot, seat).player_id = seeded["player"].id
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], rows, names={"Elin Exempel": moa}, apply=False)

    assert plan.changes == []
    assert "Svaret om 'Elin Exempel' sparas inte: inget pass ändras av det." in plan.notes
    assert not any(note.startswith("Svaret sparas") for note in plan.notes)


def test_a_place_answer_that_changes_no_seat_says_it_is_not_saved(app, session_factory, admins, arena):
    session = session_factory()
    kiosk = find_or_create_station(session, "Hindås", "Kiosken").id
    session.commit()
    # Only a past seat: it can't move, so the answer changes nothing.
    rows = _rows([40, "Städning", datetime(2026, 10, 3), "9-12", "Tova Exempel", None, None])

    plan = _run(app, session_factory, admins["ansvarig"], rows, stations={"Städning": kiosk}, apply=False)

    assert plan.changes == []
    assert "Svaret om 'Städning' sparas inte: inget pass ändras av det." in plan.notes


def test_a_slot_changed_after_review_is_a_conflict(app, session_factory, admins, arena):
    from duty_web.admin.plan_tokens import PlanTokenError

    _run(app, session_factory, admins["ansvarig"], _rows(*SEASON))
    (undated,) = [s.id for s in session_factory().query(Slot).filter_by(duty_name="Cafépass", date=None)]
    session = session_factory()
    ctx = admin_context(session, session.get(Person, admins["ansvarig"].id), "f14-bla")
    with app.test_request_context():
        _, token = dry_run(session, ctx, REGISTRY["schedule.import"],
                           ScheduleImportInput(rows=_rows(SEASON[0])), secret_key=SECRET)
        other = session_factory()
        other.get(Slot, undated).note = "Ta med nycklar"
        other.commit()

        with pytest.raises(PlanTokenError) as caught:
            apply_plan(session, ctx, token, secret_key=SECRET, via="webb")

    assert caught.value.status == 409
    assert session_factory().get(Slot, undated) is not None


def test_a_leaders_child_in_the_file_is_a_warning(app, session_factory, admins, seeded, arena):
    session = session_factory()
    session.get(Player, seeded["player"].id).exempt = True
    session.commit()

    plan = _run(app, session_factory, admins["ansvarig"], _rows(*SEASON), apply=False)

    assert "Tova Exempel är undantagen (ledarfamilj) men har 2 pass i filen." in plan.warnings


def test_the_form_takes_the_file_to_granska_with_the_phone(client, admins, csrf_token, arena, session_factory):
    login(client, admins["ansvarig"])

    page = client.post("/admin/f14-bla/do/schedule.import", data={
        "csrf_token": csrf_token(), "file": (BytesIO(coach_workbook(SEASON)), "schema.xlsx"),
    }, content_type="multipart/form-data").get_data(as_text=True)

    assert "+ 3 nya" in page and 'class="phone"' in page
    assert session_factory().query(Slot).filter_by(duty_name="Cafépass").count() == 0


def test_answering_a_name_on_an_unchanged_shift_shows_the_phone(
    client, admins, csrf_token, seeded, arena, session_factory
):
    ida = _ida(session_factory, seeded)
    data = coach_workbook([[46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None]])
    login(client, admins["ansvarig"])
    first = client.post("/admin/f14-bla/do/schedule.import", data={
        "csrf_token": csrf_token(), "file": (BytesIO(data), "schema.xlsx"),
    }, content_type="multipart/form-data").get_data(as_text=True)
    client.post("/admin/f14-bla/genomfor", data={
        "csrf_token": csrf_token(), "plan_token": re.search(r'name="plan_token" value="([^"]+)"', first).group(1),
    })

    page = client.post("/admin/f14-bla/do/schedule.import", data={
        "csrf_token": csrf_token(), "file": (BytesIO(data), "schema.xlsx"),
        "names": f'{{"Elin Exempel": {ida}}}',
    }, content_type="multipart/form-data").get_data(as_text=True)

    assert "Ledigt ('Elin Exempel') → Ida Ny" in unescape(page)
    phone = page[page.index('class="phone"'):]
    assert "Ida Ny" in phone


def test_a_file_that_isnt_xlsx_stays_on_the_form(client, admins, csrf_token):
    login(client, admins["ansvarig"])

    response = client.post("/admin/f14-bla/do/schedule.import", data={
        "csrf_token": csrf_token(), "file": (BytesIO(b"x"), "schema.csv"),
    }, content_type="multipart/form-data")

    assert response.status_code == 400
    assert "Välj coachens Excelfil (.xlsx)." in response.get_data(as_text=True)


@pytest.mark.parametrize("cells, message", [
    ([46, "Cafépass", SATURDAY, "Fre 21-18", "Tova Exempel", None, None], "Rad 4: ogiltig tid"),
    ([46, "Cafépass", "2026-11-31", "Fre 18-21", "Tova Exempel", None, None], "Rad 4: ogiltigt datum"),
])
def test_a_bad_row_is_shown_on_the_form_by_its_number(client, admins, csrf_token, session_factory, cells, message):
    login(client, admins["ansvarig"])

    response = client.post("/admin/f14-bla/do/schedule.import", data={
        "csrf_token": csrf_token(), "file": (BytesIO(coach_workbook([cells])), "schema.xlsx"),
    }, content_type="multipart/form-data")

    assert response.status_code == 400
    assert message in unescape(response.get_data(as_text=True))
    assert session_factory().query(Slot).filter_by(duty_name="Cafépass").count() == 0


def test_granskas_answer_replans_through_omplanera(client, admins, csrf_token, seeded, arena, session_factory):
    ida = _ida(session_factory, seeded)
    login(client, admins["ansvarig"])
    asked = client.post("/admin/f14-bla/do/schedule.import", data={
        "csrf_token": csrf_token(),
        "file": (BytesIO(coach_workbook([[46, "Cafépass", SATURDAY, "11:30-16:00", "Elin Exempel", None, None]])),
                 "schema.xlsx"),
    }, content_type="multipart/form-data").get_data(as_text=True)
    token = re.search(r'name="plan_token" value="([^"]+)"', asked).group(1)

    page = client.post("/admin/f14-bla/omplanera", data={
        "csrf_token": csrf_token(), "plan_token": token, "name::Elin Exempel": str(ida),
    }).get_data(as_text=True)

    # Jinja escapes the quotes, so compare the text a person reads.
    assert "Vem är 'Elin Exempel'?" in unescape(asked)
    assert "Ida Ny" in page and "Vem är" not in page


def test_both_doors_plan_the_same(client, admins, api_keys, csrf_token, arena):
    data = coach_workbook(SEASON)
    api = client.post("/admin/api/schedule.import", data={
        "team": "f14-bla", "file": (BytesIO(data), "schema.xlsx"),
    }, headers=bearer(api_keys["ansvarig"]), content_type="multipart/form-data").get_json()
    login(client, admins["ansvarig"])

    page = client.post("/admin/f14-bla/do/schedule.import", data={
        "csrf_token": csrf_token(), "file": (BytesIO(data), "schema.xlsx"),
    }, content_type="multipart/form-data").get_data(as_text=True)

    assert api["counts"]["add"] == 3
    for change in api["changes"]:
        assert change["summary"] in page
