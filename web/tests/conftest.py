import hashlib
import os

import pytest
from itsdangerous import URLSafeTimedSerializer

from duty_web.app import create_app
from duty_web.config import AppConfig
from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import Person, Slot, Team


@pytest.fixture
def app():
    config = AppConfig(
        secret_key="test-secret",
        db_path=":memory:",
        api_key="test-api-key",
        smtp_host="localhost", smtp_port=587, smtp_user="u",
        smtp_password="p", from_address="noreply@exempel.se",
    )
    application = create_app(config)
    application.config.update(TESTING=True)
    yield application


@pytest.fixture
def session_factory(app):
    return app.extensions["duty_web_session_factory"]


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded(session_factory):
    """A team, a logged-in-able person, and two of their slots.

    The 'slot' is a historical slot (Jan 16, 2026) for team-calendar tests.
    The 'future_slot' is always 30 days in the future for personal view tests.
    """
    session = session_factory()
    team = Team(name="F14 Blå", venue="Wallenstam arena")
    person = Person(name="Alva Exempel", email="tova@exempel.se")
    session.add_all([team, person])
    session.flush()
    from datetime import date, time, timedelta

    slot = Slot(
        team_id=team.id, date=date(2026, 1, 16), start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=person.id,
    )
    # Compute the next 16th of any month (ensures test assertion "16" in response works forever)
    today = date.today()
    if today.day < 16:
        future_slot_date = date(today.year, today.month, 16)
    else:
        # Next month's 16th
        if today.month == 12:
            future_slot_date = date(today.year + 1, 1, 16)
        else:
            future_slot_date = date(today.year, today.month + 1, 16)

    future_slot = Slot(
        team_id=team.id, date=future_slot_date, start_time=time(18, 0),
        end_time=time(21, 0), station="Cafe", duty_name="Arena värdskap",
        venue="Wallenstam arena", person_id=person.id,
    )
    session.add_all([slot, future_slot])
    session.commit()
    return {"team": team, "person": person, "slot": slot, "future_slot": future_slot}


@pytest.fixture
def csrf_token(client, app):
    """Generate a CSRF token bound to the client's session cookie.

    Flask-WTF's own ``generate_csrf()`` reads/writes ``flask.session``,
    which is only bound to a live request context — but
    ``client.session_transaction()``'s ``sess`` object is a separate,
    directly-addressable view of the same session store used *outside*
    any request context. So this replicates generate_csrf()'s algorithm
    (see flask_wtf.csrf.generate_csrf) against that `sess` object instead:
    same session field ("csrf_token"), same signing scheme
    (URLSafeTimedSerializer with the app's secret key and the
    "wtf-csrf-token" salt) — producing a token the real CSRFProtect
    middleware validates identically to one a rendered form would carry.
    """
    def make_token():
        with client.session_transaction() as sess:
            if "csrf_token" not in sess:
                sess["csrf_token"] = hashlib.sha1(os.urandom(64)).hexdigest()
            serializer = URLSafeTimedSerializer(app.secret_key, salt="wtf-csrf-token")
            return serializer.dumps(sess["csrf_token"])

    return make_token
