"""Personal keys for the admin API (spec, *Security*).

A key is 256 random bits behind a short public prefix. Only SHA-256 of it
is stored: for a random key of that length a slow hash adds nothing, and
this is the scheme GitHub and Stripe use. The plaintext exists once, in the
response that created it, and is never logged, stored or sent anywhere.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..clock import utcnow
from ..models import ApiKey

KEY_PREFIX = "cs_"
KEY_LIFETIME = timedelta(days=180)
# Far longer than any real key; refuses junk before hashing it.
_MAX_KEY_LENGTH = 200


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def create_api_key(
    session: Session, person_id: int, *, now: datetime | None = None
) -> tuple[ApiKey, str]:
    now = now or utcnow()
    prefix = f"{KEY_PREFIX}{secrets.token_hex(4)}"
    plaintext = f"{prefix}_{secrets.token_urlsafe(32)}"
    row = ApiKey(
        person_id=person_id,
        prefix=prefix,
        key_hash=hash_key(plaintext),
        created_at=now,
        expires_at=now + KEY_LIFETIME,
    )
    session.add(row)
    session.commit()
    return row, plaintext


def authenticate(
    session: Session, presented: str | None, *, now: datetime | None = None
) -> ApiKey | None:
    if (
        not presented
        or not presented.startswith(KEY_PREFIX)
        or len(presented) > _MAX_KEY_LENGTH
    ):
        return None
    now = now or utcnow()
    digest = hash_key(presented)
    row = session.query(ApiKey).filter(ApiKey.key_hash == digest).one_or_none()
    if row is None or not hmac.compare_digest(row.key_hash, digest):
        return None
    if row.revoked_at is not None or row.expires_at <= now:
        return None
    row.last_used_at = now
    session.commit()
    return row


def active_keys(
    session: Session, person_id: int, *, now: datetime | None = None
) -> list[ApiKey]:
    now = now or utcnow()
    return (
        session.query(ApiKey)
        .filter(
            ApiKey.person_id == person_id,
            ApiKey.revoked_at.is_(None),
            ApiKey.expires_at > now,
        )
        .order_by(ApiKey.created_at.desc(), ApiKey.id.desc())
        .all()
    )


def revoke_api_key(
    session: Session, key_id: int, *, now: datetime | None = None
) -> ApiKey | None:
    row = session.get(ApiKey, key_id)
    if row is None:
        return None
    if row.revoked_at is None:
        row.revoked_at = now or utcnow()
        session.commit()
    return row
