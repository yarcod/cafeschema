"""Admin, the agent door (spec D1): the same actions as JSON, behind a key.

The key goes in the Authorization header and nowhere else. Session cookies
are never consulted here, so a logged-in browser isn't an API client and
CSRF doesn't apply. Rights are resolved on every call from the key's
person, so removing an ansvarig locks her agent out on its next request.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request

from ..admin import ratelimit
from ..admin.actions import REGISTRY
from ..admin.actions.base import NearDuplicate, ValidationError, fields_schema
from ..admin.api_keys import KEY_PREFIX, authenticate
from ..admin.executor import apply_plan, dry_run, scope_allows
from ..admin.permissions import AdminContext, admin_context
from ..admin.plan_tokens import PlanTokenError
from ..models import ApiKey
from ..session_scope import get_session as _session

api_bp = Blueprint("admin_api", __name__, url_prefix="/admin/api")

_KEY_PARAMS = ("api_key", "apikey", "key", "token", "access_token")
_NOT_FOUND = {"error": "not_found"}


class _Refused(Exception):
    def __init__(self, status: int, body: dict[str, Any]):
        super().__init__(status)
        self.status = status
        self.body = body


@api_bp.errorhandler(_Refused)
def _refused(exc: _Refused):
    return jsonify(exc.body), exc.status


@api_bp.after_request
def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


def _key() -> ApiKey:
    if any(name in request.args for name in _KEY_PARAMS) or any(
        value.startswith(KEY_PREFIX) for value in request.cookies.values()
    ):
        raise _Refused(400, {
            "error": "key_in_wrong_place",
            "message": "Skicka nyckeln i Authorization-headern, aldrig i adressen eller en cookie.",
        })
    scheme, _, presented = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        raise _Refused(401, {"error": "unauthorized"})
    key = authenticate(_session(), presented.strip())
    if key is None:
        raise _Refused(401, {"error": "unauthorized"})
    if not ratelimit.api_limiter.allow(("api", key.id)):
        raise _Refused(429, {"error": "rate_limited"})
    return key


def _context(key: ApiKey, slug: str | None) -> AdminContext:
    ctx = admin_context(_session(), key.person, slug)
    if ctx is None:
        raise _Refused(404, _NOT_FOUND)
    return ctx


def _payload():
    if request.is_json:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise _Refused(400, {"errors": {"_": "Skicka ett JSON-objekt"}})
        return data
    return request.form


def _is_false(value: Any) -> bool:
    return value is False or str(value).strip().lower() in ("false", "0", "no", "nej")


@api_bp.route("", methods=["GET"])
def discovery():
    ctx = _context(_key(), None)
    return jsonify({
        "teams": [team.slug for team in ctx.teams],
        "actions": {
            name: {
                "title": action.title,
                "scope": action.scope,
                "fields": fields_schema(action.Input),
                "uploads": list(action.uploads),
            }
            for name, action in sorted(REGISTRY.items())
        },
    })


@api_bp.route("/<name>", methods=["POST"])
def run(name: str):
    key = _key()
    payload = _payload()
    action = REGISTRY.get(name)
    if action is None:
        raise _Refused(404, _NOT_FOUND)
    slug = payload.get("team") or None
    if action.scope == "team" and not slug:
        raise _Refused(400, {"errors": {"team": "Ange lagets slug, t.ex. f15"}})
    ctx = _context(key, slug)
    if not scope_allows(action, ctx):
        raise _Refused(404, _NOT_FOUND)

    session = _session()
    secret_key = current_app.config["SECRET_KEY"]
    try:
        if _is_false(payload.get("dry_run", True)):
            token = payload.get("plan_token")
            if not token:
                raise ValidationError({"plan_token": "Saknas. Kör först med dry_run: true."})
            _, plan = apply_plan(
                session, ctx, str(token), secret_key=secret_key, via=key.prefix, expect=name
            )
            return jsonify({"applied": True, **plan.public()})
        inp = action.parse(payload, request.files)
        plan, token = dry_run(session, ctx, action, inp, secret_key=secret_key)
        return jsonify({**plan.public(), "plan_token": token})
    except ValidationError as exc:
        return jsonify({"errors": exc.errors}), 400
    except NearDuplicate as exc:
        return jsonify({
            "error": "near_duplicate",
            "field": exc.field,
            "existing": {"id": exc.existing_id, "name": exc.existing_name},
        }), 409
    except PlanTokenError as exc:
        return jsonify({"error": exc.code}), exc.status
