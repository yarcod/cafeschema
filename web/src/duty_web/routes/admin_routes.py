"""Admin, the HTML door (spec D1, D3): pages, action forms, Granska, Genomför."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup
from sqlalchemy import func

from ..admin import ratelimit
from ..agenda import group_days, group_shifts
from ..admin.api_keys import revoke_api_key
from ..admin.actions import REGISTRY
from ..admin.actions.base import Action, NearDuplicate, ValidationError, fields_schema
from ..admin.executor import apply_plan, dry_run, preview, scope_allows
from ..admin.permissions import AdminContext, admin_context, admin_teams
from ..admin.plan_tokens import PlanTokenError, read_plan_token
from ..admin.roster_view import roster_groups
from ..clock import utcnow
from ..models import ApiKey, Person, Slot, Venue
from ..places import linkable_documents, rank_documents
from ..session_scope import get_session as _session

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")
PREVIEW_DAYS = 10


def _context(slug: str | None) -> AdminContext:
    person = _session().get(Person, int(current_user.id))
    ctx = admin_context(_session(), person, slug)
    if ctx is None:
        abort(404)
    return ctx


def _action(ctx: AdminContext, name: str) -> Action:
    action = REGISTRY.get(name)
    if action is None or not scope_allows(action, ctx):
        abort(404)
    return action


def _throttle() -> None:
    if not ratelimit.admin_limiter.allow(("web", int(current_user.id))):
        abort(429)


def _phone(session, ctx: AdminContext, plan) -> Markup | None:
    """The team's agenda on the planned days, the way parents will see it."""
    if ctx.team is None:
        return None
    team_id = ctx.team.id
    days = sorted(plan.preview_dates)
    shown, more = days[:PREVIEW_DAYS], max(0, len(days) - PREVIEW_DAYS)

    def render(s):
        dated = (
            s.query(Slot).filter(Slot.team_id == team_id, Slot.date.in_(shown))
            .order_by(Slot.date, Slot.id).all()
        ) if shown else []
        undated = (
            s.query(Slot).filter(Slot.team_id == team_id, Slot.date.is_(None))
            .order_by(Slot.duty_name, Slot.id).all()
        ) if plan.preview_undated else []
        # Our own template's output, escaped as it was rendered.
        return Markup(render_template(
            "admin/_phone.html", days=group_days(group_shifts(dated)),
            undated_shifts=group_shifts(undated), more=more,
        ))

    return preview(session, plan, render)


def _granska(session, ctx: AdminContext, action: Action, plan, token: str):
    return render_template(
        "admin/granska.html", ctx=ctx, action=action, plan=plan, plan_token=token,
        phone=_phone(session, ctx, plan),
    )


@admin_bp.after_request
def _no_store(response):
    # Granska carries a plan token and the pages list the team's people.
    response.headers["Cache-Control"] = "no-store"
    return response


@admin_bp.route("/")
@login_required
def home():
    ctx = _context(None)
    return redirect(url_for("admin.lag", slug=ctx.teams[0].slug))


@admin_bp.route("/<slug>/lag")
@login_required
def lag(slug: str):
    ctx = _context(slug)
    session = _session()
    team_keys = []
    if ctx.is_club_admin:
        # Every active key in the club, not just current ansvariga's: a key
        # outlives the role it was made under and must stay revocable.
        team_keys = (
            session.query(ApiKey)
            .filter(ApiKey.revoked_at.is_(None), ApiKey.expires_at > utcnow())
            .order_by(ApiKey.created_at, ApiKey.id)
            .all()
        )
    return render_template(
        "admin/lag.html", ctx=ctx, groups=roster_groups(session, ctx.team),
        team_keys=team_keys, done=request.args.get("klart"),
    )


