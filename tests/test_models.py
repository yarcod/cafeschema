from __future__ import annotations

from datetime import date

import pytest

from duty_mailer.models import Occurrence, Person, Role


def test_person_display_prefers_name():
    assert Person(email="a@x.se", name="Anna").display == "Anna"


def test_person_display_falls_back_to_email():
    assert Person(email="a@x.se").display == "a@x.se"


def test_occurrence_defaults_to_single_lead_day():
    occ = Occurrence(due=date(2026, 9, 19), people=(Person("a@x.se"),))
    assert occ.lead_days == (1,)


def test_occurrence_sorts_lead_days_descending():
    occ = Occurrence(
        due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(1, 7)
    )
    assert occ.lead_days == (7, 1)


def test_occurrence_rejects_more_than_two_lead_days():
    with pytest.raises(ValueError, match="högst 2"):
        Occurrence(
            due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(14, 7, 1)
        )


def test_occurrence_rejects_duplicate_lead_days():
    with pytest.raises(ValueError, match="dubblerade"):
        Occurrence(due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(7, 7))


def test_occurrence_rejects_non_positive_lead_days():
    with pytest.raises(ValueError, match="positiva"):
        Occurrence(due=date(2026, 9, 19), people=(Person("a@x.se"),), lead_days=(0,))


def test_occurrence_rejects_empty_people():
    with pytest.raises(ValueError, match="minst en"):
        Occurrence(due=date(2026, 9, 19), people=())


def test_roles_exist():
    assert {r.name for r in Role} == {"FORHANDSBESKED", "PAMINNELSE"}
