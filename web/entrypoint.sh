#!/bin/sh
set -e

# Admin import app: localhost only, never exposed via fly.toml's
# [http_service] (which only forwards the public port below).
python -m duty_web.admin_main &

exec python -m duty_web
