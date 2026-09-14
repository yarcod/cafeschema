from __future__ import annotations

import textwrap

import pytest

from duty_mailer.config import ConfigError, load_config

FULL = """
schedule:
  source: xlsx
  path: schema.xlsx
  default_lead_days: [7, 1]
  timezone: Europe/Stockholm
  chore: matchvärd
  schedule_link: https://example.com/schema
  max_handoff_gap_days: 8

email:
  smtp_host: smtp.fastmail.com
  smtp_port: 587
  smtp_user: robot@example.com
  from_address: robot@example.com
  reply_to: styrelsen@example.com
"""

MINIMAL = """
schedule:
  source: xlsx
  path: schema.xlsx
  chore: matchvärd

email:
  smtp_host: smtp.fastmail.com
  smtp_port: 587
  smtp_user: robot@example.com
  from_address: robot@example.com
"""


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


def test_loads_a_full_config(tmp_path):
    cfg = load_config(write(tmp_path, FULL))
    assert cfg.schedule.source == "xlsx"
    assert cfg.schedule.default_lead_days == (7, 1)
    assert cfg.schedule.chore == "matchvärd"
    assert cfg.email.smtp_port == 587
    assert cfg.email.reply_to == "styrelsen@example.com"


def test_applies_defaults_for_optional_keys(tmp_path):
    cfg = load_config(write(tmp_path, MINIMAL))
    assert cfg.schedule.default_lead_days == (1,)
    assert cfg.schedule.timezone == "Europe/Stockholm"
    assert cfg.schedule.max_handoff_gap_days == 8
    assert cfg.schedule.schedule_link is None
    assert cfg.email.reply_to is None


def test_rejects_an_unknown_source(tmp_path):
    bad = FULL.replace("source: xlsx", "source: carrier_pigeon")
    with pytest.raises(ConfigError, match="carrier_pigeon"):
        load_config(write(tmp_path, bad))


def test_rejects_xlsx_source_without_a_path(tmp_path):
    bad = MINIMAL.replace("  path: schema.xlsx\n", "")
    with pytest.raises(ConfigError, match="path"):
        load_config(write(tmp_path, bad))


def test_rejects_google_csv_source_without_a_url(tmp_path):
    bad = MINIMAL.replace("source: xlsx", "source: google_csv")
    with pytest.raises(ConfigError, match="url"):
        load_config(write(tmp_path, bad))


def test_rejects_more_than_two_default_lead_days(tmp_path):
    bad = FULL.replace("default_lead_days: [7, 1]", "default_lead_days: [14, 7, 1]")
    with pytest.raises(ConfigError, match="högst 2"):
        load_config(write(tmp_path, bad))


def test_rejects_a_missing_required_key(tmp_path):
    bad = FULL.replace("  chore: matchvärd\n", "")
    with pytest.raises(ConfigError, match="chore"):
        load_config(write(tmp_path, bad))


def test_rejects_a_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="hittades inte"):
        load_config(tmp_path / "nope.yaml")
