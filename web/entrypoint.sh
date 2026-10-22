#!/bin/sh
set -e

# Bring the database up to the current schema before either process opens
# it. Idempotent; see duty_web/migrate.py.
python -m duty_web.migrate

# Admin import app: localhost only, never exposed via fly.toml's
# [http_service] (which only forwards the public port below).
python -m duty_web.admin_main &

exec python -m duty_web
