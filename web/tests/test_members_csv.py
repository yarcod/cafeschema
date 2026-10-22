"""Reading 360Player's members export (Truppen → Exportera)."""

import pytest

from duty_web.admin.actions.base import ValidationError
from duty_web.admin.members_csv import MAX_BYTES, interpret, parse_members_csv

HEADER = "Förnamn,Efternamn,E-post,Telefonnummer,Föräldrars namn,Barnens namn"


def export(*rows, bom=False):
    text = "\n".join([HEADER, *rows]) + "\n"
    return ("﻿" if bom else "").encode("utf-8") + text.encode("utf-8")


def test_rows_become_names_addresses_and_relations():
    rows = parse_members_csv(export(
        "Tova,Exempel,,0700000000,Alva Exempel;Björn Exempel,",
        "Alva,Exempel, TOVA@Exempel.se ,0701111111,,Tova Exempel",
    ))

    assert rows == [
        {"name": "Tova Exempel", "email": "", "parents": ["Alva Exempel", "Björn Exempel"], "children": []},
        {"name": "Alva Exempel", "email": "tova@exempel.se", "parents": [], "children": ["Tova Exempel"]},
    ]


def test_the_phone_number_is_never_read():
    rows = parse_members_csv(export("Alva,Exempel,a@exempel.se,0701111111,,Tova Exempel"))

    assert "0701111111" not in repr(rows)


def test_a_byte_order_mark_is_harmless():
    rows = parse_members_csv(export("Alva,Exempel,a@exempel.se,,,Tova Exempel", bom=True))

    assert rows[0]["name"] == "Alva Exempel"


def test_a_file_that_is_not_the_export_says_so():
    with pytest.raises(ValidationError) as caught:
        parse_members_csv(b"parent_name,parent_email,children_on_team\nA,a@x.se,B\n")

    assert "360Players medlemsexport" in caught.value.errors["file"]


def test_a_file_in_another_encoding_says_so():
    with pytest.raises(ValidationError) as caught:
        parse_members_csv(export("Åsa,Öberg,a@exempel.se,,,Ida Öberg").decode("utf-8").encode("latin-1"))

    assert "UTF-8" in caught.value.errors["file"]


def test_an_empty_or_huge_file_is_refused():
    with pytest.raises(ValidationError):
        parse_members_csv(export())
    with pytest.raises(ValidationError):
        parse_members_csv(b"x" * (MAX_BYTES + 1))


def test_leaders_are_parents_listed_again_without_children():
    roster = interpret(parse_members_csv(export(
        "Moa,Granne,,,Maja Granne,",
        "Maja,Granne,maja@exempel.se,,,Moa Granne",
        "Maja,Granne,maja@exempel.se,,,",          # her leader membership
        "Okänd,Ledare,okand@exempel.se,,,",        # a member who is no parent here
        "Utan,Post,,,,Moa Granne",                 # a guardian without email
    )))

    assert roster.leaders == {"maja@exempel.se"}
    assert roster.ignored_members == 1 and roster.guardians_without_email == 1
    assert roster.guardians["maja@exempel.se"].children == {"moa granne"}
    assert roster.players == {"moa granne": "Moa Granne"}


def test_a_child_without_a_player_row_is_not_a_player_here():
    roster = interpret(parse_members_csv(export(
        "Moa,Granne,,,Maja Granne,",
        "Maja,Granne,maja@exempel.se,,,Moa Granne;Syskon Granne",
    )))

    assert roster.players == {"moa granne": "Moa Granne"}
    assert roster.guardians["maja@exempel.se"].children == {"moa granne"}
    assert roster.children_without_row == 1


def test_a_guardian_of_another_teams_children_is_not_counted_as_missing_email():
    roster = interpret(parse_members_csv(export(
        "Moa,Granne,,,Maja Granne,",
        "Utan,Post,,,,Syskon Granne",
    )))

    assert roster.guardians_without_email == 0 and roster.children_without_row == 1
