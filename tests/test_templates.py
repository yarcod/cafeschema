from __future__ import annotations

from datetime import date, time

from duty_mailer.models import Occurrence, Person, Role
from duty_mailer.templates import format_date_sv, render

ANNA = Person("anna@x.se", "Anna Svensson")
BJORN = Person("bjorn@x.se", "Björn Ek")
CARINA = Person("carina@x.se", "Carina Lund")

THIS_WEEK = Occurrence(due=date(2026, 9, 19), people=(BJORN, CARINA), lead_days=(7, 1))
LAST_WEEK = Occurrence(due=date(2026, 9, 12), people=(ANNA,))


def assert_no_addresses(msg, *addresses):
    """Subject, plain body and HTML body must not show anyone's address."""
    for address in addresses:
        assert address not in msg.subject
        assert address not in msg.body
        assert address not in msg.html_body


def test_format_date_sv():
    assert format_date_sv(date(2026, 9, 19)) == "lördag 19 september"


def test_format_date_sv_handles_every_month():
    months = [format_date_sv(date(2026, m, 1)).split()[-1] for m in range(1, 13)]
    assert months == [
        "januari", "februari", "mars", "april", "maj", "juni",
        "juli", "augusti", "september", "oktober", "november", "december",
    ]


def test_recipients_are_the_whole_group():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert msg.to == ("bjorn@x.se", "carina@x.se")


def test_nudge_subject_mentions_tomorrow():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert msg.subject == "Påminnelse — matchvärd i morgon"


def test_nudge_body_names_the_group():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "Björn Ek" in msg.body
    assert "Carina Lund" in msg.body
    assert "lördag 19 september" in msg.body


def test_heads_up_subject_includes_the_date():
    msg = render(
        THIS_WEEK, Role.FORHANDSBESKED, None, chore="matchvärd", schedule_link=None
    )
    assert msg.subject == "Er tur snart — matchvärd lördag 19 september"


def test_heads_up_names_the_previous_group_when_adjacent():
    msg = render(
        THIS_WEEK, Role.FORHANDSBESKED, LAST_WEEK, chore="matchvärd", schedule_link=None
    )
    assert "Anna Svensson" in msg.body
    assert_no_addresses(msg, "anna@x.se", "bjorn@x.se", "carina@x.se")


def test_heads_up_omits_the_handover_when_there_is_no_predecessor():
    msg = render(
        THIS_WEEK, Role.FORHANDSBESKED, None, chore="matchvärd", schedule_link=None
    )
    assert "nyckl" not in msg.body.lower()
    assert "förra" not in msg.body.lower()


def test_nudge_never_mentions_the_previous_group():
    # The handover belongs in the heads-up; by the day before it is noise.
    msg = render(
        THIS_WEEK, Role.PAMINNELSE, LAST_WEEK, chore="matchvärd", schedule_link=None
    )
    assert "Anna Svensson" not in msg.body


def test_schedule_link_is_included_when_configured():
    msg = render(
        THIS_WEEK,
        Role.PAMINNELSE,
        None,
        chore="matchvärd",
        schedule_link="https://example.com/schema",
    )
    assert "https://example.com/schema" in msg.body


def test_schedule_link_is_omitted_when_not_configured():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "Schema:" not in msg.body


