"""The agent's door: same actions, same rights, a personal key in a header."""

import logging
from datetime import time

from duty_web.admin.ratelimit import RateLimiter
from duty_web.models import ROLE_ANSVARIG, Slot, TeamRole, Venue

from .conftest import bearer, login, run_action


def _echo(client, key, **body):
    return client.post("/admin/api/tests.echo", json={"team": "f14-bla", **body}, headers=bearer(key))


def test_no_key_or_a_wrong_key_is_unauthorized(client, api_keys):
    assert client.get("/admin/api").status_code == 401
    assert client.get("/admin/api", headers=bearer("cs_nope")).status_code == 401
    assert client.get("/admin/api", headers={"Authorization": api_keys["club"]}).status_code == 401


def test_a_logged_in_browser_is_not_an_api_key(client, admins, api_keys):
    login(client, admins["club"])

    assert client.get("/admin/api").status_code == 401


def test_a_key_outside_the_header_is_refused(client, api_keys):
    key = api_keys["club"]

    assert client.get(f"/admin/api?api_key={key}").status_code == 400
    client.set_cookie("nyckel", key)
    assert client.get("/admin/api", headers=bearer(key)).status_code == 400


def test_discovery_lists_my_teams_and_the_actions(client, echo, api_keys):
    body = client.get("/admin/api", headers=bearer(api_keys["ansvarig"])).get_json()

    assert body["teams"] == ["f14-bla"]
    assert body["actions"]["tests.echo"] == {
        "title": "Eko", "scope": "team", "uploads": [], "agent_note": "",
        "fields": {"text": "string", "count": "integer?"},
    }


def test_a_key_without_admin_rights_finds_nothing(client, api_keys):
    assert client.get("/admin/api", headers=bearer(api_keys["parent"])).status_code == 404


def test_dry_run_is_the_default_and_writes_nothing(client, echo, api_keys, session_factory):
    body = _echo(client, api_keys["ansvarig"], text="Hindås").get_json()

    assert [c["summary"] for c in body["changes"]] == ["Hindås 0"]
    assert body["counts"] == {"add": 1, "update": 0, "delete": 0} and body["plan_token"]
    assert session_factory().query(Venue).count() == 0


def test_apply_with_the_token_then_never_again(client, echo, api_keys, session_factory):
    key = api_keys["ansvarig"]
    token = _echo(client, key, text="Hindås").get_json()["plan_token"]

    applied = _echo(client, key, dry_run=False, plan_token=token)
    replayed = _echo(client, key, dry_run=False, plan_token=token)

    assert applied.status_code == 200 and applied.get_json()["applied"] is True
    assert (replayed.status_code, replayed.get_json()) == (409, {"error": "stale_plan"})
    assert session_factory().query(Venue).count() == 1


def test_apply_needs_a_token(client, echo, api_keys):
    response = _echo(client, api_keys["ansvarig"], dry_run=False)

    assert response.status_code == 400 and "plan_token" in response.get_json()["errors"]


def test_other_teams_and_unknown_actions_are_not_found(client, echo, api_keys):
    key = api_keys["ansvarig"]

    assert _echo(client, key, team="p13", text="x").status_code == 404
    assert client.post("/admin/api/finns.inte", json={"team": "f14-bla"}, headers=bearer(key)).status_code == 404


def test_a_team_action_needs_a_team(client, echo, api_keys):
    response = client.post("/admin/api/tests.echo", json={"text": "x"}, headers=bearer(api_keys["club"]))

    assert response.status_code == 400 and "team" in response.get_json()["errors"]


def test_validation_errors_name_the_fields(client, echo, api_keys):
    response = _echo(client, api_keys["ansvarig"], text="", count="tre")

    assert response.status_code == 400
    assert response.get_json() == {"errors": {"text": "Saknas", "count": "Måste vara ett heltal"}}


def test_a_removed_ansvarig_is_locked_out_on_the_next_call(client, echo, admins, api_keys, session_factory):
    key = api_keys["ansvarig"]
    assert _echo(client, key, text="x").status_code == 200
    session = session_factory()
    session.query(TeamRole).filter_by(person_id=admins["ansvarig"].id, role=ROLE_ANSVARIG).delete()
    session.commit()

    assert _echo(client, key, text="x").status_code == 404


