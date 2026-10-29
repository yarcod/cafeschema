"""A blank season workbook for an ansvarig who'd rather plan in Excel.

The first sheet is the coach's layout that schedule_xlsx reads, so a filled-in
template goes through Importera Excel → Granska like any coach's file. The
guide and the dropdown lists sit on later sheets without those headings: the
import reads the first sheet that has them, so neither the example nor the
lists can become passes.

The dropdowns offer the team's children and the activities the app knows,
spelled the way the app knows them, so Granska has fewer names and places to
ask about. They still accept anything else; Granska asks about that as usual.
"""

from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.worksheet.datavalidation import DataValidation

from .schedule_xlsx import PERSON_COLUMNS

HEADINGS = ("Aktivitet", "Datum", "Tid", *PERSON_COLUMNS)
# Formatted and validated rows: a season, with room to spare.
ROWS = 500
WIDTHS = {"A": 26, "B": 13, "C": 13, "D": 24, "E": 24, "F": 24}


def _text(cell, value: str) -> None:
    """A plain string, even one starting with '=': names come from 360Player
    and Excel must never run one as a formula."""
    cell.value = value
    cell.data_type = "s"


def _list(sheet, column: int, values: list[str]) -> str | None:
    """Write values down a column of the list sheet; the range a dropdown uses."""
    for row, value in enumerate(values, start=1):
        _text(sheet.cell(row, column), value)
    if not values:
        return None
    letter = "AB"[column - 1]
    return f"Listor!${letter}$1:${letter}${len(values)}"


def _dropdown(source: str, ranges: str) -> DataValidation:
    validation = DataValidation(type="list", formula1=f"={source}", allow_blank=True)
    # Something not in the list is Granska's question, not Excel's refusal.
    validation.showErrorMessage = False
    validation.add(ranges)
    return validation


def _next_saturday(today: date) -> date:
    return today + timedelta(days=(5 - today.weekday()) % 7 or 7)


def _guide(sheet, example_activity: str) -> None:
    sheet.column_dimensions["A"].width = 100
    lines = [
        ("Så fyller du i Excel-mallen", True),
        ("", False),
        (("Fyll i fliken Schema, en rad per pass. Importera sedan filen under "
          "✎ → Pass → Importera Excel. Ingenting ändras förrän du har granskat."), False),
        ("", False),
        (("Aktivitet: välj i listan, eller skriv en ny. Granskningen frågar var en ny "
          "aktivitet hålls."), False),
        ("Datum: ett datum, t.ex. 2026-11-14. Lämna tomt om datumet inte är satt än.", False),
        (("Tid: start och slut, t.ex. 18-21, 9.30-12 eller Fre 18-21. Utan datum visar "
          "veckodagen föräldrarna vilken dag passet brukar ligga."), False),
        (("Person 1–3: barnen på passet, högst tre per rad. Välj i listan; ett namn appen "
          "inte känner igen frågar granskningen om. Tom plats blir Ledigt."), False),
        ("", False),
        ("Exempel (skriv i fliken Schema, inte här):", True),
    ]
    for row, (line, bold) in enumerate(lines, start=1):
        cell = sheet.cell(row, 1, line)
        cell.font = Font(bold=bold)
    start = len(lines) + 1
    for column, heading in enumerate(HEADINGS, start=1):
        sheet.cell(start, column, heading).font = Font(bold=True)
    example = sheet.cell(start + 1, 2, _next_saturday(date.today()))
    example.number_format = "yyyy-mm-dd"
    _text(sheet.cell(start + 1, 1), example_activity)
    sheet.cell(start + 1, 3, "9-12")
    sheet.cell(start + 1, 4, "Barnets namn")


def build_template(players: list[str], activities: list[str]) -> bytes:
    """The template as .xlsx bytes, with these children and activities offered."""
    workbook = Workbook()
    schedule = workbook.worksheets[0]
    schedule.title = "Schema"
    guide = workbook.create_sheet("Så fyller du i")
    lists = workbook.create_sheet("Listor")
    lists.sheet_state = "hidden"

    for column, heading in enumerate(HEADINGS, start=1):
        schedule.cell(1, column, heading).font = Font(bold=True)
    schedule.freeze_panes = "A2"
    for letter, width in WIDTHS.items():
        schedule.column_dimensions[letter].width = width
    for row in range(2, ROWS + 2):
        schedule.cell(row, 2).number_format = "yyyy-mm-dd"
        # Text, or Excel reads "9-12" as the 12th of September.
        schedule.cell(row, 3).number_format = "@"

    last = ROWS + 1
    people = _list(lists, 1, players)
    known = _list(lists, 2, activities)
    if people:
        schedule.add_data_validation(_dropdown(people, f"D2:F{last}"))
    if known:
        schedule.add_data_validation(_dropdown(known, f"A2:A{last}"))
    when = DataValidation(
        type="date", operator="greaterThan", formula1="DATE(2000,1,1)", allow_blank=True,
        errorStyle="warning", error="Skriv ett datum, t.ex. 2026-11-14, eller lämna tomt.",
        errorTitle="Datum",
    )
    when.add(f"B2:B{last}")
    schedule.add_data_validation(when)

    _guide(guide, activities[0] if activities else "Cafépass")

    out = BytesIO()
    workbook.save(out)
    return out.getvalue()
