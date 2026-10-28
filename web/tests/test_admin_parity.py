"""Every action exists for both humans and agents, and is tested (spec D1)."""

from pathlib import Path

import pytest

from duty_web.admin.actions import REGISTRY

from .conftest import bearer, login

REAL_ACTIONS = sorted(name for name in REGISTRY if not name.startswith("tests."))
TESTS = Path(__file__).parent


def test_the_registry_has_the_v1_actions():
    assert REAL_ACTIONS == [
        "roster.import", "schedule.import", "slot.delete", "slot.update", "slots.assign",
        "slots.create", "station.create", "station.link_document", "team_admins.add",
        "team_admins.remove", "venue.create",
    ]


@pytest.mark.parametrize("name", REAL_ACTIONS)
def test_an_action_has_a_form(app, name):
    app.jinja_env.get_template(REGISTRY[name].template)


@pytest.mark.parametrize("name", REAL_ACTIONS)
def test_an_action_has_a_test_file_naming_it(name):
    sources = "".join(p.read_text(encoding="utf-8") for p in TESTS.glob("test_action_*.py"))

    assert f'"{name}"' in sources


@pytest.mark.parametrize("name", REAL_ACTIONS)
def test_an_action_answers_at_both_doors(client, admins, api_keys, csrf_token, name):
    api = client.post(f"/admin/api/{name}", json={"team": "f14-bla"}, headers=bearer(api_keys["club"]))
    login(client, admins["club"])
    html = client.post(f"/admin/f14-bla/do/{name}", data={"csrf_token": csrf_token()})

    # Empty input: refused as invalid at both doors, never "no such page".
    assert api.status_code == 400 and api.get_json()["errors"]
    assert html.status_code == 400
