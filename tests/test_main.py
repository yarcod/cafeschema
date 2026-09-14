from __future__ import annotations

from datetime import date

import pytest

from duty_mailer.__main__ import main, run
from duty_mailer.config import Config, EmailConfig, ScheduleConfig
from duty_mailer.email_sender import SendError
from duty_mailer.models import Occurrence, Person

CFG = Config(
    schedule=ScheduleConfig(
        source="xlsx",
        chore="matchvärd",
        path="schema.xlsx",
        default_lead_days=(7, 1),
        timezone="Europe/Stockholm",
        schedule_link="https://example.com/schema",
        max_handoff_gap_days=8,
    ),
    email=EmailConfig(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="robot@example.com",
        from_address="robot@example.com",
    ),
)

LAST_WEEK = Occurrence(date(2026, 9, 12), (Person("a@x.se", "Anna"),), (7, 1))
THIS_WEEK = Occurrence(date(2026, 9, 19), (Person("b@x.se", "Björn"),), (7, 1))


class FakeSource:
    def __init__(self, occurrences):
        self._occurrences = occurrences

    def fetch(self):
        return list(self._occurrences)


@pytest.fixture
def sent(monkeypatch):
    box = []
    monkeypatch.setattr(
        "duty_mailer.__main__.send",
        lambda msg, cfg, password: box.append(msg),
    )
    return box


def test_sends_nothing_when_nothing_is_due(sent):
    code = run(
        CFG,
        today=date(2026, 9, 15),
        dry_run=False,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert code == 0
    assert sent == []


def test_sends_the_nudge_the_day_before(sent):
    code = run(
        CFG,
        today=date(2026, 9, 18),
        dry_run=False,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert code == 0
    assert [m.to for m in sent] == [("b@x.se",)]
    assert "i morgon" in sent[0].subject


def test_heads_up_includes_the_previous_group(sent):
    run(
        CFG,
        today=date(2026, 9, 12),
        dry_run=False,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert "Anna" in sent[0].body


def test_dry_run_sends_nothing(sent, capsys):
    code = run(
        CFG,
        today=date(2026, 9, 18),
        dry_run=True,
        password="x",
        source=FakeSource([LAST_WEEK, THIS_WEEK]),
    )
    assert code == 0
    assert sent == []
    assert "b@x.se" in capsys.readouterr().out


def test_a_failed_send_does_not_stop_the_others(monkeypatch, capsys):
    attempted = []

    def flaky(msg, cfg, password):
        attempted.append(msg.to)
        if msg.to == ("a@x.se",):
            raise SendError("nope")

    monkeypatch.setattr("duty_mailer.__main__.send", flaky)
    both = [
        Occurrence(date(2026, 9, 19), (Person("a@x.se"),), (1,)),
        Occurrence(date(2026, 9, 19), (Person("b@x.se"),), (1,)),
    ]
    # Two occurrences on the same date cannot come from one roster, so build
    # the source directly.
    code = run(
        CFG, today=date(2026, 9, 18), dry_run=False, password="x",
        source=FakeSource(both),
    )
    assert code == 1
    assert attempted == [("a@x.se",), ("b@x.se",)]


def test_main_reports_a_bad_config_without_a_traceback(tmp_path, capsys):
    code = main(["--config", str(tmp_path / "nope.yaml")])
    assert code == 1
    assert "hittades inte" in capsys.readouterr().err


def test_main_rejects_a_malformed_date(tmp_path):
    with pytest.raises(SystemExit):
        main(["--config", str(tmp_path / "nope.yaml"), "--date", "igår"])