def test_the_api_is_rate_limited(client, echo, api_keys, monkeypatch):
    from duty_web.admin import ratelimit

    monkeypatch.setattr(ratelimit, "api_limiter", RateLimiter(2, 60))
    key = api_keys["ansvarig"]

    assert [_echo(client, key, text="x").status_code for _ in range(3)] == [200, 200, 429]


def test_responses_are_never_cached(client, echo, api_keys):
    assert _echo(client, api_keys["ansvarig"], text="x").headers["Cache-Control"] == "no-store"


def test_the_key_never_reaches_the_logs(app, client, echo, api_keys, caplog):
    app.config["PROPAGATE_EXCEPTIONS"] = False
    key = api_keys["ansvarig"]
    caplog.set_level(logging.DEBUG)

    token = _echo(client, key, text="Hindås").get_json()["plan_token"]
    _echo(client, key, dry_run=False, plan_token=token)
    crashed = _echo(client, key, text="boom")

    assert crashed.status_code == 500
    assert key not in caplog.text
    assert key.split("_")[0] + "_" + key.split("_")[1] in caplog.text  # the prefix is logged


def test_a_key_in_any_query_value_is_refused(client, api_keys):
    assert client.get(f"/admin/api?x={api_keys['club']}").status_code == 400


def test_a_key_in_a_form_field_is_refused_and_nothing_applied(client, echo, api_keys, session_factory):
    key = api_keys["ansvarig"]
    token = _echo(client, key, text="Hindås").get_json()["plan_token"]

    response = client.post(
        "/admin/api/tests.echo",
        data={"team": "f14-bla", "dry_run": "false", "plan_token": token, "api_key": key},
        headers=bearer(key),
    )

    assert response.status_code == 400
    assert key not in response.get_data(as_text=True)
    assert session_factory().query(Venue).count() == 0


def test_a_key_in_a_json_value_is_refused(client, echo, api_keys):
    key = api_keys["ansvarig"]

    assert _echo(client, key, text="x", note=key).status_code == 400


def test_a_foreign_cookie_starting_with_cs_does_not_block(client, api_keys):
    client.set_cookie("annan", "cs_inte_en_nyckel")

    assert client.get("/admin/api", headers=bearer(api_keys["club"])).status_code == 200


def test_agents_read_the_batch_with_names_only(client, app, session_factory, admins, api_keys, seeded):
    slot = seeded["future_slot"]
    run_action(app, session_factory, admins["ansvarig"], "pending.edit", slot_ids=[slot.id], start="19:00")

    response = client.get("/admin/api/pending?team=f14-bla", headers=bearer(api_keys["ansvarig"]))

    body = response.get_json()
    assert response.status_code == 200 and body["team"] == "f14-bla"
    (entry,) = body["entries"]
    assert set(entry) == {"entry_id", "day", "op", "summary", "detail", "conflict", "missing", "drawn"}
    assert entry["op"] == "update" and entry["conflict"] is None and entry["drawn"] is False
    assert "19:00" in entry["summary"] and "tid 18:00" in entry["detail"]
    assert all(set(event) == {"key", "open_seats"} for event in body["events"])
    assert "@" not in response.get_data(as_text=True)
    assert response.headers["Cache-Control"] == "no-store"


def test_an_empty_batch_still_lists_events(client, admins, api_keys, seeded, session_factory):
    session = session_factory()
    session.add(Slot(team_id=seeded["team"].id, date=seeded["future_slot"].date, start_time=time(9),
                     end_time=time(12), station="Cafe", duty_name="Cafépass", venue="Arena"))
    session.commit()

    body = client.get("/admin/api/pending?team=f14-bla", headers=bearer(api_keys["ansvarig"])).get_json()

    assert body["entries"] == []
    assert any(event["open_seats"] == 1 for event in body["events"])


def test_the_batch_is_not_readable_for_another_team_or_without_a_key(client, api_keys, second_team):
    other = client.get("/admin/api/pending?team=p13", headers=bearer(api_keys["ansvarig"]))

    assert other.status_code == 404
    assert client.get("/admin/api/pending?team=f14-bla").status_code == 401
    assert client.get("/admin/api/pending", headers=bearer(api_keys["ansvarig"])).status_code == 400
