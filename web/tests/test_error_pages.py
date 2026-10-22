"""Errors read the same for both doors: JSON for the API, short Swedish pages for people."""

import io

from duty_web.admin import ratelimit
from duty_web.admin.ratelimit import RateLimiter

from .conftest import bearer, login


def test_the_api_answers_json_for_unknown_paths_and_methods(client, api_keys):
    missing = client.get("/admin/api/finns/inte", headers=bearer(api_keys["ansvarig"]))
    wrong = client.post("/admin/api", headers=bearer(api_keys["ansvarig"]))

    assert (missing.status_code, missing.get_json()) == (404, {"error": "not_found"})
    assert (wrong.status_code, wrong.get_json()) == (405, {"error": "method_not_allowed"})


def test_the_api_answers_json_for_a_body_that_is_too_large(client, api_keys):
    response = client.post(
        "/admin/api/roster.import", data=b"x" * 2_200_000,
        headers={**bearer(api_keys["ansvarig"]), "Content-Type": "application/json"},
    )

    assert (response.status_code, response.get_json()) == (413, {"error": "too_large"})


def test_the_api_keeps_its_own_rate_limit_body(client, echo, api_keys, monkeypatch):
    monkeypatch.setattr(ratelimit, "api_limiter", RateLimiter(0, 60))

    response = client.get("/admin/api", headers=bearer(api_keys["ansvarig"]))

    assert (response.status_code, response.get_json()) == (429, {"error": "rate_limited"})


def test_an_unknown_page_is_a_short_swedish_page(client, admins):
    login(client, admins["ansvarig"])

    response = client.get("/admin/finns-inte/lag")
    body = response.get_data(as_text=True)

    assert response.status_code == 404 and "Sidan finns inte." in body
    assert "<html" in body and "Not Found" not in body


def test_a_file_that_is_too_large_is_a_swedish_page(client, admins, csrf_token):
    login(client, admins["ansvarig"])

    response = client.post(
        "/admin/f14-bla/do/roster.import",
        data={"csrf_token": csrf_token(), "file": (io.BytesIO(b"x" * 2_200_000), "enorm.csv")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 413
    assert "Filen är för stor (högst 1 MB)." in response.get_data(as_text=True)


def test_too_many_attempts_is_a_swedish_page(client, admins, echo, csrf_token, monkeypatch):
    monkeypatch.setattr(ratelimit, "admin_limiter", RateLimiter(0, 60))
    login(client, admins["ansvarig"])

    response = client.post(
        "/admin/f14-bla/do/tests.echo", data={"csrf_token": csrf_token(), "text": "x"}
    )

    assert response.status_code == 429
    assert "För många försök. Vänta en minut och försök igen." in response.get_data(as_text=True)
