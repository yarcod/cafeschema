from __future__ import annotations

from datetime import date

from duty_mailer.models import Occurrence, Person, Role
from duty_mailer.scheduling import (
    due_on,
    previous_occurrence,
    role_for,
    today_in,
)


def occ(day: int, *, lead_days: tuple[int, ...] = (1,), month: int = 9) -> Occurrence:
    return Occurrence(
        due=date(2026, month, day),
        people=(Person("a@x.se", "Anna"),),
        lead_days=lead_days,
    )


# --- role_for -------------------------------------------------------------


def test_single_lead_time_is_always_the_nudge():
    assert role_for(7, (7,)) is Role.PAMINNELSE


def test_two_lead_times_split_by_size_not_by_value():
    assert role_for(14, (14, 2)) is Role.FORHANDSBESKED
    assert role_for(2, (14, 2)) is Role.PAMINNELSE


# --- due_on ---------------------------------------------------------------


def test_sends_nothing_when_no_occurrence_matches():
    assert due_on([occ(19)], date(2026, 9, 10)) == []


def test_sends_the_nudge_one_day_before():
    assert due_on([occ(19)], date(2026, 9, 18)) == [(occ(19), Role.PAMINNELSE)]


def test_does_not_send_on_the_day_itself():
    assert due_on([occ(19)], date(2026, 9, 19)) == []


def test_does_not_send_after_the_occurrence_has_passed():
    assert due_on([occ(19)], date(2026, 9, 20)) == []


def test_sends_the_heads_up_seven_days_before():
    o = occ(19, lead_days=(7, 1))
    assert due_on([o], date(2026, 9, 12)) == [(o, Role.FORHANDSBESKED)]


def test_sends_both_messages_on_their_own_days():
    o = occ(19, lead_days=(7, 1))
    assert due_on([o], date(2026, 9, 12)) == [(o, Role.FORHANDSBESKED)]
    assert due_on([o], date(2026, 9, 18)) == [(o, Role.PAMINNELSE)]


def test_only_the_matching_occurrence_is_returned():
    early, late = occ(19), occ(20)
    assert due_on([late, early], date(2026, 9, 18)) == [(early, Role.PAMINNELSE)]
    assert due_on([late, early], date(2026, 9, 19)) == [(late, Role.PAMINNELSE)]


def test_two_occurrences_can_be_due_on_the_same_day():
    a = occ(19, lead_days=(1,))
    b = occ(25, lead_days=(7,))
    result = due_on([a, b], date(2026, 9, 18))
    assert result == [(a, Role.PAMINNELSE), (b, Role.PAMINNELSE)]


# --- previous_occurrence --------------------------------------------------


def test_no_predecessor_for_the_first_occurrence():
    a, b = occ(12), occ(19)
    assert previous_occurrence([a, b], a, max_gap_days=8) is None


def test_finds_the_immediately_preceding_occurrence():
    a, b, c = occ(5), occ(12), occ(19)
    assert previous_occurrence([a, b, c], c, max_gap_days=8) == b


def test_predecessor_is_found_regardless_of_input_order():
    a, b = occ(12), occ(19)
    assert previous_occurrence([b, a], b, max_gap_days=8) == a


def test_predecessor_at_exactly_the_gap_limit_is_included():
    a, b = occ(11), occ(19)
    assert previous_occurrence([a, b], b, max_gap_days=8) == a


def test_predecessor_beyond_the_gap_limit_is_dropped():
    a, b = occ(10), occ(19)
    assert previous_occurrence([a, b], b, max_gap_days=8) is None


def test_predecessor_across_a_season_break_is_dropped():
    spring, autumn = occ(20, month=5), occ(5)
    assert previous_occurrence([spring, autumn], autumn, max_gap_days=8) is None


# --- today_in -------------------------------------------------------------


def test_today_in_returns_a_date():
    assert isinstance(today_in("Europe/Stockholm"), date)


def test_stockholm_is_ahead_of_utc_late_in_the_evening():
    # At 23:30 UTC on 2026-09-18 it is already 2026-09-19 in Stockholm.
    # A UTC-derived "today" would send the wrong day's reminders.
    from datetime import datetime, timezone

    from duty_mailer.scheduling import date_in_zone

    instant = datetime(2026, 9, 18, 23, 30, tzinfo=timezone.utc)
    assert date_in_zone(instant, "Europe/Stockholm") == date(2026, 9, 19)


def test_dst_change_does_not_shift_the_date():
    # Sweden leaves DST at 03:00 on 2026-10-25. 00:30 UTC is 02:30 local,
    # still the 25th.
    from datetime import datetime, timezone

    from duty_mailer.scheduling import date_in_zone

    instant = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    assert date_in_zone(instant, "Europe/Stockholm") == date(2026, 10, 25)
