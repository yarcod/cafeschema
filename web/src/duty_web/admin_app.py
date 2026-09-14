"""The localhost-only import app. Never bound to a public interface — see
entrypoint.sh, which starts this on 127.0.0.1 and the public app on 0.0.0.0.
"""

from __future__ import annotations

import tempfile
from dataclasses import asdict
from pathlib import Path

from flask import Flask, jsonify, request

from .config import AppConfig
from .db import init_db, make_engine, make_session_factory
from .roster import RosterError, import_roster
from .seed import SeedError, import_schedule


def create_admin_app(config: AppConfig) -> Flask:
    app = Flask(__name__)
    engine = make_engine(config.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    app.extensions["duty_web_session_factory"] = session_factory

    @app.route("/admin/import", methods=["POST"])
    def admin_import():
        uploaded = request.files.get("file")
        team_id = request.form.get("team_id", type=int)
        if uploaded is None or team_id is None:
            return jsonify({"error": "fil och team_id krävs"}), 400

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "schema.xlsx"
            uploaded.save(path)
            session = session_factory()
            try:
                try:
                    count = import_schedule(session, path, team_id=team_id)
                except SeedError as exc:
                    return jsonify({"error": str(exc)}), 400
            finally:
                session.close()

        return jsonify({"imported": count}), 200

    @app.route("/admin/roster", methods=["POST"])
    def admin_roster():
        """Reconcile players and parents from a 360Player contact export.

        Additive: safe to re-run whenever a new parent registers, without
        rebuilding anything or disturbing live sessions and swaps.
        """
        uploaded = request.files.get("file")
        team_id = request.form.get("team_id", type=int)
        if uploaded is None or team_id is None:
            return jsonify({"error": "fil och team_id krävs"}), 400

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "roster.csv"
            uploaded.save(path)
            session = session_factory()
            try:
                try:
                    result = import_roster(session, path, team_id=team_id)
                except RosterError as exc:
                    return jsonify({"error": str(exc)}), 400
            finally:
                session.close()

        return jsonify(asdict(result)), 200

    return app
