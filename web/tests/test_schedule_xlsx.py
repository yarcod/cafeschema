"""The coach's season workbook, read in memory."""

import zipfile
from datetime import datetime
from io import BytesIO

import pytest
from openpyxl import Workbook

from duty_web.schedule_xlsx import ScheduleFileError, normalize_time_range, read_schedule

HEADER = ["Vecka", "Aktivitet", "Datum", "Tid", "Person 1", "Person 2", "Person 3"]


def coach_workbook(rows, *, sheet="Säsong 26-27") -> bytes:
    """A workbook shaped like the coach's: a title, a blank line, the header, then shifts."""
    workbook = Workbook()
    ws = workbook.active
    ws.title = sheet
    ws.append(["Bemanningsschema säsong 26-27"])
    ws.append([])
    ws.append(HEADER)
    for row in rows:
        ws.append(row)
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


def seed_workbook(rows) -> bytes:
    workbook = Workbook()
    ws = workbook.active
    ws.append(["ar", "syssla", "arena", "station", "vecka", "datum", "veckodag", "tid", "barn", "anteckning"])
    for row in rows:
        ws.append(row)
    out = BytesIO()
    workbook.save(out)
    return out.getvalue()


@pytest.mark.parametrize("raw, expected", [
    ("Fre 18-21", ("18:00", "21:00")),
    ("11:30 -16:00", ("11:30", "16:00")),
    ("9-12", ("09:00", "12:00")),
    ("10.00–13.00", ("10:00", "13:00")),
])
def test_times_as_the_coach_spells_them(raw, expected):
    assert normalize_time_range(raw) == expected


@pytest.mark.parametrize("raw", ["kväll", "18", "25-26", "18-21-22"])
def test_a_time_that_isnt_one_is_refused(raw):
    with pytest.raises(ValueError):
        normalize_time_range(raw)


def test_the_coach_layout_gives_one_row_per_person():
    data = coach_workbook([
        [46, "Cafépass", datetime(2026, 11, 14), "11:30 -16:00", "Tova Exempel", "Moa  Granne", None],
        [47, "Arena värdskap höst", "2026-11-21", "Fre 18-21", "Ida Ny", None, None],
        [None, "Cafépass", "Datum ej satt (VT27)", "Fre 18-21", "Tova Exempel", None, None],
        [None, None, None, None, None, None, None],
    ])

    rows = read_schedule(data)

    assert [(r["date"], r["start"], r["end"], r["activity"], r["player"]) for r in rows] == [
        ("2026-11-14", "11:30", "16:00", "Cafépass", "Tova Exempel"),
        ("2026-11-14", "11:30", "16:00", "Cafépass", "Moa Granne"),
        ("2026-11-21", "18:00", "21:00", "Cafépass", "Ida Ny"),
        (None, "18:00", "21:00", "Cafépass", "Tova Exempel"),
    ]
    assert rows[3]["note"] == "Fredagar" and rows[0]["note"] == ""
    assert rows[0]["row"] == 4 and (rows[0]["venue"], rows[0]["station"]) == ("", "")


def test_the_old_seed_layout_keeps_its_places_and_notes():
    data = seed_workbook([
        [2026, "Bästkustcupen", "Mölnlycke idrottshall", "Café", 50, datetime(2026, 12, 12),
         "Lördag", "09:00-12:00", "Tova Exempel", "Ta med förkläde"],
        ["", "Cafépass", "", "", "", "", "", "18:00-21:00", "Moa Granne", "Fredagar"],
    ])

    rows = read_schedule(data)

    assert rows[0] | {"row": 0} == {
        "row": 0, "date": "2026-12-12", "start": "09:00", "end": "12:00",
        "activity": "Bästkustcupen", "player": "Tova Exempel",
        "venue": "Mölnlycke idrottshall", "station": "Café", "note": "Ta med förkläde",
    }
    assert rows[1]["date"] is None and rows[1]["note"] == "Fredagar"


