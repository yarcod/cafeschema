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


def test_every_generated_key_has_the_redacted_shape(session_factory, admins):
    # Guards against drift: if the key format changes, redaction must too.
    from duty_web.admin.api_keys import create_api_key
    from duty_web.admin.redact import KEY_PATTERN

    session = session_factory()
    for _ in range(50):
        row, plaintext = create_api_key(session, admins["ansvarig"].id)
        assert KEY_PATTERN.fullmatch(plaintext)
        assert redact_text(plaintext) == f"{row.prefix}_…"


def redact_text(text):
    from duty_web.admin.redact import redact

    return redact(text)


def test_only_the_exact_key_shape_matches():
    from duty_web.admin.redact import KEY_PATTERN

    not_keys = [
        "cs_0123abc_Zq9",      # seven hex digits
        "cs_0123ABCD_Zq9",     # hex is lowercase only
        "cs_0123abcd",         # no secret part
        "cs_0123abcd_",        # empty secret part
        "cs.0123abcd.Zq9",     # dots are not separators
        "cs-0123abcd-Zq9",
        "cs_0123abcg_Zq9",     # g is not hex
    ]
    for text in not_keys:
        assert KEY_PATTERN.search(text) is None, text


def test_nothing_of_the_secret_survives_next_to_url_punctuation():
    secret = KEY[len("cs_0123abcd_"):]
    for text in (f"?a=1&api_key={KEY}&b=2", f'"{KEY}"', f"x {KEY}.", f"/{KEY}/"):
        redacted = redact_text(text)
        assert secret not in redacted and "Zq9" not in redacted, text
        assert "cs_0123abcd_…" in redacted


def test_redaction_is_linear_on_hostile_input():
    import time

    hostile = ("cs_00000000_" * 20000) + "!" + ("cs_0000000" * 20000)
    start = time.perf_counter()
    redact_text(hostile)
    assert time.perf_counter() - start < 1.0


def test_a_record_that_cannot_be_formatted_is_still_redacted_not_raised():
    record = logging.LogRecord("werkzeug", logging.INFO, __file__, 1, f"%s %s {KEY}", ("one",), None)

    assert KeyRedactor().filter(record) is True
    assert "Zq9" not in record.getMessage() and record.args is None
