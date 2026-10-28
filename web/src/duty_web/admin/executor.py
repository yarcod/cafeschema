"""Runs actions for both doors: dry_run produces a plan and its token,
apply_plan re-plans from the token and writes only if nothing changed."""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .actions import REGISTRY
from .actions.base import Action, Plan
from .errors import NearDuplicate, ValidationError
from .permissions import AdminContext
from .plan_tokens import PlanTokenError, read_plan_token, sign_plan

logger = logging.getLogger("duty_web.admin")


def scope_allows(action: Action, ctx: AdminContext) -> bool:
    """Team actions need a team; club actions only need an admin role somewhere."""
    return action.scope != "team" or ctx.team is not None


def _team_id(ctx: AdminContext) -> int | None:
    return ctx.team.id if ctx.team is not None else None


def dry_run(
    session: Session, ctx: AdminContext, action: Action, inp, *, secret_key: str
) -> tuple[Plan, str]:
    plan = action.plan(session, ctx, inp)
    session.rollback()  # plan() is pure; make sure nothing it touched lingers
    token = sign_plan(
        secret_key, action=action.name, team_id=_team_id(ctx),
        person_id=ctx.person.id, inp=inp, digest=plan.digest(),
    )
    return plan, token


def apply_plan(
    session: Session,
    ctx: AdminContext,
    token: str,
    *,
    secret_key: str,
    via: str,
    expect: str | None = None,
) -> tuple[Action, Plan]:
    """Apply what was reviewed, or raise PlanTokenError.

    404 when the token isn't this person's, this team's, or this URL's
    action; 409 when the plan no longer comes out the same (data changed,
    or the token was already used); 400 when it was tampered with.
    """
    data = read_plan_token(secret_key, token)
    action = REGISTRY.get(data["a"])
    if (
        action is None
        or (expect is not None and data["a"] != expect)
        or data["p"] != ctx.person.id
        or data["t"] != _team_id(ctx)
        or not scope_allows(action, ctx)
    ):
        raise PlanTokenError(404, "not_found")
    try:
        inp = action.Input(**data["i"])
    except TypeError as exc:
        raise PlanTokenError(400, "tampered_plan") from exc

    try:
        plan = action.plan(session, ctx, inp)
    except (ValidationError, NearDuplicate) as exc:
        # Valid when reviewed, invalid now: something changed in between.
        session.rollback()
        raise PlanTokenError(409, "stale_plan") from exc
    if plan.digest() != data["d"]:
        session.rollback()
        raise PlanTokenError(409, "stale_plan")
    try:
        for write in plan.writes:
            write(session)
        session.commit()
    except IntegrityError as exc:
        # Someone else wrote the same name between our plan and our commit.
        session.rollback()
        # Values are hidden by the engine (hide_parameters); the constraint
        # name tells a real bug apart from a lost race.
        logger.warning("Krock vid genomförande: %s (%s)", action.name, exc.orig)
        raise PlanTokenError(409, "conflict") from exc
    except Exception:
        session.rollback()
        raise

    for callback in plan.after_commit:
        try:
            callback()
        except Exception as exc:
            # No exc_info and no message: an SMTP error can carry the recipient's address.
            logger.error("Steg efter genomförande misslyckades: %s (%s)", action.name, type(exc).__name__)

    logger.info(
        "admin %s lag=%s ändringar=%d via=%s",
        action.name, ctx.team.slug if ctx.team else "-", len(plan.changes), via,
    )
    return action, plan
