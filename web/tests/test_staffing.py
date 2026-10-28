"""Fair staffing (spec D7): fewest shifts first, chance only among equals."""

import random
from datetime import date, time, timedelta

import pytest

from duty_web.staffing import (
    BUSY,
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


def cand(player_id, load=0, family=None, days=()):
    return Candidate(player_id, frozenset(family or {player_id * 100}), load, set(days))


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
