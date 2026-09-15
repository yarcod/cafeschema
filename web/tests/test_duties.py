"""The duty knowledge the trainer's schedule file doesn't carry."""

from duty_web.duties import canonical_duty_name, profile_for


def test_the_cup_is_staffed_in_molnlycke_idrottshall():
    profile = profile_for("Bästkustcupen")

    assert profile.venue == "Mölnlycke idrottshall"
    assert profile.station == "Café"
    assert profile.document == "Cafeteria_Instruktion_MIH.pdf"


def test_a_cafepass_is_the_lower_floor_of_arena_b():
    profile = profile_for("Cafépass")

    assert profile.venue == "Wallenstam arena"
    assert profile.station == "Café Arena B, nedre plan"
    assert profile.document == "Cafeteria_Instruktion_W_Arena_B_nedre_plan.pdf"


def test_both_arena_hosting_terms_are_now_called_cafepass():
    assert canonical_duty_name("Arena värdskap höst") == "Cafépass"
    assert canonical_duty_name("Arena värdskap vinter") == "Cafépass"


def test_a_name_that_was_never_renamed_is_returned_as_it_is():
    assert canonical_duty_name("Bästkustcupen") == "Bästkustcupen"
    assert canonical_duty_name("Åby Julmarknad") == "Åby Julmarknad"


def test_lookup_ignores_case_and_surrounding_whitespace():
    assert canonical_duty_name("  arena värdskap höst ") == "Cafépass"
    assert profile_for(" bästkustcupen ").document == "Cafeteria_Instruktion_MIH.pdf"


def test_a_slot_still_carrying_the_old_name_finds_its_profile():
    """The rename reaches the database by migration, not by re-import, so a
    database that hasn't been migrated yet still gets the right document."""
    assert profile_for("Arena värdskap höst") == profile_for("Cafépass")


def test_a_duty_we_know_nothing_about_has_no_profile():
    assert profile_for("Åby Julmarknad") is None
    assert profile_for("") is None
