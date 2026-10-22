"""A key exists in plaintext exactly once: in the response that created it."""

from datetime import datetime, timedelta

from sqlalchemy import text

from duty_web.admin.api_keys import (
    KEY_LIFETIME,
    active_keys,
    authenticate,
    create_api_key,
    hash_key,
    revoke_api_key,
)
from duty_web.models import ApiKey

NOW = datetime(2026, 10, 23, 12, 0)


def test_only_a_hash_is_stored(session_factory, seeded):
    session = session_factory()

    row, plaintext = create_api_key(session, seeded["person"].id, now=NOW)

    stored = " ".join(
        str(value) for record in session.execute(text("SELECT * FROM api_keys"))
        for value in record
    )
    assert plaintext not in stored
    assert row.key_hash == hash_key(plaintext)
    assert plaintext.startswith(row.prefix + "_") and row.prefix.startswith("cs_")
    assert row.expires_at == NOW + KEY_LIFETIME


def test_two_keys_are_never_alike(session_factory, seeded):
    session = session_factory()
    _, first = create_api_key(session, seeded["person"].id, now=NOW)
    _, second = create_api_key(session, seeded["person"].id, now=NOW)

    assert first != second and len(first) > 40


def test_the_right_key_authenticates_and_is_marked_used(session_factory, seeded):
    session = session_factory()
    row, plaintext = create_api_key(session, seeded["person"].id, now=NOW)

    later = NOW + timedelta(hours=1)
    assert authenticate(session, plaintext, now=later).id == row.id
    assert session.get(ApiKey, row.id).last_used_at == later


def test_wrong_missing_or_malformed_keys_are_refused(session_factory, seeded):
    session = session_factory()
    _, plaintext = create_api_key(session, seeded["person"].id, now=NOW)

    assert authenticate(session, plaintext + "x", now=NOW) is None
    assert authenticate(session, None, now=NOW) is None
    assert authenticate(session, "", now=NOW) is None
    assert authenticate(session, plaintext[3:], now=NOW) is None  # without cs_


def test_an_expired_key_is_refused(session_factory, seeded):
    session = session_factory()
    _, plaintext = create_api_key(session, seeded["person"].id, now=NOW)

    assert authenticate(session, plaintext, now=NOW + KEY_LIFETIME) is None


def test_a_revoked_key_is_refused_and_no_longer_listed(session_factory, seeded):
    session = session_factory()
    row, plaintext = create_api_key(session, seeded["person"].id, now=NOW)
    kept, _ = create_api_key(session, seeded["person"].id, now=NOW)

    revoke_api_key(session, row.id, now=NOW)

    assert authenticate(session, plaintext, now=NOW) is None
    assert [k.id for k in active_keys(session, seeded["person"].id, now=NOW)] == [kept.id]
    assert revoke_api_key(session, 9999, now=NOW) is None
