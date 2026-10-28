"""The workbench for humans: ✎, the team page, and an action's way through Granska."""

import re

from duty_web.admin.ratelimit import RateLimiter
from duty_web.models import Slot, Venue

from .conftest import login


def _token(body):
    return re.search(r'name="plan_token" value="([^"]+)"', body).group(1)


def _review(client, csrf_token, text="Hindås", count="1", slug="f14-bla"):
    return client.post(
        f"/admin/{slug}/do/tests.echo",
        data={"csrf_token": csrf_token(), "text": text, "count": count},
    )


def test_an_ansvarig_sees_the_pen_and_a_parent_does_not(client, admins):
    login(client, admins["ansvarig"])
    assert 'aria-label="Hantera"' in client.get("/").get_data(as_text=True)

    for who in ("parent", "leader"):
        login(client, admins[who])
        assert 'aria-label="Hantera"' not in client.get("/").get_data(as_text=True)


def test_admin_requires_login(client):
    response = client.get("/admin/")

    assert response.status_code == 302 and "/login" in response.headers["Location"]


def test_admin_home_goes_to_the_first_team(client, admins):
    login(client, admins["ansvarig"])

    response = client.get("/admin/")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/f14-bla/lag")


def test_without_rights_admin_does_not_exist(client, admins):
    login(client, admins["parent"])
    assert client.get("/admin/").status_code == 404
    assert client.get("/admin/f14-bla/lag").status_code == 404

    login(client, admins["ansvarig"])
    assert client.get("/admin/p13/lag").status_code == 404


def test_a_club_admin_switches_between_every_team(client, admins):
    login(client, admins["club"])

    body = client.get("/admin/p13/lag").get_data(as_text=True)

    assert "F14 Blå" in body and "P13" in body


def test_the_team_page_lists_names_never_addresses(client, admins):
    login(client, admins["ansvarig"])

    response = client.get("/admin/f14-bla/lag")
    body = response.get_data(as_text=True)

    assert "Ansvariga" in body and "Alva Exempel" in body and "Maja Granne" in body
    assert "@exempel.se" not in body
    assert response.headers["Cache-Control"] == "no-store"


def test_an_action_goes_through_granska(client, admins, echo, csrf_token):
    login(client, admins["ansvarig"])
    assert client.get("/admin/f14-bla/do/tests.echo").status_code == 200

    body = _review(client, csrf_token, count="2").get_data(as_text=True)

    assert "Hindås 0" in body and "Hindås 1" in body
    assert "Genomför 2 ändringar" in body and 'name="plan_token"' in body


