web_env := "SECRET_KEY=dev DB_PATH=:memory: SCHEDULE_API_KEY=dev SMTP_HOST=localhost SMTP_PORT=587 SMTP_USER=u SMTP_PASSWORD=p FROM_ADDRESS=noreply@exempel.se"

# List all recipes
default:
    just --list --unsorted

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

# Build a seed file from the trainer's schedule + the parent contact list
schedule-build source contacts out:
    uv run python scripts/build_seed_xlsx.py {{source}} {{contacts}} {{out}}

# Create a team in the deployed app (once per team). The slug names it in every other recipe
team-add slug name venue="Wallenstam arena":
    printf 'put scripts/remote_team_add.py /tmp/remote_team_add.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/remote_team_add.py {{slug}} '{{name}}' '{{venue}}'"

# Sync a team's players and parents from 360Player (Truppen → Exportera): Granska, then Genomför
roster-push team file:
    python3 -I scripts/admin_push.py roster.import '{{team}}' "{{file}}"

# Import the coach's Excel into a team's shifts: Granska, then Genomför. Questions are answered in the web
schedule-push team file:
    python3 -I scripts/admin_push.py schedule.import '{{team}}' "{{file}}"

# Rename duties and fill in venue/café on the slots already imported (idempotent)
duties-push:
    printf 'put scripts/rename_duty_profiles.py /tmp/rename_duty_profiles.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/rename_duty_profiles.py"

# Download the deployed database to rehearse a migration against a copy
db-pull out="/tmp/duty-copy.db":
    printf 'get /data/duty.db %s\n' "{{out}}" | fly ssh sftp shell -a duty-swap-webapp

# Upload a document: no team = shared by the club; a team slug = that team only (overrides a shared file of the same name)
docs-push file team="":
    fly ssh console -a duty-swap-webapp -C "mkdir -p /data/dokument/{{team}}"
    printf 'put %s /data/dokument/{{ if team == "" { "" } else { team + "/" } }}%s\n' "{{file}}" "$(basename {{file}})" | fly ssh sftp shell -a duty-swap-webapp

# List the documents on the deployed app's volume, shared and per team
docs-list:
    fly ssh console -a duty-swap-webapp -C "ls -laR /data/dokument"

# Add a throwaway test parent + pass to the deployed app (for trying swaps)
test-fixture-add email team="f15":
    printf 'put scripts/test_swap_fixture.py /tmp/test_swap_fixture.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/test_swap_fixture.py add {{email}} {{team}}"

# Remove the throwaway test parent, pass and any swap requests they are in
test-fixture-remove email team="f15":
    printf 'put scripts/test_swap_fixture.py /tmp/test_swap_fixture.py\n' | fly ssh sftp shell -a duty-swap-webapp
    fly ssh console -a duty-swap-webapp -C "python /tmp/test_swap_fixture.py remove {{email}} {{team}}"

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
