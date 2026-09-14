"""Flask app factory. Every route requires login except /login*."""

from __future__ import annotations

import calendar
import zlib
from datetime import timedelta

from flask import Flask, request
from flask_login import LoginManager, UserMixin, current_user
from flask_wtf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import AppConfig
from .db import init_db, make_engine, make_session_factory
from .dates import MONTHS_SV, long_date_sv, short_date_sv, weekday_sv
from .models import Person
from .schedule_queries import swaps_pending_for_parent
from .session_scope import close_session, get_session


class LoginUser(UserMixin):
    """Adapts a Person row to Flask-Login's expected interface."""

    def __init__(self, person: Person):
        self.person = person
        self.id = str(person.id)


def create_app(config: AppConfig) -> Flask:
    app = Flask(__name__)

    # Fly's proxy terminates TLS and forwards plain HTTP, so without this
    # url_for(_external=True) builds http:// links — and the magic-link
    # mail would then send login tokens over an unencrypted first request.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    app.config["SECRET_KEY"] = config.secret_key
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["PERMANENT_SESSION_LIFETIME"] = 60 * 24 * 60 * 60  # 60 days, seconds
    app.config["REMEMBER_COOKIE_DURATION"] = timedelta(days=60)
    app.config["REMEMBER_COOKIE_SECURE"] = True
    app.config["REMEMBER_COOKIE_HTTPONLY"] = True
    app.config["REMEMBER_COOKIE_SAMESITE"] = "Lax"

    engine = make_engine(config.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    app.extensions["duty_web_session_factory"] = session_factory
    app.extensions["duty_web_app_config"] = config

    from .notifications import configure as configure_notifications

    configure_notifications(
        host=config.smtp_host, port=config.smtp_port, user=config.smtp_user,
        password=config.smtp_password, from_address=config.from_address,
    )

    login_manager = LoginManager()
    login_manager.login_view = "auth.login"

    @login_manager.user_loader
    def load_user(user_id: str) -> LoginUser | None:
        session = get_session()
        person = session.get(Person, int(user_id))
        return LoginUser(person) if person else None

    login_manager.init_app(app)
    csrf = CSRFProtect(app)

    if config.public_hosts:
        @app.before_request
        def reject_unknown_host():
            """Refuse requests carrying a Host we don't serve.

            url_for(_external=True) builds the magic link from the request's
            host, and ProxyFix trusts the proxy's X-Forwarded-Host — so
            without this, a poisoned Host header would mint a login link
            pointing at someone else's domain and mail the token there.
            """
            hostname = request.host.split(":")[0].lower()
            if hostname not in config.public_hosts:
                return "Okänd värd", 400

    # Duties aren't drawn from a fixed, known-in-advance set of categories
    # (the schedule's source spreadsheet adds new activity names each
    # season), so instead of an enum-style lookup, colors are assigned by a
    # deterministic hash of the duty's own name into a fixed palette —
    # style.css defines chip-h0..chip-hN / dot-h0..dot-hN for exactly this.
    HASH_BUCKETS = 5

    def _hash_bucket(value: str) -> int:
        return zlib.crc32((value or "").strip().lower().encode("utf-8")) % HASH_BUCKETS

    @app.template_filter("chip_class")
    def chip_class(name: str) -> str:
        return f"chip chip-h{_hash_bucket(name)}"

    @app.template_filter("dot_class")
    def dot_class(name: str) -> str:
        return f"dot dot-h{_hash_bucket(name)}"

    # Monday-first week-of-days grid for a given year/month, used to render
    # Hela laget's calendar without doing date arithmetic in Jinja.
    app.jinja_env.globals["month_weeks"] = calendar.Calendar(firstweekday=0).monthdayscalendar
    app.jinja_env.globals["MONTHS_SV"] = MONTHS_SV

    # Swedish weekday/month names, hardcoded rather than locale-derived
    # (see dates.py) — strftime("%A") would silently render in English on
    # any host without an sv_SE locale installed, including the deployed
    # container.
    app.jinja_env.filters["weekday_sv"] = weekday_sv
    app.jinja_env.filters["short_date_sv"] = short_date_sv
    app.jinja_env.filters["long_date_sv"] = long_date_sv

    @app.context_processor
    def pending_swaps_badge():
        """Count on the Byten tab — otherwise a waiting request is invisible
        unless you happen to open that tab."""
        if not current_user.is_authenticated:
            return {}
        session = get_session()
        return {
            "pending_swap_count": len(
                swaps_pending_for_parent(session, int(current_user.id))
            )
        }

    # Every route acquires its (request-scoped, flask.g-cached) session via
    # get_session() instead of calling the session factory directly; this
    # closes it once the response is built, returning its connection to the
    # pool rather than relying on GC/refcounting (see session_scope.py).
    app.teardown_appcontext(close_session)

    from .routes.auth_routes import auth_bp
    from .routes.document_routes import document_bp
    from .routes.schedule_routes import schedule_bp
    from .routes.settings_routes import settings_bp
    from .routes.swap_routes import swap_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(document_bp)
    app.register_blueprint(schedule_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(swap_bp)

    # The login/verify flow is unauthenticated by nature (that's the point
    # of a magic link) and self-limits via the login-token rate limit and
    # the emailed link itself, so it's exempt from CSRF rather than needing
    # a session-bound token before a session exists.
    csrf.exempt(auth_bp)

    return app
