"""Instructions and other documents parents need before a duty.

Files live on the Fly volume rather than in the image, so adding one doesn't
need a deploy (see `just docs-push`). The reminder mail links here instead of
attaching anything, so there is one copy to keep up to date.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, current_app, render_template, send_from_directory
from flask_login import login_required

document_bp = Blueprint("documents", __name__)

# Anything a parent could reasonably need to read on a phone. Kept to a
# known list so a stray file on the volume can't be served as something the
# browser will execute.
ALLOWED_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".md"}


def _documents_dir() -> Path:
    return Path(current_app.extensions["duty_web_app_config"].documents_dir)


def _documents() -> list[dict]:
    directory = _documents_dir()
    if not directory.is_dir():
        return []
    return [
        {
            "name": path.stem.replace("_", " "),
            "filename": path.name,
            "suffix": path.suffix.lstrip(".").upper(),
            "size_kb": max(1, round(path.stat().st_size / 1024)),
        }
        for path in sorted(directory.iterdir())
        if path.is_file() and path.suffix.lower() in ALLOWED_SUFFIXES
    ]


@document_bp.route("/dokument")
@login_required
def dokument():
    return render_template("dokument.html", documents=_documents())


@document_bp.route("/dokument/<path:filename>")
@login_required
def download(filename: str):
    # send_from_directory refuses to escape the directory, but the allowlist
    # also keeps the response to types we intend to hand out.
    if Path(filename).suffix.lower() not in ALLOWED_SUFFIXES:
        abort(404)
    return send_from_directory(_documents_dir(), filename)
