"""Per-parent settings — currently just the e-mail notification opt-out."""

from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..models import Person
from ..session_scope import get_session as _session

settings_bp = Blueprint("settings", __name__)


@settings_bp.route("/installningar", methods=["GET", "POST"])
@login_required
def installningar():
    session = _session()
    person = session.get(Person, int(current_user.id))

    if request.method == "POST":
        person.email_notifications = request.form.get("email_notifications") == "on"
        session.commit()
        return redirect(url_for("settings.installningar", sparat=1))

    return render_template(
        "installningar.html", person=person, saved=request.args.get("sparat") == "1"
    )
