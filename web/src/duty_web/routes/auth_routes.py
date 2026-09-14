"""Magic-link request + verification."""

from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from flask_login import login_user

from ..auth import (
    TOKEN_MAX_AGE_SECONDS,
    AuthError,
    issue_login_token,
    verify_login_token,
)
from ..clock import utcnow
from ..models import LoginToken, Person
from ..notifications import send_login_link
from ..session_scope import get_session as _session

auth_bp = Blueprint("auth", __name__)

RATE_LIMIT_SECONDS = 60


def _secret_key() -> str:
    return current_app.config["SECRET_KEY"]


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    email = request.form["email"].strip().lower()
    session = _session()

    # The response is identical whether or not this email belongs to a
    # registered parent (Person) — anything that differs by existence,
    # timing included, would let this form be used to enumerate the
    # roster. So: same template, same "sent_to" text, every time. Only the
    # side effects (issuing a token, sending mail) are gated on the email
    # actually being a registered Person, so this form can't be used to
    # spam an arbitrary inbox with real emails from our SMTP account.
    person = session.query(Person).filter_by(email=email).one_or_none()
    if person is not None:
        recent = (
            session.query(LoginToken)
            .filter(LoginToken.email == email)
            .order_by(LoginToken.expires_at.desc())
            .first()
        )
        already_rate_limited = False
        if recent is not None:
            issued_at = recent.expires_at - timedelta(seconds=TOKEN_MAX_AGE_SECONDS)
            already_rate_limited = utcnow() - issued_at < timedelta(seconds=RATE_LIMIT_SECONDS)

        if not already_rate_limited:
            token = issue_login_token(session, email, secret_key=_secret_key())
            link = url_for("auth.verify", token=token, _external=True)
            send_login_link(email, link)

    return render_template("login.html", sent_to=email)


@auth_bp.route("/login/verify")
def verify():
    session = _session()
    token = request.args.get("token", "")

    try:
        email = verify_login_token(session, token, secret_key=_secret_key())
    except AuthError as exc:
        return render_template("login.html", error=str(exc))

    person = session.query(Person).filter_by(email=email).one_or_none()
    if person is None:
        # No open self-registration: only emails already seeded as a Person
        # (i.e. a real parent from the imported schedule) may log in. Anyone
        # else round-tripping a valid magic link would otherwise get an
        # authenticated session and, via /team's fallback to the first Team,
        # read the whole roster and season calendar.
        return render_template(
            "login.html", error="Den adressen finns inte i schemat."
        )

    from ..app import LoginUser

    login_user(LoginUser(person), remember=True)
    return redirect(url_for("schedule.mine"))
