from __future__ import annotations

from datetime import date

from duty_mailer.models import Occurrence, Person, Role
from duty_mailer.templates import format_date_sv, render

ANNA = Person("anna@x.se", "Anna Svensson")
BJORN = Person("bjorn@x.se", "Björn Ek")
CARINA = Person("carina@x.se", "Carina Lund")

THIS_WEEK = Occurrence(due=date(2026, 9, 19), people=(BJORN, CARINA), lead_days=(7, 1))
LAST_WEEK = Occurrence(due=date(2026, 9, 12), people=(ANNA,))


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
    assert "anna@x.se" in msg.body


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


def test_people_without_names_show_their_address():
    occ = Occurrence(due=date(2026, 9, 19), people=(Person("x@x.se"),))
    msg = render(occ, Role.PAMINNELSE, None, chore="matchvärd", schedule_link=None)
    assert "x@x.se" in msg.body


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
