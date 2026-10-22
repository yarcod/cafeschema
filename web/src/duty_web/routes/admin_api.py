"""Admin, the agent door (spec D1): the same actions as JSON, behind a key.

The key goes in the Authorization header and nowhere else. Session cookies
are never consulted here, so a logged-in browser isn't an API client and
CSRF doesn't apply. Rights are resolved on every call from the key's
person, so removing an ansvarig locks her agent out on its next request.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import func

from ..admin import ratelimit
from ..admin.actions import REGISTRY
from ..admin.actions.base import NearDuplicate, ValidationError, fields_schema
from ..admin.api_keys import authenticate
from ..admin.executor import apply_plan, dry_run, scope_allows
from ..admin.permissions import AdminContext, admin_context
from ..admin.plan_tokens import PlanTokenError
from ..admin.redact import KEY_PATTERN
from ..models import ApiKey, Player, Slot, TeamRole, Venue
from ..places import linkable_documents
from ..roster import public_name
from ..session_scope import get_session as _session

api_bp = Blueprint("admin_api", __name__, url_prefix="/admin/api")

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


def _carries_a_key() -> bool:
    values = [*request.args.values(), *request.cookies.values(), *request.form.values()]
    body = request.get_json(silent=True) if request.is_json else None
    if isinstance(body, dict):
        values.extend(v for v in body.values() if isinstance(v, str))
    return any(KEY_PATTERN.search(value) for value in values)


def _key() -> ApiKey:
    if _carries_a_key():
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


@api_bp.route("/roster", methods=["GET"])
def roster():
    """The team's players and parents, with addresses: for its admins only."""
    key = _key()
    slug = request.args.get("team") or None
    if not slug:
        raise _Refused(400, {"errors": {"team": "Ange lagets slug, t.ex. f15"}})
    ctx = _context(key, slug)
    session = _session()
    roles: dict[int, list[str]] = {}
    for role in session.query(TeamRole).filter_by(team_id=ctx.team.id):
        roles.setdefault(role.person_id, []).append(role.role)
    players = session.query(Player).filter_by(team_id=ctx.team.id).order_by(Player.name)
    return jsonify({
        "team": ctx.team.slug,
        "players": [
            {
                "id": player.id,
                "name": player.name,
                "exempt": player.exempt,
                "parents": [
                    {"id": parent.id, "name": public_name(parent), "email": parent.email,
                     "roles": sorted(roles.get(parent.id, []))}
                    for parent in player.parents
                ],
            }
            for player in players
        ],
    })


@api_bp.route("/venues", methods=["GET"])
def venues():
    _context(_key(), None)
    session = _session()
    usage = dict(
        session.query(Slot.station_id, func.count(Slot.id))
        .filter(Slot.station_id.isnot(None))
        .group_by(Slot.station_id)
        .all()
    )
    return jsonify([
        {
            "id": venue.id,
            "name": venue.name,
            "stations": [
                {"id": s.id, "name": s.name, "document": s.document, "slots": usage.get(s.id, 0)}
                for s in venue.stations
            ],
        }
        for venue in session.query(Venue).order_by(Venue.name)
    ])


@api_bp.route("/documents", methods=["GET"])
def documents():
    _context(_key(), request.args.get("team") or None)
    return jsonify([
        {"filename": d.filename, "team": None, "title": d.title}
        for d in linkable_documents()
    ])
