from datetime import date, time

from duty_web.models import Slot

from .conftest import make_family


def _login(client, person):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(person.id)
        sess["_fresh"] = True


def _target_family(session_factory, team, day=23):
    """Another family holding one slot — two parents, so fan-out shows up."""
    session = session_factory()
    player = make_family(
        session, team, "Sara Bergman",
        [("Sara B", "sara@exempel.se"), ("Samir B", "samir@exempel.se")],
    )
    slot = Slot(
        team_id=team.id, date=date(2026, 1, day), start_time=time(17, 30),
        end_time=time(20, 30), station="Entré", duty_name="Arena värdskap",
        venue="Wallenstam arena", player_id=player.id, child_name=player.name,
    )
    session.add(slot)
    session.commit()
    return player, slot


def test_post_swaps_creates_a_pending_request_and_notifies_both_target_parents(
    client, app, session_factory, seeded, monkeypatch, csrf_token
):
    notified = []
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed",
        lambda to, **kwargs: notified.append(list(to)),
    )
    _, target_slot = _target_family(session_factory, seeded["team"])

    _login(client, seeded["person"])
    token = csrf_token()
    response = client.post(
        "/swaps",
        data={
            "proposer_slot_id": seeded["slot"].id,
            "target_slot_ids": [str(target_slot.id)],
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert notified == [["sara@exempel.se", "samir@exempel.se"]]


def test_post_swaps_accept_route_swaps_ownership_and_notifies_proposer(
    client, app, session_factory, seeded, monkeypatch, csrf_token
):
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed", lambda to, **kwargs: None
    )
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_accepted",
        lambda to, **kwargs: notified.append((list(to), kwargs)),
    )
    notified: list[tuple[list[str], dict]] = []
    session = session_factory()
    target_player, target_slot = _target_family(session_factory, seeded["team"])
    accepter = target_player.parents[0]

    _login(client, seeded["person"])
    token = csrf_token()
    client.post(
        "/swaps",
        data={
            "proposer_slot_id": seeded["slot"].id,
            "target_slot_ids": [str(target_slot.id)],
            "csrf_token": token,
        },
    )
    from duty_web.models import SwapRequest

    swap_id = session.query(SwapRequest).one().id

    _login(client, accepter)
    token = csrf_token()
    response = client.post(
        f"/swaps/{swap_id}/accept",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    proposer_slot = session.get(Slot, seeded["slot"].id)
    assert proposer_slot.player_id == target_player.id
    assert len(notified) == 1
    to, kwargs = notified[0]
    # Both of the proposing family's parents hear that it went through.
    assert to == [seeded["person"].email, seeded["other_parent"].email]
    # The accepter is the parent who clicked (Sara) — a bug in the original
    # draft read this off the slot's *post-swap* owner, which by this
    # point was already the original proposer, notifying the wrong name.
    assert kwargs["accepter_name"] == accepter.name
    # Pin the old/new slot description strings too, not just the names —
    # this is exactly the part of the same bug (reading slot details off
    # the rows after the ownership swap already happened) that a mere
    # name/recipient check wouldn't catch: from the original proposer's
    # perspective, "your_old_slot" is what they gave up (their own,
    # original proposer_slot's date/station/time — seeded["slot"]) and
    # "your_new_slot" is what they now have (the target_slot's
    # date/station/time, which they've just acquired via the swap).
    from duty_web.routes.swap_routes import _format_slot

    assert kwargs["your_old_slot"] == _format_slot(seeded["slot"])
    assert kwargs["your_new_slot"] == _format_slot(target_slot)


def test_post_swaps_decline_route_notifies_proposer_and_leaves_ownership_unchanged(
    client, app, session_factory, seeded, monkeypatch, csrf_token
):
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed", lambda to, **kwargs: None
    )
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_declined",
        lambda to, **kwargs: notified.append((list(to), kwargs)),
    )
    notified: list[tuple[list[str], dict]] = []
    session = session_factory()
    target_player, target_slot = _target_family(session_factory, seeded["team"])
    decliner = target_player.parents[0]

    _login(client, seeded["person"])
    token = csrf_token()
    client.post(
        "/swaps",
        data={
            "proposer_slot_id": seeded["slot"].id,
            "target_slot_ids": [str(target_slot.id)],
            "csrf_token": token,
        },
    )
    from duty_web.models import SwapRequest

    swap_id = session.query(SwapRequest).one().id

    _login(client, decliner)
    token = csrf_token()
    response = client.post(
        f"/swaps/{swap_id}/decline",
        data={"csrf_token": token},
        follow_redirects=False,
    )

    assert response.status_code == 302
    proposer_slot = session.get(Slot, seeded["slot"].id)
    assert proposer_slot.player_id == seeded["player"].id
    assert len(notified) == 1
    to, kwargs = notified[0]
    assert to == [seeded["person"].email, seeded["other_parent"].email]
    # The decliner is the parent who clicked (Sara), not the proposer — a
    # bug in the original draft passed the proposer's own name here.
    assert kwargs["decliner_name"] == decliner.name