@admin_bp.route("/<slug>/do/<name>", methods=["GET", "POST"])
@login_required
def do_action(slug: str, name: str):
    ctx = _context(slug)
    action = _action(ctx, name)
    session = _session()

    def form(errors: dict[str, str], status: int = 200):
        extra = {"labels": {}, "choices": {}, **action.form_context(session, ctx)}
        return render_template(
            action.template, ctx=ctx, action=action, errors=errors,
            form=request.form if request.method == "POST" else request.args,
            fields=fields_schema(action.Input), **extra,
        ), status

    if request.method == "GET":
        return form({})
    _throttle()
    try:
        inp = action.parse(request.form, request.files)
        plan, token = dry_run(
            session, ctx, action, inp, secret_key=current_app.config["SECRET_KEY"]
        )
    except ValidationError as exc:
        return form(exc.errors, 400)
    except NearDuplicate as exc:
        return form({exc.field: f"Finns redan: {exc.existing_name}. Välj den i stället."}, 409)
    return _granska(session, ctx, action, plan, token)


@admin_bp.route("/<slug>/genomfor", methods=["POST"])
@login_required
def genomfor(slug: str):
    ctx = _context(slug)
    _throttle()
    try:
        action, _ = apply_plan(
            _session(), ctx, request.form.get("plan_token", ""),
            secret_key=current_app.config["SECRET_KEY"], via="webb",
        )
    except PlanTokenError as exc:
        if exc.status == 404:
            abort(404)
        return render_template("admin/stale.html", ctx=ctx, code=exc.code), exc.status
    session = _session()
    person = session.get(Person, int(current_user.id))
    if admin_context(session, person, slug) is None:
        # The action took this person's own access to the team away.
        if admin_teams(session, person.id):
            return redirect(url_for("admin.home"))
        return redirect(url_for("schedule.mine"))
    return redirect(url_for(action.done_endpoint, slug=slug, klart=action.name))


@admin_bp.route("/<slug>/omplanera", methods=["POST"])
@login_required
def omplanera(slug: str):
    """Granska's answers (or Slumpa om): re-plan the reviewed input with them."""
    ctx = _context(slug)
    _throttle()
    session = _session()
    secret_key = current_app.config["SECRET_KEY"]
    try:
        data = read_plan_token(secret_key, request.form.get("plan_token", ""))
    except PlanTokenError as exc:
        if exc.status == 404:
            abort(404)
        return render_template("admin/stale.html", ctx=ctx, code=exc.code), exc.status
    action = REGISTRY.get(data["a"])
    if (
        action is None
        or not action.refinable
        or data["p"] != ctx.person.id
        or data["t"] != ctx.team.id
        or not scope_allows(action, ctx)
    ):
        abort(404)
    try:
        inp = action.refine(action.Input(**data["i"]), request.form)
        plan, token = dry_run(session, ctx, action, inp, secret_key=secret_key)
    except TypeError:
        abort(400)
    except (ValidationError, NearDuplicate):
        return render_template("admin/stale.html", ctx=ctx, code="stale_plan"), 409
    return _granska(session, ctx, action, plan, token)


@admin_bp.route("/<slug>/nycklar/<int:key_id>/aterkalla", methods=["POST"])
@login_required
def revoke_key(slug: str, key_id: int):
    ctx = _context(slug)
    # A club admin may revoke anyone's key, never see or make one.
    if not ctx.is_club_admin or revoke_api_key(_session(), key_id) is None:
        abort(404)
    return redirect(url_for("admin.lag", slug=slug, klart="api_key.revoke"))


@admin_bp.route("/<slug>/platser")
@login_required
def platser(slug: str):
    ctx = _context(slug)
    session = _session()
    venues = session.query(Venue).order_by(Venue.name).all()
    usage = dict(
        session.query(Slot.station_id, func.count(Slot.id))
        .filter(Slot.station_id.isnot(None))
        .group_by(Slot.station_id)
        .all()
    )
    documents = linkable_documents()
    linked = {s.document for v in venues for s in v.stations if s.document}
    return render_template(
        "admin/platser.html", ctx=ctx, venues=venues, usage=usage, documents=documents,
        unlinked=[d for d in documents if d.filename not in linked], rank=rank_documents,
        done=request.args.get("klart"),
    )
