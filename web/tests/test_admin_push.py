"""scripts/admin_push.py: the recipes' door to the admin API, end to end
against the test app. Loaded off disk like the migration scripts."""

import importlib.util
import http.server
import subprocess
import threading
import types
from pathlib import Path

import pytest

from duty_web.models import Player, Slot

from .test_action_roster_import import TEAM
from .test_action_schedule_import import SATURDAY
from .test_members_csv import export
from .test_schedule_xlsx import coach_workbook

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "admin_push.py"
BASE = "http://test"


@pytest.fixture(scope="module")
def admin_push():
    spec = importlib.util.spec_from_file_location("admin_push", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def send(client):
    """The CLI's transport, answered by the Flask test client."""
    calls = []

    def _send(method, url, headers, body):
        calls.append({"url": url, "headers": dict(headers), "body": body})
        response = client.open(
            url.removeprefix(BASE), method=method, data=body,
            headers={k: v for k, v in headers.items() if k != "Content-Type"},
            content_type=headers["Content-Type"],
        )
        return response.status_code, response.get_json(silent=True) or {}

    _send.calls = calls
    return _send


def _csv(tmp_path, rows=TEAM, name="F14-members.csv"):
    path = tmp_path / name
    path.write_bytes(export(*rows))
    return path


def _push(admin_push, send, key, *args, answer=True, lines=None, confirm=None):
    lines = [] if lines is None else lines
    return admin_push.push(
        *args, key=key, send=send, out=lines.append, base_url=BASE,
        confirm=confirm or (lambda prompt: answer),
    ), lines


def test_a_roster_is_reviewed_then_applied(admin_push, send, api_keys, tmp_path, session_factory):
    code, lines = _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla", _csv(tmp_path))

    assert code == 0
    assert "~ Står inte på pass: Moa Granne" in lines
    assert lines[-1] == "Genomfört: 0 nya, 1 ändrade, 0 borttagna."
    assert session_factory().query(Player).filter_by(name="Moa Granne").one().exempt is True


def test_saying_no_applies_nothing(admin_push, send, api_keys, tmp_path, session_factory):
    code, lines = _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla",
                        _csv(tmp_path), answer=False)

    assert code == 1 and lines[-1] == "Avbrutet. Inget ändrades."
    assert session_factory().query(Player).filter_by(name="Moa Granne").one().exempt is False


def test_nothing_to_change_asks_nothing(admin_push, send, api_keys, tmp_path):
    _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla", _csv(tmp_path))
    asked = []

    code, lines = _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla",
                        _csv(tmp_path), confirm=lambda prompt: asked.append(prompt) or True)

    assert code == 0 and lines[-1] == "Inget att ändra." and asked == []


def test_a_file_name_with_spaces_and_swedish_letters(admin_push, send, api_keys, tmp_path):
    path = _csv(tmp_path, name="F19_F20 - Flickor 2019_2020 åäö-members.csv")

    code, _ = _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla", path)

    assert code == 0


def test_an_unanswered_question_sends_you_to_the_web(
    admin_push, send, api_keys, tmp_path, session_factory
):
    path = tmp_path / "Bemanningsschema.xlsx"
    path.write_bytes(coach_workbook([[46, "Cafépass", SATURDAY, "11:30-16:00", "Okänd Spelare", None, None]]))
    before = session_factory().query(Slot).count()
    asked = []

    code, lines = _push(admin_push, send, api_keys["ansvarig"], "schedule.import", "f14-bla", path,
                        confirm=lambda prompt: asked.append(prompt) or True)

    assert code == 2 and asked == []
    assert any(line.startswith("? Vem är 'Okänd Spelare'?") for line in lines)
    assert lines[-1] == "Svara på frågorna i webben under ✎ och genomför där. Inget ändrades."
    assert session_factory().query(Slot).count() == before


def test_a_change_between_review_and_apply_applies_nothing(
    admin_push, send, api_keys, tmp_path, session_factory
):
    def meanwhile(prompt):
        session = session_factory()
        session.query(Player).filter_by(name="Moa Granne").one().exempt = True
        session.commit()
        return True

    with pytest.raises(admin_push.PushError, match="ändrades mellan"):
        _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla", _csv(tmp_path),
              confirm=meanwhile)


def test_the_key_is_only_ever_a_header(admin_push, send, api_keys, tmp_path):
    key = api_keys["ansvarig"]

    _, lines = _push(admin_push, send, key, "roster.import", "f14-bla", _csv(tmp_path))

    assert len(send.calls) == 2
    for call in send.calls:
        assert call["headers"]["Authorization"] == f"Bearer {key}"
        assert key.encode() not in call["body"] and key not in call["url"]
    assert not any(key in line or "plan_token" in line for line in lines)
    assert b"plan_token" in send.calls[1]["body"]   # the token travels in the body, only there


