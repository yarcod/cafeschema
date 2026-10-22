"""Per-parent settings: the e-mail opt-out, and admins' API keys."""

from __future__ import annotations

from flask import Blueprint, abort, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..admin.api_keys import active_keys, create_api_key, revoke_api_key
from ..admin.permissions import admin_teams
from ..models import ApiKey, Person
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
        "installningar.html", person=person, saved=request.args.get("sparat") == "1",
        api_keys=active_keys(session, person.id),
    )


@settings_bp.route("/installningar/nycklar", methods=["POST"])
@login_required
def create_key():
    session = _session()
    person_id = int(current_user.id)
    if not admin_teams(session, person_id):
        abort(404)
    row, plaintext = create_api_key(session, person_id)
    # Rendered here and never again: the response that carries the key must
    # not be stored by the browser or anything in between.
    response = make_response(
        render_template("nyckel_skapad.html", plaintext=plaintext, prefix=row.prefix)
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@settings_bp.route("/installningar/nycklar/<int:key_id>/aterkalla", methods=["POST"])
@login_required
def revoke_key(key_id: int):
    session = _session()
    row = session.get(ApiKey, key_id)
    if row is None or row.person_id != int(current_user.id):
        abort(404)
    revoke_api_key(session, key_id)
    return redirect(url_for("settings.installningar", sparat=1) + "#api-nycklar")
