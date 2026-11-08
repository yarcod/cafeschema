"""The duty knowledge the trainer's schedule file doesn't carry."""

from datetime import date, timedelta

from duty_web.duties import PATTERNS, canonical_duty_name, pattern_sessions, profile_for


def test_the_cup_is_staffed_in_molnlycke_idrottshall():
    profile = profile_for("Bästkustcupen")

    assert profile.venue == "Mölnlycke idrottshall"
    assert profile.station == "Café"


def test_a_cafepass_is_the_lower_floor_of_arena_b():
    profile = profile_for("Cafépass")

    assert profile.venue == "Wallenstam arena"
    assert profile.station == "Café Arena B, nedre plan"


def test_a_profile_names_a_place_not_an_instruction():
    """Which instruction a station links is decided under Platser, in the
    database; a filename in code would only go stale."""
    assert not hasattr(profile_for("Cafépass"), "document")


def test_both_arena_hosting_terms_are_now_called_cafepass():
    assert canonical_duty_name("Arena värdskap höst") == "Cafépass"
    assert canonical_duty_name("Arena värdskap vinter") == "Cafépass"


def test_a_name_that_was_never_renamed_is_returned_as_it_is():
    assert canonical_duty_name("Bästkustcupen") == "Bästkustcupen"
    assert canonical_duty_name("Åby Julmarknad") == "Åby Julmarknad"


def test_lookup_ignores_case_and_surrounding_whitespace():
    assert canonical_duty_name("  arena värdskap höst ") == "Cafépass"
    assert profile_for(" bästkustcupen ") == profile_for("Bästkustcupen")


def test_a_slot_still_carrying_the_old_name_finds_its_profile():
    """The rename reaches the database by migration, not by re-import, so a
    database that hasn't been migrated yet still finds its station."""
    assert profile_for("Arena värdskap höst") == profile_for("Cafépass")


def test_a_duty_we_know_nothing_about_has_no_profile():
    assert profile_for("Åby Julmarknad") is None
    assert profile_for("") is None


FRI, SAT, SUN = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 22)


def test_a_fri_sun_span_generates_exactly_eleven_sessions():
    got = pattern_sessions(PATTERNS["cafe_weekend"], [FRI, SAT, SUN])

    assert len(got) == 11
    assert got[0] == (FRI, "17:30", "19:30")
    assert [(s, e) for d, s, e in got if d == SAT] == [
        ("08:30", "10:30"), ("10:30", "12:30"), ("12:30", "14:30"), ("14:30", "16:30"), ("16:30", "18:30")]


def test_a_wider_span_only_uses_fri_sat_sun():
    week = [date(2026, 11, 16) + timedelta(days=n) for n in range(7)]

    assert len(pattern_sessions(PATTERNS["cafe_weekend"], week)) == 11


def test_a_sat_sun_span_has_no_friday():
    got = pattern_sessions(PATTERNS["cafe_weekend"], [SAT, SUN])

    assert len(got) == 10 and FRI not in {d for d, _, _ in got}


def test_two_weekends_in_one_span_make_two_sets():
    days = [FRI + timedelta(days=n) for n in range(10)]

    assert len(pattern_sessions(PATTERNS["cafe_weekend"], days)) == 22
