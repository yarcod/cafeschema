from __future__ import annotations

from datetime import date, datetime

import pytest

from duty_mailer.sources.rows import RosterError, occurrences_from_rows, parse_date

DEFAULT = (1,)


def normalize(rows):
    return occurrences_from_rows(rows, default_lead_days=DEFAULT)


# --- parse_date -----------------------------------------------------------


def test_parse_date_accepts_iso_strings():
    assert parse_date("2026-09-19") == date(2026, 9, 19)


def test_parse_date_accepts_datetimes_from_excel():
    assert parse_date(datetime(2026, 9, 19, 0, 0)) == date(2026, 9, 19)


def test_parse_date_accepts_dates():
    assert parse_date(date(2026, 9, 19)) == date(2026, 9, 19)


def test_parse_date_ignores_blanks():
    assert parse_date(None) is None
    assert parse_date("   ") is None


def test_parse_date_rejects_gibberish():
    with pytest.raises(RosterError, match="datum"):
        parse_date("nästa lördag")


# --- layouts --------------------------------------------------------------


def test_wide_layout_gathers_every_email_column():
    occs = normalize([
        {"datum": "2026-09-19", "epost1": "a@x.se", "epost2": "b@x.se"},
    ])
    assert len(occs) == 1
    assert [p.email for p in occs[0].people] == ["a@x.se", "b@x.se"]


def test_tall_layout_merges_rows_sharing_a_date():
    occs = normalize([
        {"datum": "2026-09-19", "epost": "a@x.se"},
        {"datum": "2026-09-19", "epost": "b@x.se"},
    ])
    assert len(occs) == 1
    assert [p.email for p in occs[0].people] == ["a@x.se", "b@x.se"]


def test_ragged_wide_rows_are_fine():
    occs = normalize([
        {"datum": "2026-09-19", "epost1": "a@x.se", "epost2": "b@x.se"},
        {"datum": "2026-09-26", "epost1": "c@x.se", "epost2": None},
    ])
    assert [len(o.people) for o in occs] == [2, 1]


def test_names_are_paired_with_their_email_column():
    occs = normalize([
        {
            "datum": "2026-09-19",
            "epost1": "a@x.se", "namn1": "Anna",
            "epost2": "b@x.se", "namn2": "Björn",
        },
    ])
    assert [(p.email, p.name) for p in occs[0].people] == [
        ("a@x.se", "Anna"),
        ("b@x.se", "Björn"),
    ]


def test_results_are_sorted_by_date():
    occs = normalize([
        {"datum": "2026-09-26", "epost": "b@x.se"},
        {"datum": "2026-09-19", "epost": "a@x.se"},
    ])
    assert [o.due for o in occs] == [date(2026, 9, 19), date(2026, 9, 26)]


def test_duplicate_addresses_on_one_date_are_collapsed():
    occs = normalize([
        {"datum": "2026-09-19", "epost1": "a@x.se", "epost2": "a@x.se"},
    ])
    assert len(occs[0].people) == 1


def test_addresses_are_trimmed_and_lowercased():
    occs = normalize([{"datum": "2026-09-19", "epost": "  Anna@X.SE  "}])
    assert occs[0].people[0].email == "anna@x.se"


def test_column_order_is_numeric_not_lexicographic():
    occs = normalize([
        {
            "datum": "2026-09-19",
            "epost1": "a@x.se",
            "epost2": "b@x.se",
            "epost10": "c@x.se",
        },
    ])
    assert [p.email for p in occs[0].people] == ["a@x.se", "b@x.se", "c@x.se"]


# --- optional columns -----------------------------------------------------


def test_lead_days_are_read_from_the_row():
    occs = normalize([{"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": "7,1"}])
    assert occs[0].lead_days == (7, 1)


def test_lead_days_accept_a_single_value():
    occs = normalize([{"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": "3"}])
    assert occs[0].lead_days == (3,)


def test_empty_lead_days_falls_back_to_the_default():
    occs = occurrences_from_rows(
        [{"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": ""}],
        default_lead_days=(7, 1),
    )
    assert occs[0].lead_days == (7, 1)


def test_key_location_is_read_when_present():
    occs = normalize([
        {"datum": "2026-09-19", "epost": "a@x.se", "nyckelplats": "hos Anna"},
    ])
    assert occs[0].key_location == "hos Anna"


def test_key_location_defaults_to_none():
    occs = normalize([{"datum": "2026-09-19", "epost": "a@x.se"}])
    assert occs[0].key_location is None


# --- tolerance and errors -------------------------------------------------


def test_header_casing_and_whitespace_are_ignored():
    occs = normalize([{"  Datum ": "2026-09-19", "EPOST1": "a@x.se"}])
    assert occs[0].due == date(2026, 9, 19)


def test_rows_without_a_date_are_skipped():
    occs = normalize([
        {"datum": None, "epost": "a@x.se"},
        {"datum": "2026-09-19", "epost": "b@x.se"},
    ])
    assert len(occs) == 1


def test_a_date_with_nobody_assigned_is_skipped():
    # A half-filled future row is normal in a real roster, not an error.
    occs = normalize([
        {"datum": "2026-09-19", "epost1": None},
        {"datum": "2026-09-26", "epost1": "a@x.se"},
    ])
    assert [o.due for o in occs] == [date(2026, 9, 26)]


def test_missing_date_column_is_an_error():
    with pytest.raises(RosterError, match="datum"):
        normalize([{"epost": "a@x.se"}])


def test_no_email_columns_at_all_is_an_error():
    with pytest.raises(RosterError, match="epost"):
        normalize([{"datum": "2026-09-19", "kommentar": "hej"}])


def test_an_invalid_lead_day_is_reported_with_its_date():
    with pytest.raises(RosterError, match="2026-09-19"):
        normalize([
            {"datum": "2026-09-19", "epost": "a@x.se", "dagar_innan": "14,7,1"},
        ])