def test_genomfor_applies_and_returns_to_the_team(client, admins, echo, csrf_token, session_factory):
    login(client, admins["ansvarig"])
    token = _token(_review(client, csrf_token).get_data(as_text=True))

    response = client.post(
        "/admin/f14-bla/genomfor", data={"csrf_token": csrf_token(), "plan_token": token}
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/admin/f14-bla/lag?klart=tests.echo")
    assert [v.name for v in session_factory().query(Venue)] == ["Hindås"]


def test_genomfor_twice_says_something_changed(client, admins, echo, csrf_token, session_factory):
    login(client, admins["ansvarig"])
    token = _token(_review(client, csrf_token).get_data(as_text=True))
    data = {"csrf_token": csrf_token(), "plan_token": token}
    client.post("/admin/f14-bla/genomfor", data=data)

    response = client.post("/admin/f14-bla/genomfor", data=data)

    assert response.status_code == 409
    assert "Något har ändrats" in response.get_data(as_text=True)
    assert session_factory().query(Venue).count() == 1


def test_validation_errors_stay_on_the_form(client, admins, echo, csrf_token):
    login(client, admins["ansvarig"])

    response = _review(client, csrf_token, text="")

    assert response.status_code == 400 and "Saknas" in response.get_data(as_text=True)


def test_genomfor_needs_csrf(client, admins, echo, csrf_token):
    login(client, admins["ansvarig"])
    token = _token(_review(client, csrf_token).get_data(as_text=True))

    assert client.post("/admin/f14-bla/genomfor", data={"plan_token": token}).status_code == 400


def test_a_token_stays_with_its_person_and_team(client, admins, echo, csrf_token, session_factory):
    login(client, admins["club"])
    club_token = _token(_review(client, csrf_token).get_data(as_text=True))
    # The club admin's own token, posted to another team's URL.
    assert client.post(
        "/admin/p13/genomfor", data={"csrf_token": csrf_token(), "plan_token": club_token}
    ).status_code == 404

    login(client, admins["ansvarig"])
    # Someone else's token, on the right team.
    assert client.post(
        "/admin/f14-bla/genomfor", data={"csrf_token": csrf_token(), "plan_token": club_token}
    ).status_code == 404
    assert session_factory().query(Venue).count() == 0


def test_admin_posts_are_rate_limited(client, admins, echo, csrf_token, monkeypatch):
    from duty_web.admin import ratelimit

    monkeypatch.setattr(ratelimit, "admin_limiter", RateLimiter(2, 60))
    login(client, admins["ansvarig"])

    statuses = [_review(client, csrf_token).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]


def test_the_limiter_forgets_old_hits():
    limiter = RateLimiter(2, 60)

    assert [limiter.allow("k", now=t) for t in (0, 1, 2)] == [True, True, False]
    assert limiter.allow("k", now=61) is True


def test_granska_shows_the_phone_after_the_change_and_writes_nothing(
    client, admins, echo, csrf_token, session_factory
):
    login(client, admins["ansvarig"])

    page = client.post(
        "/admin/f14-bla/do/tests.echo", data={"csrf_token": csrf_token(), "text": "slot"}
    ).get_data(as_text=True)

    assert 'class="phone"' in page and "09:00–12:00" in page and "Eko" in page
    assert session_factory().query(Slot).filter_by(duty_name="Eko").count() == 0


def test_granska_without_days_has_no_phone(client, admins, echo, csrf_token):
    login(client, admins["ansvarig"])

    page = client.post(
        "/admin/f14-bla/do/tests.echo", data={"csrf_token": csrf_token(), "text": "a"}
    ).get_data(as_text=True)

    assert 'class="phone"' not in page


def test_granska_answers_replan_the_reviewed_input(client, admins, echo, csrf_token):
    login(client, admins["ansvarig"])
    first = client.post(
        "/admin/f14-bla/do/tests.echo", data={"csrf_token": csrf_token(), "text": "a"}
    ).get_data(as_text=True)

    response = client.post("/admin/f14-bla/omplanera", data={
        "csrf_token": csrf_token(), "plan_token": _token(first), "text": "b",
    })

    assert response.status_code == 200
    assert "b 0" in response.get_data(as_text=True)


def test_slumpa_om_replans(client, admins, echo, csrf_token):
    login(client, admins["ansvarig"])
    first = client.post(
        "/admin/f14-bla/do/tests.echo", data={"csrf_token": csrf_token(), "text": "a"}
    ).get_data(as_text=True)
    assert "Slumpa om" in first

    page = client.post("/admin/f14-bla/omplanera", data={
        "csrf_token": csrf_token(), "plan_token": _token(first), "reshuffle": "1",
    }).get_data(as_text=True)

    assert "a 1" in page


def test_an_error_without_a_field_still_shows(client, admins, echo, csrf_token, monkeypatch):
    from duty_web.admin.errors import ValidationError

    def refuse(self, session, ctx, inp):
        raise ValidationError({"rows[3].start": "Rad 5: ogiltig tid"})

    monkeypatch.setattr(type(echo), "plan", refuse)
    login(client, admins["ansvarig"])

    response = _review(client, csrf_token)

    assert response.status_code == 400
    assert 'class="error-summary"' in response.get_data(as_text=True)
    assert "Rad 5: ogiltig tid" in response.get_data(as_text=True)


def test_omplanera_refuses_someone_elses_token(client, admins, echo, csrf_token):
    login(client, admins["ansvarig"])
    first = client.post(
        "/admin/f14-bla/do/tests.echo", data={"csrf_token": csrf_token(), "text": "a"}
    ).get_data(as_text=True)
    login(client, admins["club"])

    response = client.post("/admin/f14-bla/omplanera", data={
        "csrf_token": csrf_token(), "plan_token": _token(first), "text": "b",
    })

    assert response.status_code == 404


def test_omplanera_is_404_for_an_action_without_questions(
    client, admins, echo, csrf_token, monkeypatch
):
    login(client, admins["ansvarig"])
    first = client.post(
        "/admin/f14-bla/do/tests.echo", data={"csrf_token": csrf_token(), "text": "a"}
    ).get_data(as_text=True)
    monkeypatch.setattr(type(echo), "refinable", False)

    response = client.post("/admin/f14-bla/omplanera", data={
        "csrf_token": csrf_token(), "plan_token": _token(first), "text": "b",
    })

    assert response.status_code == 404
