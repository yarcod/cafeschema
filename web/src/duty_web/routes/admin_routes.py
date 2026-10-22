"""Admin, the HTML door (spec D1, D3): pages, action forms, Granska, Genomför."""

from __future__ import annotations

from flask import Blueprint, abort, current_app, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..admin import ratelimit
from ..admin.actions import REGISTRY
from ..admin.actions.base import Action, NearDuplicate, ValidationError, fields_schema
from ..admin.executor import apply_plan, dry_run, scope_allows
from ..admin.permissions import AdminContext, admin_context
from ..admin.plan_tokens import PlanTokenError
from ..admin.roster_view import roster_groups
from ..models import Person
from ..session_scope import get_session as _session

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


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
    return render_template(
        "admin/lag.html", ctx=ctx, groups=roster_groups(_session(), ctx.team),
        done=request.args.get("klart"),
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
            action.template, ctx=ctx, action=action, errors=errors, form=request.form,
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
    return render_template(
        "admin/granska.html", ctx=ctx, action=action, plan=plan, plan_token=token
    )


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
    return redirect(url_for(action.done_endpoint, slug=slug, klart=action.name))
