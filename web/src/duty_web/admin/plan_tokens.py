"""Signed plan tokens: what was reviewed, bound to who reviewed it.

The token carries the action, team, person, the input, and the digest of
the plan that was shown. It is signed (HMAC via itsdangerous, keyed from
SECRET_KEY), not encrypted: it travels only back to the person who
produced it. It expires after 30 minutes.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SALT = "admin-plan"
MAX_AGE_SECONDS = 30 * 60
_KEYS = {"a", "t", "p", "i", "d"}


class PlanTokenError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _serializer(secret_key: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret_key, salt=SALT)


def sign_plan(
    secret_key: str, *, action: str, team_id: int | None, person_id: int, inp: Any, digest: str
) -> str:
    return _serializer(secret_key).dumps(
        {"a": action, "t": team_id, "p": person_id, "i": dataclasses.asdict(inp), "d": digest}
    )


def read_plan_token(
    secret_key: str, token: str, *, max_age: int = MAX_AGE_SECONDS
) -> dict[str, Any]:
    try:
        data = _serializer(secret_key).loads(token, max_age=max_age)
    except SignatureExpired as exc:
        raise PlanTokenError(409, "stale_plan") from exc
    except BadSignature as exc:
        raise PlanTokenError(400, "tampered_plan") from exc
    if not isinstance(data, dict) or set(data) != _KEYS:
        raise PlanTokenError(400, "tampered_plan")
    return data