@pytest.mark.parametrize("args, message", [
    (("roster.import", "f14-bla", "schema.xlsx"), r"\.csv"),
    (("schedule.import", "f14-bla", "lista.csv"), r"\.xlsx"),
    (("slot.delete", "f14-bla", "x.csv"), "Okänd åtgärd"),
])
def test_a_wrong_file_or_action_stops_before_any_call(admin_push, send, tmp_path, args, message):
    action, team, name = args
    (tmp_path / name).write_bytes(b"x")

    with pytest.raises(admin_push.PushError, match=message):
        admin_push.push(action, team, tmp_path / name, key="k", send=send, base_url=BASE)
    assert send.calls == []


def test_a_bad_file_is_named_by_its_field(admin_push, send, api_keys, tmp_path):
    path = tmp_path / "trasig.csv"
    path.write_bytes(b"inte,en,export\n")

    with pytest.raises(admin_push.PushError, match="^file: "):
        _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla", path)


def test_a_refused_key_says_so(admin_push, send, tmp_path):
    with pytest.raises(admin_push.PushError, match="Nyckeln nekades"):
        _push(admin_push, send, "cs_fel", "roster.import", "f14-bla", _csv(tmp_path))


def test_read_key_never_puts_the_key_on_a_command_line(admin_push, monkeypatch):
    seen = []

    def run(argv, **kwargs):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="hemlig\n", stderr="")

    monkeypatch.setattr(admin_push.subprocess, "run", run)

    assert admin_push.read_key("op://Private/x/credential") == "hemlig"
    assert seen == [["op", "read", "op://Private/x/credential"]]


def test_read_key_failure_hides_what_op_printed(admin_push, monkeypatch):
    def run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, output="hemlig", stderr="hemlig")

    monkeypatch.setattr(admin_push.subprocess, "run", run)

    with pytest.raises(admin_push.PushError) as exc:
        admin_push.read_key("op://Private/x/credential")
    assert "hemlig" not in str(exc.value) and "op signin" in str(exc.value)


def test_every_call_carries_an_explicit_user_agent(admin_push, send, api_keys, tmp_path):
    _push(admin_push, send, api_keys["ansvarig"], "roster.import", "f14-bla", _csv(tmp_path))

    assert len(send.calls) == 2
    for call in send.calls:
        agent = call["headers"].get("User-Agent", "")
        assert agent and not agent.startswith("Python-urllib")


def test_http_send_sets_the_user_agent(admin_push, monkeypatch):
    seen = []

    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def read(self): return b"{}"

    def urlopen(request, timeout=None):
        seen.append(request)
        return Response()

    monkeypatch.setattr(admin_push, "OPENER", types.SimpleNamespace(open=urlopen))

    admin_push.http_send("POST", "http://test/x", {"Authorization": "Bearer k"}, b"")

    agent = seen[0].get_header("User-agent")
    assert agent and not agent.startswith("Python-urllib")
    assert seen[0].get_header("Authorization") == "Bearer k"


def _serve(handler):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_http_send_does_not_follow_a_redirect(admin_push):
    elsewhere = []

    class Elsewhere(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            elsewhere.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
        log_message = lambda *args: None

    target = _serve(Elsewhere)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/x")
            self.end_headers()
        log_message = lambda *args: None

    origin = _serve(Redirect)
    try:
        status, payload = admin_push.http_send(
            "POST", f"http://127.0.0.1:{origin.server_port}/x", {"Authorization": "Bearer k"}, b"")
    finally:
        origin.shutdown()
        target.shutdown()

    assert (status, payload) == (302, {})
    assert elsewhere == []
    with pytest.raises(admin_push.PushError, match=r"Oväntat svar från servern \(302\)"):
        admin_push._check(status, payload)


@pytest.mark.parametrize("url", ["http://innebandy.edholm.cc", "ftp://localhost", "http://localhost.evil.se"])
def test_a_base_url_that_is_not_https_or_local_is_refused(admin_push, url):
    with pytest.raises(admin_push.PushError, match="https"):
        admin_push.check_base_url(url)


@pytest.mark.parametrize("url", ["https://innebandy.edholm.cc/", "http://localhost:5000", "http://127.0.0.1:8000"])
def test_https_and_local_base_urls_are_accepted(admin_push, url):
    assert admin_push.check_base_url(url) == url.rstrip("/")


def test_a_403_without_a_json_body_blames_the_server_or_cloudflare(admin_push):
    with pytest.raises(admin_push.PushError, match=r"Servern eller Cloudflare nekade anropet \(403\)\. Inget ändrades\."):
        admin_push._check(403, {})
    with pytest.raises(admin_push.PushError, match="Nyckeln nekades"):
        admin_push._check(403, {"error": "nej"})
    with pytest.raises(admin_push.PushError, match="Nyckeln nekades"):
        admin_push._check(401, {})
