"""Instructions and other documents parents need before a duty.

Files live on the Fly volume rather than in the image, so adding one doesn't
need a deploy (see `just docs-push`). The reminder mail links here instead of
attaching anything, so there is one copy to keep up to date.

Both the mail and Att göra link to the one instruction that duty needs
(`document_url_for_duty`) rather than to the index, so nobody has to pick the
right café's PDF out of a list on the way to their shift.

Two levels of documents: files at the root of the documents folder are
shared by the whole club; files in <documents_dir>/<team slug>/ are that
team's own, visible only to parents with a child on it. A duty's
instruction is looked up in the slot's team folder first, so a team can
keep its own version of a shared instruction under the same name.
"""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from ..duties import profile_for
from ..places import ALLOWED_SUFFIXES, document_for_slot
from ..roster import teams_for_parent
from ..session_scope import get_session as _session

document_bp = Blueprint("documents", __name__)

def _documents_dir() -> Path:
    return Path(current_app.extensions["duty_web_app_config"].documents_dir)


def _documents(directory: Path, slug: str | None = None) -> list[dict]:
    if not directory.is_dir():
        return []
    return [
        {
            "name": path.stem.replace("_", " "),
            "filename": path.name,
            "suffix": path.suffix.lstrip(".").upper(),
            "size_kb": max(1, round(path.stat().st_size / 1024)),
            "url": (
                url_for("documents.team_download", slug=slug, filename=path.name)
                if slug
                else url_for("documents.download", filename=path.name)
            ),
        }
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
    ]


def _url_for_document(
    document: str | None, team_slug: str | None, *, external: bool
) -> str | None:
    """Where a named instruction can be downloaded, or None if it isn't there.

    The team's own copy wins over the shared one. None also covers "mapped,
    but not uploaded yet": a link to a file the volume doesn't have would
    404 on the morning of the shift.
    """
    if not document:
        return None
    if team_slug and (_documents_dir() / team_slug / document).is_file():
        return url_for(
            "documents.team_download", slug=team_slug, filename=document,
            _external=external,
        )
    if (_documents_dir() / document).is_file():
        return url_for("documents.download", filename=document, _external=external)
    return None


def instruction_url_for_duty(
    duty_name: str | None, team_slug: str | None = None, *, external: bool = False
) -> str | None:
    """This duty's own instruction per duties.py, or None."""
    profile = profile_for(duty_name)
    return _url_for_document(
        profile.document if profile else None, team_slug, external=external
    )


def instruction_url_for_slot(slot, *, external: bool = False) -> str | None:
    """This slot's own instruction: its station's, or its duty's until linked."""
    return _url_for_document(document_for_slot(slot), slot.team.slug, external=external)


def document_url_for_duty(
    duty_name: str | None, team_slug: str | None = None, *, external: bool = False
) -> str:
    """Where Att göra's "Läs instruktionerna" goes — the index as a fallback,
    so the button always leads somewhere readable."""
    return instruction_url_for_duty(duty_name, team_slug, external=external) or url_for(
        "documents.dokument", _external=external
    )


def document_url_for_slot(slot) -> str:
    return instruction_url_for_slot(slot) or url_for("documents.dokument")


@document_bp.route("/dokument")
@login_required
def dokument():
    root = _documents_dir()
    teams = teams_for_parent(_session(), int(current_user.id))
    per_team = [(team, _documents(root / team.slug, team.slug)) for team in teams]
    # A shared document that every one of your teams has its own copy of is
    # never the one you need, so it isn't listed. One that only some of them
    # replace still is: the others use it.
    replaced = (
        set.intersection(*({d["filename"] for d in docs} for _, docs in per_team))
        if per_team
        else set()
    )
    shared = [d for d in _documents(root) if d["filename"] not in replaced]
    team_sections = [
        {"team": team, "documents": docs} for team, docs in per_team if docs
    ]
    total = len(shared) + sum(len(s["documents"]) for s in team_sections)
    return render_template(
        "dokument.html", shared=shared, team_sections=team_sections, total=total
    )


# <filename>, not <path:filename>: a slash here would reach into a team's
# folder without the membership check below.
@document_bp.route("/dokument/<filename>")
@login_required
def download(filename: str):
    # send_from_directory refuses to escape the directory, but the allowlist
    # also keeps the response to types we intend to hand out.
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        abort(404)
    return send_from_directory(_documents_dir(), filename)


@document_bp.route("/dokument/<slug>/<filename>")
@login_required
def team_download(slug: str, filename: str):
    slugs = {team.slug for team in teams_for_parent(_session(), int(current_user.id))}
    # Not found, like any other team's page: whether the file exists is
    # none of a non-member's business.
    if slug not in slugs or Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        abort(404)
    return send_from_directory(_documents_dir() / slug, filename)
