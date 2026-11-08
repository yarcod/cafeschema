"""Fair staffing (spec D7): fewest shifts first, chance only among equals."""

import random
from datetime import date, time, timedelta

import pytest

from duty_web.staffing import (
    BUSY,
    BUSY_WEEKEND,
    CLASHES,
    ELSEWHERE,
    NO_ONE,
    ON_SHIFT,
    Candidate,
    Seat,
    assign,
    season_of,
    spread,
)

DAY = date(2027, 3, 13)


def seat(slot_id, day=DAY, start=9, station=1):
    return Seat(slot_id, day, time(start), time(start + 3), station)


def cand(player_id, load=0, family=None, days=(), elsewhere=()):
    return Candidate(
        player_id, frozenset(family or {player_id * 100}), load, set(days), tuple(elsewhere)
    )


def chosen(results):
    return [r.player_id for r in results]


def test_the_fewest_shifts_go_first():
    seats = [seat(1), seat(2, DAY + timedelta(days=21)), seat(3, DAY + timedelta(days=42))]

    results = assign(seats, [cand(1, load=0), cand(2, load=3), cand(3, load=6)], seed=7)

    assert chosen(results) == [1, 1, 1]


def test_nobody_gets_two_shifts_on_one_day():
    results = assign([seat(1, start=9), seat(2, start=12)], [cand(1), cand(2, load=5)], seed=1)

    assert chosen(results) == [1, 2]


def test_a_seat_nobody_can_take_stays_free_with_a_reason():
    results = assign([seat(1, start=9), seat(2, start=12)], [cand(1)], seed=1)

    assert chosen(results) == [1, None]
    assert results[1].reason == BUSY == "alla kvarvarande har redan pass den dagen"


def test_no_candidates_at_all_says_so():
    (result,) = assign([seat(1)], [], seed=1)

    assert (result.player_id, result.reason) == (None, NO_ONE)


def test_an_undated_shift_never_takes_someone_twice():
    results = assign([seat(1, day=None), seat(2, day=None)], [cand(1)], seed=1)

    assert chosen(results) == [1, None] and results[1].reason == ON_SHIFT


def test_a_sibling_joins_the_shift_among_equals():
    occupied = {seat(1).shift: {1}}
    for s in range(30):
        (result,) = assign(
            [seat(1)], [cand(2, family={7}), cand(3, family={8})], seed=s,
            occupied=occupied, families={1: frozenset({7})},
        )
        assert result.player_id == 2


def test_fairness_beats_a_sibling():
    (result,) = assign(
        [seat(1)], [cand(2, load=1, family={7}), cand(3, load=0)], seed=3,
        occupied={seat(1).shift: {1}}, families={1: frozenset({7})},
    )

    assert result.player_id == 3


def test_two_weekends_running_is_avoided_among_equals():
    last_week, long_ago = DAY - timedelta(days=7), DAY - timedelta(days=60)
    for s in range(30):
        (result,) = assign(
            [seat(1)], [cand(1, load=1, days={last_week}), cand(2, load=1, days={long_ago})], seed=s
        )
        assert result.player_id == 2


def test_a_shift_the_same_weekend_rules_someone_out_however_few_shifts_they_have():
    for held, day in [(DAY, DAY + timedelta(days=1)),    # Saturday, then Sunday
                      (DAY, DAY - timedelta(days=1))]:   # Saturday, then Friday
        (result,) = assign([seat(1, day)], [cand(1, days={held}), cand(2, load=5)], seed=1)
        assert result.player_id == 2


def test_weekdays_are_not_a_weekend():
    thursday, monday = DAY - timedelta(days=2), DAY + timedelta(days=2)
    for held, day in [(DAY, thursday), (DAY, monday), (thursday, DAY)]:
        (result,) = assign([seat(1, day)], [cand(1, days={held}), cand(2, load=5)], seed=1)
        assert result.player_id == 1


def test_the_draw_itself_keeps_a_family_to_one_shift_a_weekend():
    results = assign([seat(1), seat(2, DAY + timedelta(days=1))], [cand(1)], seed=1)

    assert chosen(results) == [1, None]
    assert results[1].reason == BUSY_WEEKEND == "alla kvarvarande har redan pass den helgen"


def test_a_clash_on_another_team_rules_someone_out():
    clash = [(DAY, time(10), time(12))]

    (result,) = assign([seat(1)], [cand(1, elsewhere=clash), cand(2, load=5)], seed=1)

    assert result.player_id == 2


def test_another_teams_shift_that_only_touches_this_one_is_no_clash():
    before, after = [(DAY, time(7), time(9))], [(DAY, time(12), time(14))]

    (result,) = assign([seat(1)], [cand(1, elsewhere=before + after), cand(2, load=5)], seed=1)

    assert result.player_id == 1


