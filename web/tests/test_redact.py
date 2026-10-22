"""The werkzeug access log must never carry a key's secret part."""

import logging

from duty_web.admin.redact import KeyRedactor

KEY = "cs_0123abcd_" + "Zq9-_" * 8


def _access_record():
    return logging.LogRecord(
        "werkzeug", logging.INFO, __file__, 1,
        '%s - - [x] "%s" %s -', ("127.0.0.1", f"GET /admin/api?api_key={KEY} HTTP/1.1", 400), None,
    )


def test_the_filter_keeps_only_the_prefix():
    record = _access_record()

    assert KeyRedactor().filter(record) is True
    message = record.getMessage()
    assert "cs_0123abcd_…" in message and KEY not in message and "Zq9" not in message
    assert record.args in (None, ())


def test_a_key_in_the_message_itself_is_redacted():
    record = logging.LogRecord("werkzeug", logging.INFO, __file__, 1, f"see {KEY}", None, None)

    KeyRedactor().filter(record)

    assert record.getMessage() == "see cs_0123abcd_…"


def test_the_filter_is_installed_once_however_often_the_app_is_built(app):
    # The app fixture builds a fresh app in every test.
    filters = [f for f in logging.getLogger("werkzeug").filters if isinstance(f, KeyRedactor)]

    assert len(filters) == 1
