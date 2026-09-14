"""Request-scoped SQLAlchemy session (I7).

Every route module used to call ``current_app.extensions["duty_web_session_factory"]()``
directly, handing back a brand-new Session that nothing ever closed — on the
file-backed engine this relied entirely on refcounting/GC to return pooled
connections, and combined with SQLite's default locking behavior, "database
is locked" under concurrent public+admin process access was a plausible
operational issue.

``get_session()`` caches one Session per request in ``flask.g`` (mirroring
the one-session-per-request-handler pattern every route already followed in
practice), and ``close_session`` — registered as a ``teardown_appcontext``
handler in ``create_app`` — closes it once the response has been built.
Every view already reads whatever relationship attributes it needs *before*
returning (render_template renders synchronously inside the view, and
jsonify() serializes synchronously too), so closing the session at teardown
time, after the view has returned, does not risk a DetachedInstanceError.
"""

from __future__ import annotations

from flask import current_app, g
from sqlalchemy.orm import Session

_G_KEY = "duty_web_session"


def get_session() -> Session:
    if _G_KEY not in g:
        session_factory = current_app.extensions["duty_web_session_factory"]
        g.duty_web_session = session_factory()
    return g.duty_web_session


def close_session(exception: BaseException | None = None) -> None:
    session = g.pop(_G_KEY, None)
    if session is not None:
        session.close()