def test_another_teams_shift_on_another_day_is_no_clash():
    (result,) = assign(
        [seat(1)],
        [cand(1, elsewhere=[(DAY + timedelta(days=1), time(9), time(12))]), cand(2, load=5)],
        seed=1,
    )

    assert result.player_id == 1


def test_a_seat_stays_free_saying_which_rule_stopped_it():
    weekday = DAY + timedelta(days=3)
    clash = [(weekday, time(9), time(12))]

    (alone,) = assign([seat(1, weekday)], [cand(1, elsewhere=clash)], seed=1)
    (mixed,) = assign(
        [seat(1, weekday)], [cand(1, elsewhere=clash), cand(2, days={weekday})], seed=1
    )

    assert (alone.player_id, alone.reason) == (None, ELSEWHERE)
    assert (mixed.player_id, mixed.reason) == (None, CLASHES)


def _weekends(firsts, starts, seats_per_shift=2):
    return [
        Seat(n, day, time(hour), time(hour + 2), 1)
        for n, (day, hour, _) in enumerate(
            (first + timedelta(days=d), hour, s)
            for first in firsts for d in (0, 1) for hour in starts for s in range(seats_per_shift)
        )
    ]


def test_those_who_waited_a_weekend_all_go_first_the_next():
    # 11 shifts of two seats a weekend, for 30 families: 8 wait each time.
    # A Saturday with six shifts and a Sunday with five.
    firsts = [DAY, DAY + timedelta(days=14)]
    seats = [s for s in _weekends(firsts, range(8, 20, 2)) if s.day.weekday() == 5 or s.start.hour < 18]
    pool = [cand(p) for p in range(1, 31)]
    by_id = {s.slot_id: s for s in seats}

    results = assign(seats, pool, seed=11)

    first = {r.player_id for r in results if by_id[r.slot_id].day < firsts[1]}
    second = {r.player_id for r in results if by_id[r.slot_id].day >= firsts[1]}
    assert (len(first), len(second)) == (22, 22)
    assert set(range(1, 31)) - first <= second


@pytest.mark.parametrize("seed", range(20))
def test_sixty_seats_over_three_weekends_are_shared_one_a_weekend(seed):
    # F19/20's real shape: 5 shifts of two seats, Saturday and Sunday, for 23 families.
    firsts = [date(2026, 11, 28), date(2027, 1, 23), date(2027, 4, 24)]
    seats = _weekends(firsts, range(8, 18, 2))
    by_id = {s.slot_id: s for s in seats}

    results = assign(seats, [cand(p) for p in range(1, 24)], seed=seed)

    assert all(r.player_id is not None for r in results)
    weekends = {}
    for result in results:
        day = by_id[result.slot_id].day
        weekend = day - timedelta(days=day.weekday())
        assert weekend not in weekends.setdefault(result.player_id, set())
        weekends[result.player_id].add(weekend)
    assert sorted(len(held) for held in weekends.values()) == [2] * 9 + [3] * 14


def test_the_same_seed_gives_the_same_answer_and_seeds_differ():
    seats = [seat(i, DAY + timedelta(days=14 * i)) for i in range(6)]
    pool = [cand(i) for i in range(1, 7)]

    first = chosen(assign(seats, pool, seed=42))

    assert chosen(assign(seats, pool, seed=42)) == first
    assert len({tuple(chosen(assign(seats, pool, seed=s))) for s in range(20)}) > 1


@pytest.mark.parametrize("seed", range(40))
def test_the_rules_hold_for_any_seed(seed):
    rng = random.Random(seed)
    days = [DAY + timedelta(days=7 * week + rng.choice((0, 1))) for week in range(15)]
    seats = [seat(i, days[i // 2], start=9 + 3 * (i % 2)) for i in range(30)]
    pool = [cand(p) for p in range(1, 13)]
    by_id = {s.slot_id: s for s in seats}

    results = assign(seats, pool, seed=seed)

    taken: dict[int, set] = {}
    for result in results:
        assert result.player_id in {c.player_id for c in pool}
        day = by_id[result.slot_id].day
        assert day not in taken.setdefault(result.player_id, set())
        taken[result.player_id].add(day)
    assert spread(len(taken.get(c.player_id, ())) for c in pool)[1] <= 1


def test_the_season_runs_august_to_july():
    assert season_of(date(2026, 8, 1)) == (date(2026, 8, 1), date(2027, 7, 31))
    assert season_of(date(2027, 7, 31)) == (date(2026, 8, 1), date(2027, 7, 31))
    assert season_of(date(2026, 7, 31)) == (date(2025, 8, 1), date(2026, 7, 31))


def test_spread_is_mean_and_width():
    assert spread([1, 2, 3]) == (2.0, 2)
    assert spread([]) == (0.0, 0)
