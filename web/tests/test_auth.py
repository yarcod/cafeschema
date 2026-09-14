import time as time_module
from datetime import datetime, timedelta

import pytest
from itsdangerous import URLSafeTimedSerializer

from duty_web.auth import AuthError, issue_login_token, verify_login_token
from duty_web.db import init_db, make_engine, make_session_factory
from duty_web.models import LoginToken

SECRET = "test-secret"


def make_session():
    engine = make_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)()


def test_issued_token_verifies_to_the_same_email():
    session = make_session()
    token = issue_login_token(session, "tova@exempel.se", secret_key=SECRET)

    email = verify_login_token(session, token, secret_key=SECRET)

    assert email == "tova@exempel.se"


def test_token_cannot_be_used_twice():
    session = make_session()
    token = issue_login_token(session, "tova@exempel.se", secret_key=SECRET)
    verify_login_token(session, token, secret_key=SECRET)

    with pytest.raises(AuthError, match="redan använts"):
        verify_login_token(session, token, secret_key=SECRET)


def test_token_rejected_after_expiry():
    session = make_session()
    issued_at = datetime(2026, 1, 1, 12, 0)
    token = issue_login_token(
        session, "tova@exempel.se", secret_key=SECRET, now=issued_at
    )

    with pytest.raises(AuthError, match="upphört"):
        verify_login_token(
            session, token, secret_key=SECRET,
            now=issued_at + timedelta(minutes=21),
        )


def test_tampered_token_is_rejected():
    session = make_session()
    token = issue_login_token(session, "tova@exempel.se", secret_key=SECRET)
    other_serializer = URLSafeTimedSerializer("wrong-secret", salt="login")
    forged = other_serializer.dumps("attacker@exempel.se")

    with pytest.raises(AuthError, match="ogiltig"):
        verify_login_token(session, forged, secret_key=SECRET)


def test_unknown_token_is_rejected():
    session = make_session()

    with pytest.raises(AuthError, match="ogiltig"):
        verify_login_token(session, "not-a-real-token", secret_key=SECRET)


def test_itsdangerous_signature_expiry_independent_of_db(monkeypatch):
    """Verify that itsdangerous's own signature-expiry mechanism rejects
    an expired token independent of the DB expires_at check.

    This isolates the SignatureExpired path in verify_login_token.
    itsdangerous uses real wall-clock time (time.time()) to embed and check
    the token's timestamp, independent of the 'now' parameter. By mocking
    time.time() to an old value during issue, we create a token with an old
    embedded timestamp. Then we restore real time and manually push the DB
    expires_at far into the future to isolate itsdangerous's own max_age
    rejection as the only thing that can fail.
    """
    session = make_session()

    # Save the original time.time function
    original_time = time_module.time

    # Mock time.time() to an old value while issuing so itsdangerous
    # embeds an old timestamp in the token.
    old_time = 1000000.0
    monkeypatch.setattr("time.time", lambda: old_time)

    token = issue_login_token(session, "tova@exempel.se", secret_key=SECRET)

    # Restore real time so the timestamp difference will be large
    monkeypatch.setattr("time.time", original_time)

    # Manually push the LoginToken.expires_at far into the future
    # so the DB-side check won't reject it.
    token_row = session.query(LoginToken).filter(
        LoginToken.email == "tova@exempel.se"
    ).one()
    token_row.expires_at = datetime.utcnow() + timedelta(days=1)
    session.commit()

    # Now verify with real, unmocked time. itsdangerous will see that the
    # embedded timestamp is too old and reject it with SignatureExpired,
    # before the DB-side check even runs.
    with pytest.raises(AuthError, match="upphört"):
        verify_login_token(session, token, secret_key=SECRET)
