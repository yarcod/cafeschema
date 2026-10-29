#!/bin/sh
set -e

# Bring the database up to the current schema before the app opens it.
# Idempotent; see duty_web/migrate.py.
python -m duty_web.migrate

exec python -m duty_web
