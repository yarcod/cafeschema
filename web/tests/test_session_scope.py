"""I7: the request-scoped session must actually get closed once the
request context is torn down, rather than being left open for GC to
eventually collect (a plausible contributor to "database is locked" under
concurrent public+admin process access on the file-backed engine)."""

from duty_web.session_scope import get_session


def test_session_is_closed_when_the_request_context_is_popped(app):
    captured = {}

    with app.test_request_context("/"):
        session = get_session()
        original_close = session.close

        def spy_close():
            captured["closed"] = True
            original_close()

        session.close = spy_close

    # The `with` block above pops the request context on exit, which runs
    # every teardown_appcontext handler — including close_session.
    assert captured.get("closed") is True


def test_get_session_returns_the_same_session_within_one_request(app):
    with app.test_request_context("/"):
        first = get_session()
        second = get_session()

        assert first is second


def test_get_session_returns_a_fresh_session_for_a_new_request(app):
    with app.test_request_context("/"):
        first = get_session()

    with app.test_request_context("/"):
        second = get_session()

    assert first is not second
