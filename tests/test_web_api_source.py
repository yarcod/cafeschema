from datetime import date, time

import responses

from duty_mailer.sources.rows import RosterError
from duty_mailer.sources.web_api import WebAppApiSource


@responses.activate
def test_fetch_builds_one_occurrence_per_shift_not_per_date():
    """A day with three shifts is three separate mails.

    Keying on the date alone put everyone who had *any* shift that day into
    one occurrence, so the 09–12 pair were told they were on with the 12–15
    and 15–18 pairs. The recipient's question is "who am I doing this with",
    and the answer is the other people standing in the café at the same
    time — nobody else.
    """
    def entry(start, end, email):
        return {
            "date": "2026-09-19", "start_time": start, "end_time": end,
            "station": "Café Arena B, nedre plan", "duty_name": "Cafépass",
            "venue": "Wallenstam arena", "note": None,
            "parents": [{"name": email.split("@")[0], "email": email}],
        }

    responses.get(
        "https://web.example/api/schedule",
        json=[
            entry("09:00", "12:00", "tidig@exempel.se"),
            entry("12:00", "15:00", "mitten@exempel.se"),
            entry("15:00", "18:00", "sen@exempel.se"),
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert len(occurrences) == 3
    assert all(occ.due == date(2026, 9, 19) for occ in occurrences)
    assert [[p.email for p in occ.people] for occ in occurrences] == [
        ["tidig@exempel.se"], ["mitten@exempel.se"], ["sen@exempel.se"],
    ]


@responses.activate
def test_fetch_keeps_everyone_who_shares_one_shift_together():
    """The flip side: same shift, different families, one mail naming both."""
    def entry(email):
        return {
            "date": "2026-09-19", "start_time": "12:30", "end_time": "16:30",
            "station": "Café Arena A, övre plan", "duty_name": "Cafépass Arena A",
            "venue": "Wallenstam arena", "note": None,
            "parents": [{"name": email.split("@")[0], "email": email}],
        }

    responses.get(
        "https://web.example/api/schedule",
        json=[entry("sofie@exempel.se"), entry("mikaela@exempel.se")],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert len(occurrences) == 1
    assert [p.email for p in occurrences[0].people] == [
        "sofie@exempel.se", "mikaela@exempel.se",
    ]


@responses.activate
def test_fetch_carries_the_time_and_place_of_the_shift():
    """The mail has to say which shift it is about, now that a date can hold
    several — and which of the arena's two cafés to turn up at."""
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-09-19", "start_time": "12:30", "end_time": "16:30",
                "station": "Café Arena A, övre plan", "duty_name": "Cafépass Arena A",
                "venue": "Wallenstam arena", "note": None,
                "parents": [{"name": "Sofie", "email": "sofie@exempel.se"}],
            },
        ],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occ = source.fetch()[0]

    assert occ.start_time == time(12, 30)
    assert occ.end_time == time(16, 30)
    assert occ.venue == "Wallenstam arena"
    assert occ.station == "Café Arena A, övre plan"


@responses.activate
def test_fetch_occurrences_are_sorted_by_time_within_a_date():
    def entry(start, end):
        return {
            "date": "2026-09-19", "start_time": start, "end_time": end,
            "station": "", "duty_name": "Cafépass",
            "venue": "Wallenstam arena", "note": None,
            "parents": [{"name": start, "email": f"{start.replace(':', '')}@x.se"}],
        }

    responses.get(
        "https://web.example/api/schedule",
        json=[entry("15:00", "18:00"), entry("09:00", "12:00"), entry("12:00", "15:00")],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    assert [occ.start_time for occ in source.fetch()] == [
        time(9, 0), time(12, 0), time(15, 0),
    ]


@responses.activate
def test_fetch_dedupes_a_person_holding_two_stations_in_the_same_shift():
    """Two stations in one time window is still one shift — the people are
    in the same place at the same time, so they get one mail, named once."""
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
                "date": "2026-01-16", "start_time": "18:00", "end_time": "21:00",
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
    # The two stations disagree, so neither is claimed as the shift's place.
    assert occ.station is None


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
def test_fetch_dedupes_a_family_holding_two_stations_in_the_same_shift():
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


@responses.activate
def test_fetch_keeps_the_instruction_link_the_app_sends():
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-09-25", "start_time": "16:30", "end_time": "22:45",
                "station": "Café", "duty_name": "Bästkustcupen",
                "venue": "Mölnlycke idrottshall", "note": None,
                "document_url": "https://web.example/dokument/MIH.pdf",
                "parents": [{"name": "Alva", "email": "alva@exempel.se"}],
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occ = source.fetch()[0]

    assert occ.document_link == "https://web.example/dokument/MIH.pdf"


@responses.activate
def test_fetch_leaves_the_instruction_link_unset_when_the_app_has_none():
    """Older app versions don't send the field; the mail falls back to the
    document index from config.yaml."""
    responses.get(
        "https://web.example/api/schedule",
        json=[
            {
                "date": "2026-09-25", "start_time": "16:30", "end_time": "22:45",
                "station": "Café", "duty_name": "Bästkustcupen",
                "venue": "", "note": None,
                "parents": [{"name": "Alva", "email": "alva@exempel.se"}],
            },
        ],
        match=[responses.matchers.header_matcher({"X-Api-Key": "secret"})],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    assert source.fetch()[0].document_link is None


@responses.activate
def test_identical_shifts_of_two_teams_are_mailed_separately():
    """F15 and P13 can both staff the Cafépass at the same time and place.
    Without the team in the key they became one mail: both teams' parents in
    To:, told they share the shift, and one team got the other's instruction."""
    def entry(team, email):
        return {
            "date": "2026-09-19", "start_time": "12:30", "end_time": "16:30",
            "station": "Café Arena A", "duty_name": "Cafépass",
            "venue": "Wallenstam arena", "note": None, "team": team,
            "document_url": f"https://web.example/dokument/{team}/Cafe.pdf",
            "parents": [{"name": email.split("@")[0], "email": email}],
        }

    responses.get(
        "https://web.example/api/schedule",
        json=[entry("f15", "f15@exempel.se"), entry("p13", "p13@exempel.se")],
    )
    source = WebAppApiSource(
        "https://web.example/api/schedule", api_key="secret", default_lead_days=(1,)
    )

    occurrences = source.fetch()

    assert len(occurrences) == 2
    by_email = {occ.people[0].email: occ for occ in occurrences}
    assert all(len(occ.people) == 1 for occ in occurrences)
    assert by_email["f15@exempel.se"].document_link.endswith("/f15/Cafe.pdf")
    assert by_email["p13@exempel.se"].document_link.endswith("/p13/Cafe.pdf")