def test_people_without_names_get_a_placeholder_not_their_address():
    occ = Occurrence(due=date(2026, 9, 19), people=(Person("x@x.se"),))
    msg = render(occ, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "(namn saknas)" in msg.body
    assert_no_addresses(msg, "x@x.se")


def test_an_address_stored_as_the_name_is_treated_as_nameless():
    """Production has parents whose name was filled in with their address."""
    occ = Occurrence(due=date(2026, 9, 19), people=(Person("x@x.se", "x@x.se"),))
    msg = render(occ, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "(namn saknas)" in msg.body
    assert_no_addresses(msg, "x@x.se")


def test_a_nameless_previous_person_is_not_revealed_by_address():
    previous = Occurrence(
        due=date(2026, 9, 12), people=(ANNA, Person("anonym@x.se")), key_location="Kansliet"
    )
    msg = render(
        THIS_WEEK, Role.FORHANDSBESKED, previous, chore="matchvärd", schedule_link=None
    )
    assert "Anna Svensson" in msg.body
    assert "(namn saknas)" in msg.body
    assert_no_addresses(msg, "anna@x.se", "anonym@x.se")


def test_body_has_no_leading_or_trailing_blank_lines():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert msg.body == msg.body.strip()


CUP = Occurrence(
    due=date(2026, 9, 25),
    people=(BJORN,),
    document_link="https://web.example/dokument/MIH.pdf",
)


def test_the_mail_links_to_the_instruction_for_this_duty():
    msg = render(
        CUP,
        Role.PAMINNELSE,
        None,
        chore="cafévärd",
        schedule_link=None,
        documents_link="https://web.example/dokument",
    )
    assert "https://web.example/dokument/MIH.pdf" in msg.body
    assert "https://web.example/dokument/MIH.pdf" in msg.html_body


# --- which shift, and where -----------------------------------------------
#
# A date can hold several shifts, so a mail that only says the date is
# ambiguous: two of them would arrive looking identical. And one arena holds
# two cafés, so "Wallenstam arena" alone doesn't say where to stand.

ARENA_A = Occurrence(
    due=date(2026, 9, 19),
    people=(BJORN, CARINA),
    lead_days=(7, 1),
    start_time=time(12, 30),
    end_time=time(16, 30),
    venue="Wallenstam arena",
    station="Café Arena A, övre plan",
)


def test_nudge_subject_says_which_shift():
    msg = render(ARENA_A, Role.PAMINNELSE, None, chore="cafévärd", schedule_link=None)
    assert msg.subject == "Påminnelse — cafévärd i morgon 12:30–16:30"


def test_heads_up_subject_says_which_shift():
    msg = render(
        ARENA_A, Role.FORHANDSBESKED, None, chore="cafévärd", schedule_link=None
    )
    assert msg.subject == "Er tur snart — cafévärd lördag 19 september 12:30–16:30"


def test_body_names_the_time_and_the_cafe():
    msg = render(ARENA_A, Role.PAMINNELSE, None, chore="cafévärd", schedule_link=None)
    assert "12:30–16:30" in msg.body
    assert "Wallenstam arena" in msg.body
    assert "Café Arena A, övre plan" in msg.body


def test_the_html_mail_says_the_same_place():
    msg = render(ARENA_A, Role.PAMINNELSE, None, chore="cafévärd", schedule_link=None)
    assert "Café Arena A, övre plan" in msg.html_body


def test_a_shift_with_no_time_reads_exactly_as_before():
    """The tabular sources carry no time column, so their occurrences must
    keep rendering the way they always did rather than growing an empty
    dash."""
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert msg.subject == "Påminnelse — matchvärd i morgon"
    assert "–" not in msg.body
    assert "Plats" not in msg.body


def test_a_shift_with_a_venue_but_no_station_names_just_the_venue():
    occ = Occurrence(
        due=date(2026, 9, 19),
        people=(BJORN,),
        start_time=time(9, 0),
        end_time=time(12, 0),
        venue="Mölnlycke idrottshall",
    )
    msg = render(occ, Role.PAMINNELSE, None, chore="cafévärd", schedule_link=None)
    assert "Mölnlycke idrottshall" in msg.body
    assert "Plats: Mölnlycke idrottshall\n" in msg.body + "\n"


def test_the_mail_falls_back_to_the_document_index():
    msg = render(
        THIS_WEEK,
        Role.PAMINNELSE,
        None,
        chore="cafévärd",
        schedule_link=None,
        documents_link="https://web.example/dokument",
    )
    assert "https://web.example/dokument" in msg.body


def test_no_instruction_link_at_all_when_nothing_is_configured():
    msg = render(THIS_WEEK, Role.PAMINNELSE, None, chore="cafévärd", schedule_link=None)
    assert "Instruktioner:" not in msg.body
