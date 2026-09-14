"""Magic-link issue/verify.

Two independent checks gate a login: itsdangerous's own signature + max_age
(catches tampering and enforces the 20-minute window even against a forged
timestamp), and a DB row tracking consumed_at (catches replay of a token
that is still within its time window but has already been used once).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from .clock import utcnow
from .models import LoginToken

TOKEN_MAX_AGE_SECONDS = 20 * 60
SALT = "login"


class AuthError(Exception):
    """Raised when a login link cannot be honored. Message is user-facing."""


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=SALT)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_login_token(
    session: Session,
    email: str,
    *,
    secret_key: str,
    now: datetime | None = None,
) -> str:
    now = now or utcnow()
    # itsdangerous embeds a second-resolution timestamp, so two tokens for
    # the same email issued within the same second would otherwise sign to
    # the exact same string; a nonce keeps every issued token unique.
    nonce = secrets.token_urlsafe(8)
    token = _serializer(secret_key).dumps({"email": email, "nonce": nonce})
    session.add(
        LoginToken(
            token_hash=_hash(token),
            email=email,
            expires_at=now + timedelta(seconds=TOKEN_MAX_AGE_SECONDS),
        )
    )
    session.commit()
    return token


def verify_login_token(
    session: Session,
    token: str,
    *,
    secret_key: str,
    now: datetime | None = None,
) -> str:
    now = now or utcnow()
    try:
        payload = _serializer(secret_key).loads(
            token, max_age=TOKEN_MAX_AGE_SECONDS
        )
    except SignatureExpired as exc:
        raise AuthError("Länken har upphört att gälla.") from exc
    except BadSignature as exc:
        raise AuthError("Länken är ogiltig.") from exc

    try:
        email = payload["email"]
    except (TypeError, KeyError) as exc:
        raise AuthError("Länken är ogiltig.") from exc

    row = (
        session.query(LoginToken)
        .filter(LoginToken.token_hash == _hash(token))
        .one_or_none()
    )
    if row is None:
        raise AuthError("Länken är ogiltig.")
    if row.consumed_at is not None:
        raise AuthError("Länken har redan använts.")
    if row.expires_at < now:
        raise AuthError("Länken har upphört att gälla.")

    row.consumed_at = now
    session.commit()
    return email