def test_a_seed_row_without_a_player_is_refused_with_its_row():
    data = seed_workbook([["", "Cafépass", "", "", "", "", "", "18:00-21:00", "", ""]])

    with pytest.raises(ScheduleFileError, match="Rad 2"):
        read_schedule(data)


def test_a_bad_time_names_its_row():
    data = coach_workbook([[46, "Cafépass", datetime(2026, 11, 14), "kväll", "Tova Exempel", None, None]])

    with pytest.raises(ScheduleFileError, match="Rad 4"):
        read_schedule(data)


def test_something_that_isnt_a_workbook_is_refused():
    with pytest.raises(ScheduleFileError, match="Excel"):
        read_schedule(b"Datum;Tid\n2026-11-14;18-21\n")


def test_a_workbook_without_a_schedule_is_refused():
    workbook = Workbook()
    workbook.active.append(["Namn", "Telefon"])
    out = BytesIO()
    workbook.save(out)

    with pytest.raises(ScheduleFileError, match="Hittade inget schema"):
        read_schedule(out.getvalue())


def test_a_file_over_the_limit_is_refused():
    with pytest.raises(ScheduleFileError, match="för stor"):
        read_schedule(b"x" * 1_000_001)


@pytest.mark.parametrize("raw", ["2026-11-31", "14 nov", "nästa vecka", 46])
def test_a_date_that_isnt_one_names_its_row(raw):
    data = coach_workbook([[46, "Cafépass", raw, "Fre 18-21", "Tova Exempel", None, None]])

    with pytest.raises(ScheduleFileError, match="Rad 4: ogiltigt datum"):
        read_schedule(data)


@pytest.mark.parametrize("raw", [None, "", "Datum ej satt", "datum ej satt (HT27)"])
def test_a_blank_or_placeholder_date_is_undated(raw):
    data = coach_workbook([[46, "Cafépass", raw, "Fre 18-21", "Tova Exempel", None, None]])

    assert read_schedule(data)[0]["date"] is None


def _with_member(data: bytes, name: str, payload: bytes) -> bytes:
    """The workbook, with one member added or replaced."""
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            if item.filename != name:
                target.writestr(item, source.read(item.filename))
        target.writestr(name, payload)
    return out.getvalue()


def test_a_workbook_that_unpacks_huge_is_refused_before_it_is_read():
    data = _with_member(coach_workbook([]), "xl/media/padding.bin", b"\0" * 25_000_000)
    assert len(data) < 1_000_000   # small on the wire

    with pytest.raises(ScheduleFileError, match="uppackad"):
        read_schedule(data)


def test_a_workbook_of_many_members_is_refused():
    data = coach_workbook([])
    for n in range(120):
        data = _with_member(data, f"xl/media/{n}.bin", b"x")

    with pytest.raises(ScheduleFileError, match="uppackad"):
        read_schedule(data)


def test_a_broken_sheet_is_refused_as_unreadable():
    data = _with_member(coach_workbook([]), "xl/worksheets/sheet1.xml", b"<worksheet><sheetData><row")

    with pytest.raises(ScheduleFileError, match="Excel"):
        read_schedule(data)


def test_far_off_columns_are_never_read():
    workbook = Workbook()
    ws = workbook.active
    ws.append(HEADER)
    ws.append([46, "Cafépass", datetime(2026, 11, 14), "Fre 18-21", "Tova Exempel"])
    ws.cell(row=2, column=16_000, value="långt bort")
    out = BytesIO()
    workbook.save(out)

    rows = read_schedule(out.getvalue())

    assert [r["player"] for r in rows] == ["Tova Exempel"]


def test_an_overlong_cell_is_refused_with_its_row():
    data = coach_workbook([[46, "Cafépass", datetime(2026, 11, 14), "Fre 18-21", "T" * 500, None, None]])

    with pytest.raises(ScheduleFileError, match="Rad 4: för lång text"):
        read_schedule(data)