def test_the_other_parent_can_answer_a_request_addressed_to_the_family(
    client, app, session_factory, seeded, monkeypatch, csrf_token
):
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed", lambda to, **kwargs: None
    )
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_accepted", lambda to, **kwargs: None
    )
    session = session_factory()
    target_player, target_slot = _target_family(session_factory, seeded["team"])

    # The first parent proposes; the *second* parent of the other family
    # accepts. Neither of them ever "owned" the slot personally.
    _login(client, seeded["person"])
    token = csrf_token()
    client.post(
        "/swaps",
        data={
            "proposer_slot_id": seeded["slot"].id,
            "target_slot_ids": [str(target_slot.id)],
            "csrf_token": token,
        },
    )
    from duty_web.models import SwapRequest

    swap_id = session.query(SwapRequest).one().id

    _login(client, target_player.parents[1])
    token = csrf_token()
    response = client.post(f"/swaps/{swap_id}/accept", data={"csrf_token": token})

    assert response.status_code == 302
    assert session.get(Slot, seeded["slot"].id).player_id == target_player.id


def test_post_swaps_without_csrf_token_is_rejected(client, seeded):
    _login(client, seeded["person"])
    response = client.post(
        "/swaps",
        data={"proposer_slot_id": seeded["slot"].id, "target_slot_ids": ["1"]},
    )

    assert response.status_code == 400


def test_post_swaps_accept_on_an_already_resolved_request_is_a_400_not_a_500(
    client, app, session_factory, seeded, monkeypatch, csrf_token
):
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_proposed", lambda to, **kwargs: None
    )
    monkeypatch.setattr(
        "duty_web.routes.swap_routes.send_swap_declined", lambda to, **kwargs: None
    )
    session = session_factory()
    target_player, target_slot = _target_family(session_factory, seeded["team"])

    _login(client, seeded["person"])
    token = csrf_token()
    client.post(
        "/swaps",
        data={
            "proposer_slot_id": seeded["slot"].id,
            "target_slot_ids": [str(target_slot.id)],
            "csrf_token": token,
        },
    )
    from duty_web.models import SwapRequest

    swap_id = session.query(SwapRequest).one().id

    _login(client, target_player.parents[0])
    token = csrf_token()
    client.post(f"/swaps/{swap_id}/decline", data={"csrf_token": token})

    # The request is now declined; accepting it again must not 500.
    token = csrf_token()
    response = client.post(f"/swaps/{swap_id}/accept", data={"csrf_token": token})

    assert response.status_code == 400


def test_post_swaps_decline_on_an_unknown_request_is_a_400_not_a_500(
    client, seeded, app, csrf_token
):
    _login(client, seeded["person"])
    token = csrf_token()

    response = client.post("/swaps/999999/decline", data={"csrf_token": token})

    assert response.status_code == 400


def test_post_swaps_with_a_non_numeric_proposer_slot_id_is_a_400_not_a_500(
    client, seeded, app, csrf_token
):
    _login(client, seeded["person"])
    token = csrf_token()

    response = client.post(
        "/swaps",
        data={"proposer_slot_id": "not-a-number", "target_slot_ids": ["1"], "csrf_token": token},
    )

    assert response.status_code == 400


def test_post_swaps_with_a_missing_proposer_slot_id_is_a_400_not_a_500(
    client, seeded, app, csrf_token
):
    _login(client, seeded["person"])
    token = csrf_token()

    response = client.post(
        "/swaps", data={"target_slot_ids": ["1"], "csrf_token": token}
    )

    assert response.status_code == 400


def test_post_swaps_with_an_unfilled_target_slot_does_not_crash_or_create_a_request(
    client, app, session_factory, seeded, csrf_token
):
    """A bogus or unfilled target_slot_id (e.g. 'Hela laget' rendering an
    empty slot as clickable) must not commit an orphan SwapRequest and then
    crash looking up the target slot's parents (I3)."""
    session = session_factory()
    from datetime import date, time

    from duty_web.models import Slot, SwapRequest

    unfilled_slot = Slot(
        team_id=seeded["team"].id, date=date(2026, 1, 23), start_time=time(17, 30),
        end_time=time(20, 30), station="Entré", duty_name="Arena värdskap",
        venue="Wallenstam arena", player_id=None,
    )
    session.add(unfilled_slot)
    session.commit()

    _login(client, seeded["person"])
    token = csrf_token()
    response = client.post(
        "/swaps",
        data={
            "proposer_slot_id": seeded["slot"].id,
            "target_slot_ids": [str(unfilled_slot.id)],
            "csrf_token": token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert session.query(SwapRequest).count() == 0
