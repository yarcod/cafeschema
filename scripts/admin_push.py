#!/usr/bin/env python3
"""Run an admin action against Caféschema's API: Granska, then Genomför.

Used by `just roster-push` and `just schedule-push`:

    python3 -I scripts/admin_push.py roster.import f15 "F15-members.csv"
    python3 -I scripts/admin_push.py schedule.import f15 Bemanningsschema.xlsx

Stdlib only: it imports nothing from the repo and runs with any python3.
The key comes from 1Password when it runs (`op read`, reference in
ADMIN_KEY_REF), stays in memory and is sent only as the Authorization
header: never printed, never written to disk, never on a command line. The
plan token is treated the same way. Questions (Vem är 'X'?) are answered in
the web, never here: an import with an open question is not applied.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE_URL = os.environ.get("ADMIN_BASE_URL", "https://innebandy.edholm.cc")
KEY_REF = os.environ.get("ADMIN_KEY_REF", "op://Private/pjss2h7w7ec7amvsfbni4cdcvu/credential")
USER_AGENT = "cafeschema-admin-push/1"
SUFFIX = {"roster.import": ".csv", "schedule.import": ".xlsx"}
SYMBOL = {"add": "+", "update": "~", "delete": "−"}


class PushError(Exception):
    pass


def read_key(ref: str = KEY_REF) -> str:
    try:
        done = subprocess.run(["op", "read", ref], capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise PushError("1Password CLI (op) saknas.") from None
    except subprocess.CalledProcessError:
        # Whatever op printed stays unread: it could be the secret itself.
        raise PushError(f"Kunde inte läsa {ref} ur 1Password. Är du inloggad (op signin)?") from None
    key = done.stdout.strip()
    if not key:
        raise PushError(f"{ref} är tom i 1Password.")
    return key


def multipart(fields: dict[str, str], file_field: str, path: Path) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    filename = path.name.replace('"', "").replace("\r", "").replace("\n", "")
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        for name, value in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n".encode()
        + path.read_bytes() + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect would re-send the Authorization header to wherever Location
    points. Refusing it turns the 3xx into an HTTPError instead."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)
LOCAL_HOSTS = ("localhost", "127.0.0.1")


def check_base_url(base_url: str) -> str:
    """The key goes in a header: only over https, or to this machine."""
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS):
        return base_url.rstrip("/")
    raise PushError("ADMIN_BASE_URL måste vara https (eller localhost): nyckeln skickas inte okrypterad.")


def http_send(method: str, url: str, headers: dict[str, str], body: bytes) -> tuple[int, dict]:
    # Cloudflare in front of the app refuses urllib's default User-Agent (error 1010).
    headers = {"User-Agent": USER_AGENT, **headers}
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with OPENER.open(request, timeout=60) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except ValueError:
            return exc.code, {}
    except urllib.error.URLError as exc:
        raise PushError(f"Nådde inte {url}: {exc.reason}") from None


def _check(status: int, payload: dict) -> None:
    if status == 200:
        return
    if status == 400 and isinstance(payload.get("errors"), dict):
        raise PushError("\n".join(f"{field}: {message}" for field, message in payload["errors"].items()))
    if status == 409:
        raise PushError("Något ändrades mellan granskning och genomförande. Inget ändrades; kör igen.")
    if status == 403 and not payload:
        raise PushError("Servern eller Cloudflare nekade anropet (403). Inget ändrades.")
    if status in (401, 403, 404):
        raise PushError("Nyckeln nekades, eller så har den inget lag med det namnet.")
    if status == 429:
        raise PushError("För många anrop. Vänta en minut och kör igen.")
    raise PushError(f"Oväntat svar från servern ({status}).")


def render(plan: dict) -> list[str]:
    """Granska as text: + nytt, ~ ändrat, − borttaget, then warnings, notes, questions."""
    lines = []
    for change in plan.get("changes", []):
        detail = f"  ({change['detail']})" if change.get("detail") else ""
        lines.append(f"{SYMBOL.get(change['op'], '?')} {change['summary']}{detail}")
    lines += [f"! {warning}" for warning in plan.get("warnings", [])]
    lines += [f"· {note}" for note in plan.get("notes", [])]
    lines += [f"? {q['label']}" for q in plan.get("questions", []) if q.get("selected") is None]
    return lines


def ask(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("j", "ja", "y", "yes")
    except EOFError:
        return False


def push(action, team, path, *, key, send=http_send, confirm=ask, out=print, base_url=BASE_URL) -> int:
    if action not in SUFFIX:
        raise PushError(f"Okänd åtgärd {action}: välj {' eller '.join(SUFFIX)}.")
    path = Path(path)
    if path.suffix.lower() != SUFFIX[action]:
        raise PushError(f"{action} läser en {SUFFIX[action]}-fil, inte {path.name}.")
    if not path.is_file():
        raise PushError(f"Filen {path} finns inte.")
    url = f"{base_url}/admin/api/{action}"
    auth = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}

    body, content_type = multipart({"team": team, "dry_run": "true"}, "file", path)
    status, plan = send("POST", url, {**auth, "Content-Type": content_type}, body)
    _check(status, plan)
    token = plan.pop("plan_token")
    for line in render(plan):
        out(line)
    if not plan.get("changes"):
        out("Inget att ändra.")
        return 0
    if any(q.get("selected") is None for q in plan.get("questions", [])):
        out("Svara på frågorna i webben under ✎ och genomför där. Inget ändrades.")
        return 2
    if not confirm("Genomför? [j/N] "):
        out("Avbrutet. Inget ändrades.")
        return 1

    payload = json.dumps({"team": team, "dry_run": False, "plan_token": token}).encode()
    status, done = send("POST", url, {**auth, "Content-Type": "application/json"}, payload)
    _check(status, done)
    counts = done.get("counts", {})
    out(f"Genomfört: {counts.get('add', 0)} nya, {counts.get('update', 0)} ändrade, "
        f"{counts.get('delete', 0)} borttagna.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3:
        print(__doc__, file=sys.stderr)
        return 64
    try:
        base_url = check_base_url(BASE_URL)
        return push(*args, key=read_key(), base_url=base_url)
    except PushError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
