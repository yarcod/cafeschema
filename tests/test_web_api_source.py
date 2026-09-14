from datetime import date

import responses

from duty_mailer.sources.rows import RosterError
from duty_mailer.sources.web_api import WebAppApiSource


@responses.activate
def test_fetch_builds_one_occurrence_per_date():
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "Cafe", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": "Hämta nyckel",
                "person": {"name": "Alva Exempel", "email": "tova@exempel.se"},
            },
            {
                "date": "2026-01-16", "start_time": "17:30", "end_time": "20:30",
                "station": "Entré", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "person": {"name": "Erik L", "email": "erik@exempel.se"},
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert len(occurrences) == 1
    occ = occurrences[0]
    assert occ.due == date(2026, 1, 16)
    assert {p.email for p in occ.people} == {"tova@exempel.se", "erik@exempel.se"}


@responses.activate
def test_fetch_dedupes_a_person_holding_two_stations_on_the_same_date():
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "Cafe", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "person": {"name": "Alva Exempel", "email": "tova@exempel.se"},
            },
            {
                "date": "2026-01-16", "start_time": "17:30", "end_time": "20:30",
                "station": "Entré", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "person": {"name": "Alva Exempel", "email": "tova@exempel.se"},
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert len(occurrences) == 1
    occ = occurrences[0]
    assert [p.email for p in occ.people] == ["tova@exempel.se"]


@responses.activate
def test_fetch_threads_the_note_field_through_as_key_location():
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "Cafe", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": "Hämta nyckel helgen innan",
                "person": {"name": "Alva Exempel", "email": "tova@exempel.se"},
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert occurrences[0].key_location == "Hämta nyckel helgen innan"


@responses.activate
def test_fetch_raises_roster_error_on_http_failure():
    responses.get("https://web.example/api/schedule", status=500)
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    try:
        source.fetch()
        assert False, "expected RosterError"
    except RosterError:
        pass


@responses.activate
def test_fetch_raises_roster_error_on_unauthorized():
    responses.get("https://web.example/api/schedule", status=401)
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="wrong", default_lead_days=(1,)
    )

    try:
        source.fetch()
        assert False, "expected RosterError"
    except RosterError:
        pass


@responses.activate
def test_fetch_raises_roster_error_on_malformed_response():
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16",
                "start_time": "18:00",
                "end_time": "21:00",
                # Missing "person" key
                "station": "Cafe",
                "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena",
                "note": "Hämta nyckel",
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    try:
        source.fetch()
        assert False, "expected RosterError"
    except RosterError:
        pass


@responses.activate
def test_fetch_raises_roster_error_on_bad_date_string():
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "not-a-date",
                "start_time": "18:00",
                "end_time": "21:00",
                "station": "Cafe",
                "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena",
                "note": "Hämta nyckel",
                "person": {"name": "Erik L", "email": "erik@exempel.se"},
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    try:
        source.fetch()
        assert False, "expected RosterError"
    except RosterError:
        pass


@responses.activate
def test_fetch_skips_people_who_turned_off_email():
    """The web app's per-parent opt-out must stop reminders too, not just
    the app's own swap notifications."""
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "person": {"name": "Med Mail", "email": "ja@exempel.se",
                           "email_notifications": True},
            },
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "person": {"name": "Utan Mail", "email": "nej@exempel.se",
                           "email_notifications": False},
            },
        ],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert [p.email for p in occurrences[0].people] == ["ja@exempel.se"]


@responses.activate
def test_fetch_reminds_every_parent_of_the_player():
    """A duty belongs to the player and either parent may turn up for it, so
    both are reminded — the single-parent shape below was the bug that left
    half the team never hearing about their own duties."""
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "Cafe", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "child_name": "Klara Ahlqvist",
                "player": {"name": "Klara Ahlqvist"},
                "parents": [
                    {"name": "Hans Ahlqvist", "email": "hans@exempel.se",
                     "email_notifications": True},
                    {"name": "Lena Ahlqvist", "email": "lena@exempel.se",
                     "email_notifications": True},
                ],
            },
        ],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert [p.email for p in occurrences[0].people] == [
        "hans@exempel.se", "lena@exempel.se",
    ]


@responses.activate
def test_fetch_skips_only_the_parent_who_turned_off_email():
    """The opt-out is per parent; one of them opting out must not silence
    the other."""
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
                "station": "Cafe", "duty_name": "Arena värdskap",
                "venue": "Wallenstam arena", "note": None,
                "player": {"name": "Klara Ahlqvist"},
                "parents": [
                    {"name": "Hans Ahlqvist", "email": "hans@exempel.se",
                     "email_notifications": False},
                    {"name": "Lena Ahlqvist", "email": "lena@exempel.se",
                     "email_notifications": True},
                ],
            },
        ],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert [p.email for p in occurrences[0].people] == ["lena@exempel.se"]


@responses.activate
def test_fetch_dedupes_a_family_holding_two_stations_on_the_same_date():
    entry = {
        "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
        "station": "Cafe", "duty_name": "Arena värdskap",
        "venue": "Wallenstam arena", "note": None,
        "player": {"name": "Klara Ahlqvist"},
        "parents": [
            {"name": "Hans Ahlqvist", "email": "hans@exempel.se"},
            {"name": "Lena Ahlqvist", "email": "lena@exempel.se"},
        ],
    }
    responses.get(
        "https://web.example/api/schedule",
        json=[entry, {**entry, "station": "Entré"}],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert [p.email for p in occurrences[0].people] == [
        "hans@exempel.se", "lena@exempel.se",
    ]
