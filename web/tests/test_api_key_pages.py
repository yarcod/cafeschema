"""Keys are made and revoked by their owner; a club admin can only revoke."""

import re

from sqlalchemy import text

from duty_web.admin.api_keys import create_api_key
from duty_web.models import ApiKey

from .conftest import bearer, login


def _create(client, csrf_token):
    return client.post("/installningar/nycklar", data={"csrf_token": csrf_token()})


def _plaintext(body):
    return re.search(r"(cs_[0-9a-f]{8}_[A-Za-z0-9_-]{40,})", body).group(1)


def test_a_new_key_is_shown_once_and_never_cached(client, admins, csrf_token, session_factory):
    login(client, admins["ansvarig"])

    response = _create(client, csrf_token)
    body = response.get_data(as_text=True)
    key = _plaintext(body)

    assert response.headers["Cache-Control"] == "no-store"
    assert "visas bara en gång" in body
    stored = " ".join(str(v) for r in session_factory().execute(text("SELECT * FROM api_keys")) for v in r)
    assert key not in stored
    settings = client.get("/installningar").get_data(as_text=True)
    assert key not in settings and key[:11] in settings  # the prefix, not the key


def test_the_new_key_works_on_the_api(client, admins, csrf_token):
    login(client, admins["ansvarig"])
    key = _plaintext(_create(client, csrf_token).get_data(as_text=True))

    assert client.get("/admin/api", headers=bearer(key)).status_code == 200


def test_only_admins_can_make_keys(client, admins, csrf_token):
    login(client, admins["parent"])

    assert _create(client, csrf_token).status_code == 404
    assert "API-nyckel" not in client.get("/installningar").get_data(as_text=True)


def test_an_owner_revokes_her_own_key_only(client, admins, csrf_token, session_factory):
    session = session_factory()
    mine, key = create_api_key(session, admins["ansvarig"].id)
    theirs, _ = create_api_key(session, admins["club"].id)
    login(client, admins["ansvarig"])

    assert client.post(
        f"/installningar/nycklar/{theirs.id}/aterkalla", data={"csrf_token": csrf_token()}
    ).status_code == 404
    client.post(f"/installningar/nycklar/{mine.id}/aterkalla", data={"csrf_token": csrf_token()})

    assert client.get("/admin/api", headers=bearer(key)).status_code == 401
    assert session_factory().get(ApiKey, theirs.id).revoked_at is None


def test_a_club_admin_sees_and_revokes_an_ansvarigs_key(client, admins, csrf_token, session_factory):
    row, key = create_api_key(session_factory(), admins["ansvarig"].id)
    login(client, admins["club"])

    page = client.get("/admin/f14-bla/lag").get_data(as_text=True)
    assert row.prefix in page and key not in page
    client.post(f"/admin/f14-bla/nycklar/{row.id}/aterkalla", data={"csrf_token": csrf_token()})

    assert session_factory().get(ApiKey, row.id).revoked_at is not None


def test_an_ansvarig_cannot_revoke_others_or_see_the_list(client, admins, csrf_token, session_factory):
    row, _ = create_api_key(session_factory(), admins["club"].id)
    login(client, admins["ansvarig"])

    assert "API-nycklar" not in client.get("/admin/f14-bla/lag").get_data(as_text=True)
    assert client.post(
        f"/admin/f14-bla/nycklar/{row.id}/aterkalla", data={"csrf_token": csrf_token()}
    ).status_code == 404
