web_env := "SECRET_KEY=dev DB_PATH=:memory: SCHEDULE_API_KEY=dev SMTP_HOST=localhost SMTP_PORT=587 SMTP_USER=u SMTP_PASSWORD=p FROM_ADDRESS=noreply@exempel.se"

# List all recipes
default:
    just --list

# --- duty_mailer (root package) ---

# Create .venv (if missing) and install duty_mailer in editable mode with dev deps
install:
    uv venv .venv && uv pip install -e ".[dev]"

# Run duty_mailer's test suite
test:
    uv run pytest

# Send today's reminders using config.yaml (real emails)
mail-run:
    uv run python -m duty_mailer --config config.yaml

# Dry-run duty_mailer against config.yaml (prints, sends nothing)
mail-dry-run:
    uv run python -m duty_mailer --config config.yaml --dry-run

# --- web (duty-web app) ---

# Create web/.venv (if missing) and install duty-web in editable mode with dev deps
web-install:
    uv venv web/.venv && cd web && uv pip install -e ".[dev]"

# Run duty-web's test suite (from web/ so pytest only collects web/tests)
web-test:
    cd web && uv run pytest

# Start the public duty-web app locally (http://localhost:8080), in-memory db
web-run:
    cd web && {{web_env}} uv run python -m duty_web

# Start the localhost-only admin import app locally (http://localhost:8081)
web-admin:
    cd web && {{web_env}} uv run python -m duty_web.admin_main

# Build a seed file from the trainer's schedule + the parent contact list
schedule-build source contacts="data/f15_parent_mailing_list.csv" out="data/seed_sasong_26_27.xlsx":
    uv run python scripts/build_seed_xlsx.py {{source}} {{contacts}} {{out}}

# Sync players and parents into a running local admin app from the contact list
web-roster file="data/f15_parent_mailing_list.csv" team_id="1":
    curl -s -F "team_id={{team_id}}" -F "file=@{{file}}" http://localhost:8081/admin/roster

# Seed a running local admin app from a built seed file (team_id defaults to 1)
web-seed file="data/seed_sasong_26_27.xlsx" team_id="1":
    curl -s -F "team_id={{team_id}}" -F "file=@{{file}}" http://localhost:8081/admin/import

# Sync the deployed app's players and parents from the 360Player contact list
# (additive — run this whenever a new parent registers, and before schedule-push)
roster-push file="data/f15_parent_mailing_list.csv":
    printf 'put %s /tmp/roster.csv\nput scripts/remote_roster_import.py /tmp/remote_roster_import.py\n' "{{file}}" | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/remote_roster_import.py"

# Import a built seed file into the deployed app (replaces every slot)
schedule-push file="data/seed_sasong_26_27.xlsx":
    printf 'put %s /tmp/seed.xlsx\nput scripts/remote_import.py /tmp/remote_import.py\n' "{{file}}" | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/remote_import.py"

# One-off: move slot ownership from a single parent to the player (idempotent)
migrate-push file="data/f15_parent_mailing_list.csv":
    printf 'put %s /tmp/roster.csv\nput scripts/migrate_player_ownership.py /tmp/migrate_player_ownership.py\n' "{{file}}" | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/migrate_player_ownership.py"

# Download the deployed database to rehearse a migration against a copy
db-pull out="/tmp/duty-copy.db":
    printf 'get /data/duty.db %s\n' "{{out}}" | fly ssh sftp shell -a duty-swap-webapp

# Upload a document (pdf/png/jpg/txt/md) to the deployed app's Dokument page
docs-push file:
    fly ssh console -a duty-swap-webapp -C "mkdir -p /data/dokument"
    printf 'put %s /data/dokument/%s\n' "{{file}}" "$(basename {{file}})" | fly ssh sftp shell -a duty-swap-webapp

# List the documents currently on the deployed app's volume
docs-list:
    fly ssh console -a duty-swap-webapp -C "ls -la /data/dokument"

# Add a throwaway test parent + pass to the deployed app (for trying swaps)
test-fixture-add email:
    printf 'put scripts/test_swap_fixture.py /tmp/test_swap_fixture.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/test_swap_fixture.py add {{email}}"

# Remove the throwaway test parent, pass and any swap requests they are in
test-fixture-remove email:
    printf 'put scripts/test_swap_fixture.py /tmp/test_swap_fixture.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/test_swap_fixture.py remove {{email}}"

# Deploy the web app to Fly.io
web-deploy:
    cd web && fly deploy

# Tail logs from the deployed web app
web-logs:
    fly logs -a duty-swap-webapp

# Open an SSH console on the deployed web app's machine
web-ssh:
    fly ssh console -a duty-swap-webapp

# Run both test suites
test-all: test web-test
