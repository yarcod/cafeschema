from __future__ import annotations

from datetime import date, datetime

import pytest
from openpyxl import Workbook

from duty_mailer.sources.rows import RosterError
from duty_mailer.sources.xlsx import XlsxSource


def make_sheet(tmp_path, rows):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    path = tmp_path / "schema.xlsx"
    wb.save(path)
    return path


def test_reads_a_wide_sheet(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost1", "namn1", "epost2", "namn2"],
        ["2026-09-19", "a@x.se", "Anna", "b@x.se", "Björn"],
        ["2026-09-26", "c@x.se", "Carina", None, None],
    ])
    occs = XlsxSource(path, default_lead_days=(1,)).fetch()
    assert [o.due for o in occs] == [date(2026, 9, 19), date(2026, 9, 26)]
    assert [len(o.people) for o in occs] == [2, 1]
    assert occs[0].people[0].name == "Anna"


def test_reads_native_excel_date_cells(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost1"],
        [datetime(2026, 9, 19), "a@x.se"],
    ])
    occs = XlsxSource(path, default_lead_days=(1,)).fetch()
    assert occs[0].due == date(2026, 9, 19)


def test_reads_a_tall_sheet(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost", "namn"],
        ["2026-09-19", "a@x.se", "Anna"],
        ["2026-09-19", "b@x.se", "Björn"],
    ])
    occs = XlsxSource(path, default_lead_days=(1,)).fetch()
    assert len(occs) == 1
    assert len(occs[0].people) == 2


def test_applies_the_default_lead_days(tmp_path):
    path = make_sheet(tmp_path, [["datum", "epost1"], ["2026-09-19", "a@x.se"]])
    occs = XlsxSource(path, default_lead_days=(7, 1)).fetch()
    assert occs[0].lead_days == (7, 1)


def test_fully_blank_rows_are_ignored(tmp_path):
    path = make_sheet(tmp_path, [
        ["datum", "epost1"],
        ["2026-09-19", "a@x.se"],
        [None, None],
    ])
    assert len(XlsxSource(path, default_lead_days=(1,)).fetch()) == 1


def test_a_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(RosterError, match="hittades inte"):
        XlsxSource(tmp_path / "nope.xlsx", default_lead_days=(1,)).fetch()


def test_an_empty_sheet_is_reported_clearly(tmp_path):
    path = make_sheet(tmp_path, [])
    with pytest.raises(RosterError, match="tomt"):
        XlsxSource(path, default_lead_days=(1,)).fetch()
