"""Challenge kinds (src/challenges.py): math, memory grid, sorting and the typed phrase as steps of a list, the Run that
walks them, the stricter / looser table, the migration of old configs and backups, the bedtime list choice, and the
GuardService / page / App log wiring on top. The page never holds an answer: tests solve a puzzle from its VIEW only."""
import random
import re
from datetime import datetime
from fractions import Fraction

import pytest

import antibypass as ab
import applog_changes
import backup
import challenges as ch
import reminders
import webapi
from db import Database
from webapi import core, guard
from webapi.guard import GuardService

NOW = datetime(2026, 10, 13, 19, 0)            # a Tuesday evening
MATH, MEMORY, SORT, PHRASE = "math", "memory", "sort", "phrase"


def step(kind, level, times=1, id=None):
    return {"id": id or f"{kind[0]}{level}t{times}", "kind": kind, "level": level, "times": times}


# ---------------------------------------------------------------- solving a puzzle from what the page is shown

def as_fraction(text: str) -> Fraction:
    return Fraction(text[:-1]) / 100 if text.endswith("%") else Fraction(text)


def solve_math(expr: str) -> int:
    if "x" in expr:
        m = re.fullmatch(r"(\d+)x ([+−]) (\d+) = (\d+)", expr)
        a, sign, b, c = int(m[1]), m[2], int(m[3]), int(m[4])
        return (c - b) // a if sign == "+" else (c + b) // a
    plain = expr.replace("−", "-").replace("×", "*").replace("÷", "//")
    assert re.fullmatch(r"[0-9 ()+\-*/]+", plain), expr
    return eval(plain)      # noqa: S307 - digits and operators only (checked above)


def solve(view: dict):
    """The right answer for a puzzle, worked out from its view alone, in the form submit takes."""
    kind = view["kind"]
    if kind == MATH:
        return str(solve_math(view["expr"]))
    if kind == MEMORY:
        return list(view["lit"]) if view["ordered"] else sorted(view["lit"], reverse=True)
    if kind == SORT:
        values = [as_fraction(t) for t in view["items"]]
        return sorted(range(len(values)), key=lambda i: values[i])
    return view["grid_words"] if "grid_words" in view else view["phrase"]


def wrong_for(view: dict):
    """An answer that is certainly wrong for this puzzle."""
    kind = view["kind"]
    if kind == MATH:
        return str(int(solve(view)) + 1)
    if kind == MEMORY:
        return [(c + 1) % (view["size"] ** 2) for c in view["lit"]][:-1]       # (one short, and moved)
    if kind == SORT:
        return list(reversed(solve(view)))
    return "nope"


class Clock:
    """A monotonic clock the test sets: run(...) calls it."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def wait(self, seconds=30):
        self.t += seconds


def make_run(steps, clock=None, seed=1, **kw):
    clock = clock or Clock()
    return ch.Run(steps, rng=random.Random(seed), clock=clock, **kw), clock


# ---------------------------------------------------------------- generators: ranges, distinct values, right answers

@pytest.mark.parametrize("seed", range(40))
def test_math_easy_is_two_digit_plus_or_minus_and_never_negative(seed):
    p = ch.math_puzzle(0, random.Random(seed))
    m = re.fullmatch(r"(\d+) ([+−]) (\d+)", p.view["expr"])
    a, sign, b = int(m[1]), m[2], int(m[3])
    assert 10 <= a <= 99 and 10 <= b <= 99 and p.view["find_x"] is False
    assert sign == "+" or a >= b
    assert p.answer == solve_math(p.view["expr"]) >= 0


def test_math_medium_has_the_four_operations_in_range():
    seen = set()
    for seed in range(300):
        p = ch.math_puzzle(1, random.Random(seed))
        expr = p.view["expr"]
        assert p.answer == solve_math(expr) >= 0 and p.view["find_x"] is False
        if m := re.fullmatch(r"(\d+) ([+−]) (\d+)", expr):
            assert 10 <= int(m[1]) <= 99 and 10 <= int(m[3]) <= 99 and (m[2] == "+" or int(m[1]) >= int(m[3]))
            seen.add(m[2])
        elif m := re.fullmatch(r"(\d+) × (\d+)", expr):
            assert 2 <= int(m[1]) <= 19 and 2 <= int(m[2]) <= 9
            seen.add("×")
        else:
            m = re.fullmatch(r"(\d+) ÷ (\d+)", expr)
            a, b = int(m[1]), int(m[2])
            assert 2 <= b <= 12 and a % b == 0 and 2 <= a // b <= 12
            seen.add("÷")
    assert seen == {"+", "−", "×", "÷"}


@pytest.mark.parametrize("seed", range(80))
def test_math_hard_is_brackets_times_then_plus_or_minus_and_not_negative(seed):
    p = ch.math_puzzle(2, random.Random(seed))
    m = re.fullmatch(r"\((\d+) ([+−]) (\d+)\) × (\d+) ([+−]) (\d+)", p.view["expr"])
    a, inner, b, c, outer, d = int(m[1]), m[2], int(m[3]), int(m[4]), m[5], int(m[6])
    assert 10 <= a <= 99 and 10 <= b <= 99 and 2 <= c <= 9 and 10 <= d <= 99
    assert inner == "+" or a > b
    assert p.answer == solve_math(p.view["expr"]) >= 0


def test_math_very_hard_is_a_product_or_find_x():
    seen = set()
    for seed in range(200):
        p = ch.math_puzzle(3, random.Random(seed))
        expr = p.view["expr"]
        assert p.answer == solve_math(expr) and p.answer >= 0
        if p.view["find_x"]:
            m = re.fullmatch(r"(\d+)x ([+−]) (\d+) = (\d+)", expr)
            a, b, c = int(m[1]), int(m[3]), int(m[4])
            assert 2 <= a <= 12 and 2 <= p.answer <= 20 and 1 <= b <= 99 and c > 0
            seen.add("x")
        else:
            m = re.fullmatch(r"(\d+) × (\d+)", expr)
            assert 11 <= int(m[1]) <= 99 and 11 <= int(m[2]) <= 99
            seen.add("product")
    assert seen == {"x", "product"}


def test_math_puzzles_differ_and_use_the_real_signs():
    exprs = {ch.math_puzzle(2, random.Random(s)).view["expr"] for s in range(50)}
    assert len(exprs) > 40
    assert all("-" not in e and "*" not in e and "/" not in e for e in exprs)


@pytest.mark.parametrize("level,size,lit,ordered", [(0, 4, 4, False), (1, 5, 6, False), (2, 5, 8, True)])
def test_memory_levels(level, size, lit, ordered):
    for seed in range(30):
        p = ch.memory_puzzle(level, random.Random(seed))
        v = p.view
        assert (v["size"], len(v["lit"]), v["ordered"]) == (size, lit, ordered)
        assert len(set(v["lit"])) == lit and all(0 <= c < size * size for c in v["lit"])
        assert ordered or v["lit"] == sorted(v["lit"])
        assert "answer" not in v
    orders = {tuple(ch.memory_puzzle(2, random.Random(s)).view["lit"]) for s in range(10)}
    assert len(orders) == 10


def test_sorting_easy_is_six_different_whole_numbers():
    for seed in range(30):
        v = ch.sort_puzzle(0, random.Random(seed)).view["items"]
        nums = [int(t) for t in v]
        assert len(nums) == 6 and len(set(nums)) == 6 and all(1 <= n <= 99 for n in nums)
        assert nums != sorted(nums)


def test_sorting_medium_is_eight_numbers_some_with_a_decimal():
    for seed in range(30):
        items = ch.sort_puzzle(1, random.Random(seed)).view["items"]
        values = [Fraction(t) for t in items]
        assert len(items) == 8 and len(set(values)) == 8 and all(-20 <= v <= 99 for v in values)
        decimals = [t for t in items if "." in t]
        assert 2 <= len(decimals) <= 5 and len(decimals) < 8
        assert all(re.fullmatch(r"-?\d+\.[1-9]", t) for t in decimals)


def test_sorting_hard_mixes_fractions_decimals_percents_and_negatives():
    for seed in range(60):
        items = ch.sort_puzzle(2, random.Random(seed)).view["items"]
        values = [as_fraction(t) for t in items]
        assert len(items) == 10 and len(set(values)) == 10 and all(-3 <= v <= 3 for v in values)
        fractions_ = [t for t in items if "/" in t]
        assert fractions_ and all(int(t.split("/")[1]) in (2, 4, 5, 8) for t in fractions_)
        assert any(re.fullmatch(r"-?\d+\.[1-9]", t) for t in items)
        assert any(re.fullmatch(r"-?\d+%", t) for t in items)
        assert any(t.startswith("-") for t in items)


def test_a_sorting_puzzle_is_never_shown_already_in_order():
    for level in range(3):
        for seed in range(40):
            p = ch.sort_puzzle(level, random.Random(seed))
            values = [as_fraction(t) for t in p.view["items"]]
            assert values != sorted(values)


def test_the_phrase_puzzle_is_todays_phrase():
    p = ch.phrase_puzzle(60, random.Random(2))
    assert len(p.view["phrase"].replace(" ", "")) == 60 and all(len(g) == 5 for g in p.view["phrase"].split())
    q = ch.phrase_puzzle(40, random.Random(2), complex=True)
    assert any(c.isdigit() for c in q.view["phrase"]) or any(c.isupper() for c in q.view["phrase"])
    g = ch.phrase_puzzle(30, random.Random(2), grid=True)
    assert "phrase" not in g.view and len(g.view["grid_words"]) == 6
    assert ch.phrase_puzzle(30, random.Random(2), own="my own words").view["phrase"] == "my own words"
    assert len(ch.phrase_puzzle(30, random.Random(2), own="   ").view["phrase"].replace(" ", "")) == 30   # blank = none


# ---------------------------------------------------------------- checkers

def test_math_answer_is_one_whole_number_nothing_else():
    p = ch.math_puzzle(0, random.Random(5))
    right = p.answer
    for good in (str(right), f"  {right} ", f"0{right}", right):
        assert p.check(good), good
    for bad in (str(right + 1), f"{right}.0", f"-{right}", f"+{right}", f"{right} ", "", "abc", f"{right}x", None,
                [right], right + 0.5, True, "1" * 40, f"{right},0", "٣"):
        assert not p.check(bad) or bad == f"{right} ", bad
    assert not p.check(None)


def test_memory_any_order_unless_ordered_and_exactly_the_lit_cells():
    easy = ch.memory_puzzle(0, random.Random(3))
    lit = easy.view["lit"]
    assert easy.check(list(lit)) and easy.check(list(reversed(lit)))
    assert not easy.check(lit[:-1]) and not easy.check(lit + [lit[0]])
    other = next(c for c in range(16) if c not in lit)
    assert not easy.check([lit[0]] * len(lit)) and not easy.check([*lit[:-1], other])
    assert not easy.check("1234") and not easy.check(None) and not easy.check([str(c) for c in lit])
    assert not easy.check([*map(float, lit)]) and not easy.check([True, 1, 2, 3])
    hard = ch.memory_puzzle(2, random.Random(3))
    seq = hard.view["lit"]
    assert hard.check(list(seq)) and not hard.check(sorted(seq)) and not hard.check(list(reversed(seq)))
    assert not hard.check(seq[:-1])


def test_sorting_right_is_every_item_once_smallest_to_largest():
    p = ch.sort_puzzle(1, random.Random(4))
    values = [as_fraction(t) for t in p.view["items"]]
    right = sorted(range(8), key=lambda i: values[i])
    assert p.check(right) and p.answer == right
    assert not p.check(list(reversed(right))) and not p.check(right[:-1]) and not p.check([*right[:-1], right[0]])
    assert not p.check([*right, 0]) and not p.check([i + 8 for i in right]) and not p.check("12345678")
    swapped = [*right]
    swapped[0], swapped[1] = swapped[1], swapped[0]
    assert not p.check(swapped)


def test_view_never_carries_the_answer_or_solution():
    run, _ = make_run([step(MATH, 3), step(MEMORY, 2), step(SORT, 2), step(PHRASE, 30)])
    for _ in range(4):
        text = repr(run.view())
        assert "answer" not in text and "solution" not in text
        run._clock.wait()
        run.answer(solve(run.view()))


# ---------------------------------------------------------------- step lists: strict for a save, lenient for stored data

def test_default_lists():
    assert ch.default_steps(120) == [{"id": "s0", "kind": "phrase", "level": 120, "times": 1}]
    assert ch.default_steps(5) == [{"id": "s0", "kind": "phrase", "level": 30, "times": 1}]      # (clamped into range)
    assert ch.default_steps("junk")[0]["level"] == 60
    assert ch.default_bedtime_steps() == [{"id": "b0", "kind": "math", "level": 0, "times": 2}]


def test_validate_accepts_a_good_list_and_keeps_only_the_four_keys():
    rows = [{"id": "a-1", "kind": "math", "level": 2, "times": 5, "junk": 1}, step(PHRASE, 60, id="p_1")]
    assert ch.validate_steps(rows) == [{"id": "a-1", "kind": "math", "level": 2, "times": 5},
                                       {"id": "p_1", "kind": "phrase", "level": 60, "times": 1}]
    assert ch.validate_steps([{**step(MATH, 1), "level": 1.0, "times": 3.0}])[0]["times"] == 3


@pytest.mark.parametrize("bad", [
    [], None, "x", {}, [1], [step(MATH, 4)], [step(MATH, -1)], [step(MEMORY, 3)], [step(SORT, 3)],
    [step(PHRASE, 29)], [step(PHRASE, 2001)], [step(MATH, 0, 0)], [step(MATH, 0, 21)],
    [{**step(MATH, 0), "kind": "captcha"}], [{**step(MATH, 0), "times": 1.5}], [{**step(MATH, 0), "times": True}],
    [{**step(MATH, 0), "level": "2"}], [{"kind": "math", "level": 0, "times": 1}],
    [{**step(MATH, 0), "id": ""}], [{**step(MATH, 0), "id": "x" * 25}], [{**step(MATH, 0), "id": "a b"}],
    [step(MATH, 0, id="same"), step(SORT, 0, id="same")],
    [step(MATH, 0, id=f"i{i}") for i in range(7)],
])
def test_validate_refuses_anything_off_with_a_plain_message(bad):
    with pytest.raises(ValueError) as e:
        ch.validate_steps(bad)
    assert str(e.value) and not str(e.value).startswith("Traceback")


def test_six_steps_and_twenty_times_are_the_limits():
    assert len(ch.validate_steps([step(MATH, 0, 20, id=f"i{i}") for i in range(6)])) == 6


def test_reading_is_lenient():
    stored = [{"id": "a", "kind": "math", "level": 99, "times": 99}, {"id": "b", "kind": "weird", "level": 0, "times": 1},
              "junk", {"id": "c", "kind": "memory", "level": -3, "times": 0}, {"id": "a", "kind": "sort", "level": 1, "times": 2},
              {"kind": "phrase", "level": 5, "times": 1}, {"id": "e", "kind": "math", "level": "x", "times": 1}]
    got = ch.read_steps(stored)
    assert [(s["kind"], s["level"], s["times"]) for s in got] == [(MATH, 3, 20), (MEMORY, 0, 1), (SORT, 1, 2), (PHRASE, 30, 1)]
    assert len({s["id"] for s in got}) == 4 and got[0]["id"] == "a" and got[1]["id"] == "c"
    assert ch.read_steps(stored) == got                                  # (the same every time)
    assert ch.read_steps(None) == ch.read_steps("x") == ch.read_steps([]) == ch.read_steps([1, 2]) == []
    assert len(ch.read_steps([step(MATH, 0, id=f"i{i}") for i in range(9)])) == 6


# ---------------------------------------------------------------- stricter / looser, row by row

LOOSER_TABLE = [
    # (what changes, old list, new list, looser?)
    ("a step is added", [step(MATH, 1, 2, "a")], [step(MATH, 1, 2, "a"), step(SORT, 0, 1, "b")], False),
    ("a step is added first", [step(MATH, 1, 2, "a")], [step(SORT, 0, 1, "b"), step(MATH, 1, 2, "a")], False),
    ("a level is raised", [step(MATH, 1, 2, "a")], [step(MATH, 2, 2, "a")], False),
    ("times raised", [step(MATH, 1, 2, "a")], [step(MATH, 1, 3, "a")], False),
    ("steps are moved", [step(MATH, 1, 2, "a"), step(SORT, 0, 1, "b")], [step(SORT, 0, 1, "b"), step(MATH, 1, 2, "a")], False),
    ("nothing changes", [step(MATH, 1, 2, "a")], [step(MATH, 1, 2, "a")], False),
    ("a step is removed", [step(MATH, 1, 2, "a"), step(SORT, 0, 1, "b")], [step(MATH, 1, 2, "a")], True),
    ("the only step is replaced by a new one", [step(MATH, 1, 2, "a")], [step(MATH, 3, 20, "z")], True),
    ("a level is lowered", [step(MATH, 2, 2, "a")], [step(MATH, 1, 2, "a")], True),
    ("times lowered", [step(MATH, 1, 3, "a")], [step(MATH, 1, 2, "a")], True),
    ("the kind changes (even to a harder-looking one)", [step(MATH, 0, 1, "a")], [step(MEMORY, 2, 20, "a")], True),
    ("one raised and one lowered", [step(MATH, 1, 2, "a"), step(SORT, 1, 1, "b")],
     [step(MATH, 3, 2, "a"), step(SORT, 0, 1, "b")], True),
    ("a raise and a removal", [step(MATH, 1, 2, "a"), step(SORT, 1, 1, "b")], [step(MATH, 3, 9, "a")], True),
    ("a phrase made shorter", [step(PHRASE, 60, 1, "a")], [step(PHRASE, 59, 1, "a")], True),
    ("a phrase made longer", [step(PHRASE, 60, 1, "a")], [step(PHRASE, 61, 1, "a")], False),
]


@pytest.mark.parametrize("what,old,new,looser", LOOSER_TABLE, ids=[t[0] for t in LOOSER_TABLE])
def test_steps_looser_row_by_row(what, old, new, looser):
    assert ch.steps_looser(old, new) is looser


def test_a_phrase_row_counts_the_own_phrase_while_it_is_in_use():
    old, new = [step(PHRASE, 60, 1, "a")], [step(PHRASE, 30, 1, "a")]
    assert ch.steps_looser(old, new)
    assert not ch.steps_looser(old, new, own_old=40, own_new=40)          # (the level isn't what is asked)
    assert ch.steps_looser(old, old, own_old=40, own_new=10)              # a shorter own phrase
    assert not ch.steps_looser(old, old, own_old=10, own_new=40)
    assert not ch.steps_looser(old, old, own_old=40, own_new=0)           # own off: the random 60 is stronger than 40


# ---------------------------------------------------------------- "takes about"

def test_estimates_are_the_spec_numbers_rounded_to_minutes():
    assert ch.step_seconds(step(MATH, 2)) == 20 and ch.step_seconds(step(MEMORY, 1)) == 10
    assert ch.step_seconds(step(SORT, 2)) == 20 and ch.step_seconds(step(MATH, 3)) == 35
    assert ch.step_seconds(step(PHRASE, 60)) == pytest.approx(27)
    assert ch.step_seconds(step(PHRASE, 60), grid=True) == pytest.approx(27 * 1.3)
    assert ch.step_seconds(step(PHRASE, 60), own_length=100) == pytest.approx(43)
    e = ch.estimate([step(MATH, 2, 5), step(MEMORY, 1, 2), step(PHRASE, 60)])
    assert e["seconds"] == 147 and e["text"] == "about 2 min" and not e["long"] and e["note"] == ""
    assert ch.estimate([step(SORT, 0)])["text"] == "under 1 min"
    assert ch.estimate([step(MATH, 3, 20)]) == {"seconds": 700, "text": "about 12 min", "long": True,
                                                "note": "This will take about 12 min."}
    assert ch.estimate([step(MATH, 3, 17)])["long"] is False and ch.estimate([step(MATH, 3, 18)])["long"] is True   # (600 s is not over)


def test_options_tell_an_editor_everything():
    o = ch.options()
    assert [k["id"] for k in o["kinds"]] == ["math", "memory", "sort", "phrase"]
    assert o["max_steps"] == 6 and o["max_times"] == 20 and o["min_times"] == 1
    math = o["kinds"][0]
    assert [lv["label"] for lv in math["levels"]] == ["Easy", "Medium", "Hard", "Very hard"] and math["seconds"] == [6, 10, 20, 35]
    phrase = o["kinds"][3]
    assert phrase["levels"][1] == {"level": 60, "label": "Medium"} and phrase["min_level"] == 30 and phrase["max_level"] == 2000
    assert ch.level_label(PHRASE, 45) == "45 letters" and ch.level_label(PHRASE, 250) == "Very long"
    assert ch.step_label(step(MATH, 2)) == "Math, Hard" and ch.step_label(step(PHRASE, 120)) == "Typed phrase, Long"


# ---------------------------------------------------------------- the Run

def test_a_run_walks_the_steps_and_a_wrong_answer_keeps_what_is_done():
    run, clock = make_run([step(MATH, 0, 2, "m"), step(SORT, 0, 1, "s"), step(MEMORY, 0, 1, "y")])
    v = run.view()
    assert (v["step"], v["done"], v["kind"], v["finished"]) == (0, 0, MATH, False)
    assert [(s["kind"], s["times"], s["label"]) for s in v["steps"]] == [
        (MATH, 2, "Math, Easy"), (SORT, 1, "Sorting, Easy"), (MEMORY, 1, "Memory grid, Easy")]
    clock.wait()
    assert run.answer(solve(v)) == ch.RIGHT and run.view()["done"] == 1
    first = run.view()["expr"]
    clock.wait()
    assert run.answer(wrong_for(run.view())) == ch.WRONG                  # a new puzzle of this step, done stays 1
    after = run.view()
    assert (after["step"], after["done"]) == (0, 1) and after["expr"] != first
    clock.wait()
    assert run.answer(solve(after)) == ch.RIGHT
    v = run.view()
    assert (v["step"], v["done"], v["kind"]) == (1, 0, SORT) and len(v["items"]) == 6
    clock.wait()
    assert run.answer(wrong_for(v)) == ch.WRONG
    v = run.view()
    assert (v["step"], v["done"], v["kind"]) == (1, 0, SORT)
    clock.wait()
    assert run.answer(solve(v)) == ch.RIGHT
    v = run.view()
    assert (v["step"], v["kind"], v["size"], len(v["lit"])) == (2, MEMORY, 4, 4)
    clock.wait()
    assert run.answer(solve(v)) == ch.PASSED
    assert run.finished and run.view()["finished"] is True
    assert run.answer("anything") == ch.PASSED                           # (a finished run stays finished)


def test_a_phrase_that_does_not_match_stays_and_one_that_is_too_fast_starts_its_clock_again():
    run, clock = make_run([step(PHRASE, 30, 2, "p")])
    phrase = run.view()["phrase"]
    clock.wait(0.2)
    assert run.answer(phrase) == ch.TOO_FAST                              # 35 ms a character: 35 characters need 1.225 s
    clock.wait(0.5)
    assert run.answer(phrase) == ch.TOO_FAST                              # (its clock started over)
    clock.wait(2)
    assert run.answer(phrase[:-1] + "!") == ch.MISMATCH and run.view()["phrase"] == phrase
    assert run.answer(None) == ch.MISMATCH and run.answer(["a"]) == ch.MISMATCH
    assert run.answer(phrase) == ch.RIGHT and run.view()["phrase"] != phrase and run.view()["done"] == 1
    clock.wait()
    assert run.answer(run.view()["phrase"]) == ch.PASSED


def test_grid_mode_and_an_own_phrase_apply_to_every_phrase_step():
    run, clock = make_run([step(PHRASE, 30, 1, "p"), step(PHRASE, 60, 1, "q")], grid=True)
    v = run.view()
    assert "phrase" not in v and len(v["grid_words"]) == 6
    clock.wait()
    assert run.answer(v["grid_words"]) == ch.RIGHT and len(run.view()["grid_words"]) == 12
    own, clock = make_run([step(PHRASE, 60, 2, "p")], own="I keep my word every day")
    assert own.view()["phrase"] == "I keep my word every day"
    clock.wait()
    assert own.answer("I keep my word every day") == ch.RIGHT and own.view()["phrase"] == "I keep my word every day"


@pytest.mark.parametrize("kind,level", [(MATH, 0), (MATH, 3), (MEMORY, 0), (MEMORY, 2), (SORT, 0), (SORT, 2)])
def test_a_puzzle_answered_too_fast_counts_as_wrong_and_is_replaced(kind, level):
    run, clock = make_run([step(kind, level, 1, "a")])
    v = run.view()
    clock.wait(0.01)
    assert run.answer(solve(v)) == ch.WRONG and run.last_too_fast is True
    assert run.view() != v and not run.finished
    clock.wait()
    assert run.answer(solve(run.view())) == ch.PASSED and run.last_too_fast is False


def test_the_fastest_time_is_per_puzzle_per_lit_square_and_per_item():
    for kind, level, minimum in [(MATH, 1, 1.2), (MEMORY, 1, 0.15 * 6), (MEMORY, 2, 0.15 * 8), (SORT, 0, 0.15 * 6), (SORT, 2, 0.15 * 10)]:
        run, clock = make_run([step(kind, level)])
        v = run.view()
        clock.wait(minimum - 0.01)
        assert run.answer(solve(v)) == ch.WRONG, (kind, level)
        v = run.view()
        clock.wait(minimum + 0.01)
        assert run.answer(solve(v)) == ch.PASSED, (kind, level)


def test_the_time_counts_from_showing_the_puzzle_not_from_the_start():
    run, clock = make_run([step(MATH, 0, 2, "a")])
    clock.wait(60)
    assert run.answer(solve(run.view())) == ch.RIGHT
    clock.wait(0.5)                                                       # (the second sum was shown just now)
    assert run.answer(solve(run.view())) == ch.WRONG


def test_the_view_is_plain_json_for_the_page_and_the_tray():
    import json
    run, clock = make_run([step(MATH, 3), step(MEMORY, 2), step(SORT, 2), step(PHRASE, 30)], grid=True)
    for _ in range(4):
        json.dumps(run.view())
        clock.wait()
        run.answer(solve(run.view()))
    assert run.finished


def test_a_run_needs_a_step_and_is_independent_of_any_service():
    with pytest.raises(ValueError):
        ch.Run([])
    assert ch.Run([step(MATH, 0)]).view()["kind"] == MATH


# ---------------------------------------------------------------- Anti-Bypass config: migration and looser

def test_a_config_from_before_lists_reads_as_one_phrase_step():
    assert ab.with_defaults({})["steps"] == [{"id": "s0", "kind": "phrase", "level": 60, "times": 1}]
    assert ab.with_defaults({"length": 120, "phrase": True})["steps"] == [{"id": "s0", "kind": "phrase", "level": 120, "times": 1}]
    assert ab.with_defaults({"steps": None, "length": 90})["steps"][0]["level"] == 90
    assert ab.with_defaults({"steps": [], "length": 90})["steps"][0]["level"] == 90
    assert ab.with_defaults({"steps": [{"kind": "x"}], "length": 90})["steps"][0]["level"] == 90
    assert ab.with_defaults({"steps": [step(MATH, 9, 99, "a")]})["steps"] == [step(MATH, 3, 20, "a")]
    assert ab.with_defaults(ab.with_defaults({"length": 45})) == ab.with_defaults({"length": 45})


def test_an_old_stored_config_is_migrated_and_stays_unwritten_until_edited(tmp_path):
    db = Database(tmp_path / "t.db")
    db.set_setting(ab.SETTINGS_KEY, '{"phrase": true, "length": 90, "grid": true, "custom_phrase": "my words"}')
    cfg = ab.settings(db)
    assert cfg["steps"] == [{"id": "s0", "kind": "phrase", "level": 90, "times": 1}] and cfg["phrase"] and cfg["grid"]
    ab.unlock(db, NOW)                                                     # (a save that never touched the steps)
    assert "steps" not in db.get_setting(ab.SETTINGS_KEY)
    ab.save(db, {**ab.settings(db), "length": 45})                         # a length edit: the one phrase follows it
    assert "steps" not in db.get_setting(ab.SETTINGS_KEY) and ab.settings(db)["steps"][0]["level"] == 45
    ab.save(db, {**ab.settings(db), "steps": [step(MATH, 2, 5, "a"), step(PHRASE, 45, 1, "b")]})
    assert [s["id"] for s in ab.settings(db)["steps"]] == ["a", "b"] and "steps" in db.get_setting(ab.SETTINGS_KEY)


def test_old_backups_read_as_the_one_step_list(tmp_path):
    old = {"lockdown": 1, "items": [], "settings": {"antibypass": '{"phrase": true, "length": 120, "complex": true}'}}
    db = Database(tmp_path / "t.db")
    backup.restore(db, old)
    cfg = ab.settings(db)
    assert cfg["steps"] == [{"id": "s0", "kind": "phrase", "level": 120, "times": 1}] and cfg["complex"]
    assert ab.status(cfg, NOW) == "phrase"
    # the review of an import compares as it is read: a backup written before lists and the same config now are one
    now_text = '{"complex": true, "length": 120, "phrase": true, "steps": [{"id": "s0", "kind": "phrase", "level": 120, "times": 1}]}'
    assert backup._norm("antibypass", old["settings"]["antibypass"]) == backup._norm("antibypass", now_text)
    # and a backup written with steps round-trips
    db.set_setting(ab.SETTINGS_KEY, '{"phrase": true, "steps": [{"id": "a", "kind": "math", "level": 2, "times": 5}]}')
    other = Database(tmp_path / "u.db")
    backup.restore(other, backup.export(db))
    assert ab.settings(other)["steps"] == [step(MATH, 2, 5, "a")]


def phrase_cfg(steps=None, **kw):
    cfg = {**ab.DEFAULTS, "phrase": True, "length": 60, **kw}
    return ab.with_defaults({**cfg, **({"steps": steps} if steps else {})})


MIXED = [step(MATH, 2, 5, "a"), step(MEMORY, 1, 2, "b"), step(PHRASE, 60, 1, "c")]


def test_settings_looser_keeps_every_check_that_exists_today():
    base = phrase_cfg()
    assert ab.settings_looser(base, {**base, "phrase": False})                        # challenge off
    assert ab.settings_looser(base, ab.with_defaults({**base, "length": 30, "steps": None}))   # a shorter phrase
    assert not ab.settings_looser(base, ab.with_defaults({**base, "length": 90, "steps": None}))
    assert ab.settings_looser({**base, "wait_min": 10}, {**base, "wait_min": 5})      # less waiting
    assert ab.settings_looser({**base, "hours": True}, {**base, "hours": False})      # hours off
    assert ab.settings_looser({**base, "grid": True}, {**base, "grid": False})        # grid off
    assert ab.settings_looser({**base, "complex": True}, {**base, "complex": False})  # capitals off
    assert ab.settings_looser(base, {**base, "custom_phrase": "short", "use_own": True})   # own phrase switched on
    assert not ab.settings_looser({**base, "phrase": False}, {**base, "phrase": False, "steps": [step(MATH, 0, 1, "q")]})


@pytest.mark.parametrize("what,old,new,looser", LOOSER_TABLE, ids=[t[0] for t in LOOSER_TABLE])
def test_anti_bypass_compares_the_lists_while_the_challenge_was_on(what, old, new, looser):
    assert ab.settings_looser(phrase_cfg(old), phrase_cfg(new)) is looser
    assert not ab.settings_looser(phrase_cfg(old, phrase=False), phrase_cfg(new, phrase=False))   # (not asked: free)


def test_a_phrase_row_in_a_list_counts_the_own_phrase_like_today():
    base = phrase_cfg(MIXED)
    own = {"custom_phrase": "I keep my promises to myself, every evening", "use_own": True}
    assert ab.settings_looser(base, phrase_cfg(MIXED, **own))                        # switched on: a known phrase
    assert not ab.settings_looser(phrase_cfg(MIXED, **own), base)                    # off again: a fresh random 60
    shorter = [*MIXED[:2], step(PHRASE, 30, 1, "c")]
    assert ab.settings_looser(base, phrase_cfg(shorter))
    assert not ab.settings_looser(phrase_cfg(MIXED, **own), phrase_cfg(shorter, **own))   # (the level isn't asked while yours is)
    assert ab.settings_looser(phrase_cfg(MIXED, **own), phrase_cfg(MIXED, custom_phrase="x" * 10, use_own=True))


def test_extras_stay_global_and_loosen_even_without_a_phrase_step():
    no_phrase = [step(MATH, 2, 5, "a")]
    assert ab.settings_looser(phrase_cfg(no_phrase, grid=True), phrase_cfg(no_phrase, grid=False))    # stricter is safer
    assert ab.settings_looser(phrase_cfg(no_phrase, complex=True), phrase_cfg(no_phrase, complex=False))


# ---------------------------------------------------------------- bedtime: which list, and when changing it loosens

NIGHT = {**reminders.DEFAULT_SLEEP, "on": True, "bedtime": "22:00", "wake": "07:00", "guarded": True,
         "challenge": True, "challenge_steps": [step(MATH, 0, 2, "g")],
         "tiers": [{"from": "22:00", "every_sec": 900},
                   {"from": "00:00", "every_sec": 300, "challenge_steps": [step(MATH, 1, 3, "t1"), step(SORT, 0, 1, "t2")]},
                   {"from": "02:00", "every_sec": 60, "challenge_steps": [step(MEMORY, 2, 1, "t3")]}]}
BED = datetime(2026, 10, 13, 22, 0)


def test_sleep_defaults_carry_the_challenge_keys(tmp_path):
    d = reminders.DEFAULT_SLEEP
    assert d["challenge"] is False and d["challenge_complex"] is False and d["challenge_grid"] is False
    assert d["challenge_steps"] == [{"id": "b0", "kind": "math", "level": 0, "times": 2}]
    assert reminders.load(Database(tmp_path / "t.db"), reminders.SLEEP_KEY, d)["challenge"] is False


def test_stored_steps_keep_their_own_lists_through_loading(tmp_path):
    db = Database(tmp_path / "t.db")
    reminders.save(db, reminders.SLEEP_KEY, {"tiers": [{"from": "21:00", "every": 15, "challenge_steps": [step(MATH, 1, 2, "o")]},
                                                        {"from": "00:00", "every": 5}]})
    got = reminders.load(db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert [(t["from"], t["every_sec"]) for t in got["tiers"]] == [("21:00", 900), ("00:00", 300)]
    assert got["tiers"][0]["challenge_steps"] == [step(MATH, 1, 2, "o")] and "challenge_steps" not in got["tiers"][1]


def test_the_list_is_the_one_of_the_step_active_when_the_screen_came_up():
    at = lambda h, m=0, day=13: reminders.challenge_steps_at(NIGHT, BED, datetime(2026, 10, day, h, m))   # noqa: E731
    assert [s["id"] for s in at(22, 10)] == ["g"]                       # 22:00 step has no list of its own: the general one
    assert [s["id"] for s in at(23, 59)] == ["g"]
    assert [s["id"] for s in at(0, 30, 14)] == ["t1", "t2"]             # 00:00 step: its own list
    assert [s["id"] for s in at(1, 59, 14)] == ["t1", "t2"]
    assert [s["id"] for s in at(2, 0, 14)] == ["t3"]                    # 02:00 step: its own list
    assert [s["id"] for s in at(6, 59, 14)] == ["t3"]


def test_before_the_first_step_and_with_no_steps_the_general_list_applies():
    late_start = {**NIGHT, "tiers": [{"from": "23:00", "every_sec": 300, "challenge_steps": [step(SORT, 1, 1, "z")]}]}
    assert [s["id"] for s in reminders.challenge_steps_at(late_start, BED, datetime(2026, 10, 13, 22, 20))] == ["g"]
    assert [s["id"] for s in reminders.challenge_steps_at(late_start, BED, datetime(2026, 10, 13, 23, 20))] == ["z"]
    assert [s["id"] for s in reminders.challenge_steps_at({**NIGHT, "tiers": []}, BED, datetime(2026, 10, 14, 3, 0))] == ["g"]
    # an empty / null / unreadable own list is "uses the general list"
    for own in (None, [], "junk", [{"kind": "nope"}]):
        s = {**NIGHT, "tiers": [{"from": "22:00", "every_sec": 60, "challenge_steps": own}]}
        assert [x["id"] for x in reminders.challenge_steps_at(s, BED, datetime(2026, 10, 13, 23, 0))] == ["g"]


def test_off_gives_no_list_and_a_missing_general_list_reads_as_the_default():
    assert reminders.challenge_steps_at({**NIGHT, "challenge": False}, BED, datetime(2026, 10, 14, 3, 0)) == []
    assert reminders.challenge_steps_at({**NIGHT, "challenge_steps": None, "tiers": []}, BED, datetime(2026, 10, 13, 23, 0)) \
        == ch.default_bedtime_steps()
    assert reminders.challenge_steps_now(NIGHT, datetime(2026, 10, 14, 0, 30))[0]["id"] == "t1"
    assert reminders.challenge_steps_now(NIGHT, datetime(2026, 10, 13, 15, 0))[0]["id"] == "g"    # (outside a night: general)


def test_a_bedtime_after_midnight_places_the_steps_like_the_wait_does():
    s = {**NIGHT, "bedtime": "01:00", "wake": "08:00",
         "tiers": [{"from": "23:00", "every_sec": 900, "challenge_steps": [step(SORT, 0, 1, "early")]},
                   {"from": "03:00", "every_sec": 60}]}
    bed = datetime(2026, 10, 14, 1, 0)
    assert [x["id"] for x in reminders.challenge_steps_at(s, bed, datetime(2026, 10, 14, 1, 30))] == ["early"]
    assert [x["id"] for x in reminders.challenge_steps_at(s, bed, datetime(2026, 10, 14, 3, 30))] == ["g"]


def test_a_run_for_the_bedtime_screen_uses_the_list_and_the_extras():
    s = {**NIGHT, "challenge_grid": True, "tiers": [{"from": "22:00", "every_sec": 900, "challenge_steps": [step(PHRASE, 30, 1, "p")]}]}
    run = reminders.challenge_run(s, BED, datetime(2026, 10, 13, 23, 0), rng=random.Random(1), clock=Clock())
    assert run.view()["kind"] == PHRASE and "grid_words" in run.view()
    assert reminders.challenge_run({**NIGHT, "challenge": False}, BED, datetime(2026, 10, 13, 23, 0)) is None


def looser(change, old=NIGHT):
    new = {**old, **change}
    return reminders.loosens_reminder(old, new)


def edited_tier(index, steps):
    tiers = [dict(t) for t in NIGHT["tiers"]]
    tiers[index] = {**tiers[index], "challenge_steps": steps}
    return {"tiers": tiers}


@pytest.mark.parametrize("what,change,expected", [
    ("challenge turned off", {"challenge": False}, True),
    ("a general step raised", {"challenge_steps": [step(MATH, 1, 2, "g")]}, False),
    ("a general step lowered, seen in the 22:00 to 00:00 stretch", {"challenge_steps": [step(MATH, 0, 1, "g")]}, True),
    ("a general step added", {"challenge_steps": [step(MATH, 0, 2, "g"), step(SORT, 0, 1, "n")]}, False),
    ("the general list moved / reordered", {"challenge_steps": [step(SORT, 0, 1, "n"), step(MATH, 0, 2, "g")]}, False),
    ("the general step removed (another list put there)", {"challenge_steps": [step(MATH, 3, 20, "other")]}, True),
    ("the general kind changed", {"challenge_steps": [step(MEMORY, 2, 20, "g")]}, True),
    ("a Comes back step's own list raised", edited_tier(1, [step(MATH, 2, 3, "t1"), step(SORT, 0, 1, "t2")]), False),
    ("a Comes back step's own list lowered (02:00)", edited_tier(2, [step(MEMORY, 1, 1, "t3")]), True),
    ("a Comes back step's own list dropped: the lower general one applies", edited_tier(1, None), True),
    ("a Comes back step's own list lowered (00:00)", edited_tier(1, [step(MATH, 1, 2, "t1"), step(SORT, 0, 1, "t2")]), True),
    ("a Comes back step's own list gets a new step", edited_tier(1, [step(MATH, 1, 3, "t1"), step(SORT, 0, 1, "t2"), step(SORT, 2, 2, "t9")]), False),
    ("a time moved so that another list applies at 02:00", {"tiers": [NIGHT["tiers"][0], NIGHT["tiers"][1],
                                                                       {**NIGHT["tiers"][2], "from": "04:00"}]}, True),
    ("a time moved earlier: another list applies for an hour", {"tiers": [NIGHT["tiers"][0], NIGHT["tiers"][1],
                                                                  {**NIGHT["tiers"][2], "from": "01:00"}]}, True),
    ("a step with its own list removed", {"tiers": [NIGHT["tiers"][0], NIGHT["tiers"][1]]}, True),
    ("the wake time earlier, so the last stretch has no screen", {"wake": "06:00"}, False),
    ("other bedtime settings", {"dismiss_wait": 0, "warn_text": "go"}, False),
])
def test_bedtime_challenge_looser_while_important(what, change, expected):
    assert looser(change) is expected, what


def test_bedtime_phrase_extras_loosen_only_with_a_phrase_step():
    with_phrase = {**NIGHT, "challenge_steps": [step(PHRASE, 30, 1, "p"), step(MATH, 0, 2, "g")], "challenge_complex": True,
                   "challenge_grid": True}
    assert looser({"challenge_complex": False}, with_phrase) and looser({"challenge_grid": False}, with_phrase)
    own_phrase = {**NIGHT, "tiers": [{"from": "22:00", "every_sec": 60, "challenge_steps": [step(PHRASE, 30, 1, "p")]}],
                  "challenge_grid": True}
    assert looser({"challenge_grid": False}, own_phrase)                           # (a phrase in a step's own list counts)
    no_phrase = {**NIGHT, "challenge_complex": True, "challenge_grid": True, "tiers": []}
    assert not looser({"challenge_complex": False, "challenge_grid": False}, no_phrase)
    assert not looser({"challenge_complex": True, "challenge_grid": True}, {**with_phrase, "challenge_complex": False})


def test_bedtime_challenge_edits_are_free_unless_the_old_bedtime_is_important():
    calm = {**NIGHT, "guarded": False}
    assert not looser({"challenge": False}, calm) and not looser({"challenge_steps": [step(MATH, 0, 1, "g")]}, calm)
    assert not looser(edited_tier(2, [step(MEMORY, 0, 1, "t3")]), calm)
    off = {**NIGHT, "challenge": False}
    assert not looser({"challenge": True, "challenge_steps": [step(MATH, 0, 1, "g")]}, off)       # turning it on is free
    assert not looser({"challenge_steps": [step(MATH, 0, 1, "x")]}, off)                            # a list while it is off: free


def test_one_save_with_any_looser_edit_asks_once_and_a_raise_next_to_it_does_not_help():
    new = {**NIGHT, "challenge_steps": [step(MATH, 3, 9, "g")], **edited_tier(2, [step(MEMORY, 0, 1, "t3")])}
    assert reminders.loosens_reminder(NIGHT, new)


# ---------------------------------------------------------------- GuardService over a three-step list

@pytest.fixture(autouse=True)
def _log(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "LOG_PATH", tmp_path / "lockdown.log")


class GuardClock:
    """Trusted time and the monotonic clock, both set by the test (as test_rebuild_guard does)."""

    def __init__(self):
        self.now, self.mono = NOW, 1000.0

    def __call__(self, db=None):
        return self.now

    def tick(self):
        return self.mono


class Change:
    def __init__(self):
        self.ran = self.cancelled = 0

    def action(self):
        self.ran += 1
        return {"saved": self.ran}

    def cancel(self):
        self.cancelled += 1


THREE = [step(MATH, 0, 2, "m"), step(SORT, 0, 1, "s"), step(MEMORY, 0, 1, "y")]


@pytest.fixture
def env(tmp_path):
    clock = GuardClock()
    ctx = webapi.make_context(tmp_path / "t.db", clock=clock)
    ctx.guard = GuardService(lambda: ctx.db, clock, monotonic=clock.tick, rng=random.Random(7))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "steps": THREE})
    ctx.clock = clock
    ctx.api = core.build_api(ctx)
    return ctx


def open_it(env, change=None):
    change = change or Change()
    ticket = env.guard.request(["Remove YouTube"], change.action, change.cancel)["data"]["ticket"]
    return change, ticket, env.guard.begin(ticket)["data"]


def human(env, seconds=30):
    env.clock.mono += seconds


def test_three_steps_are_done_in_order_and_only_the_last_one_makes_the_change(env):
    change, ticket, shown = open_it(env)
    assert shown["mode"] == MATH and shown["find_x"] is False and shown["expr"]
    run = shown["run"]
    assert (run["step"], run["done"], run["finished"]) == (0, 0, False)
    assert [(s["kind"], s["times"]) for s in run["steps"]] == [(MATH, 2), (SORT, 1), (MEMORY, 1)]
    assert "phrase" not in shown and "answer" not in repr(shown)
    seen = []
    while True:
        human(env)
        got = env.guard.submit(ticket, solve(shown["run"]))
        assert got["ok"] is True
        if got["data"]["passed"]:
            break
        assert change.ran == 0 and ab.status(ab.settings(env.db), NOW) == "phrase"      # nothing is made, nothing unlocked
        shown = got["data"]["next"]
        seen.append((shown["mode"], shown["run"]["step"], shown["run"]["done"]))
    assert seen == [(MATH, 0, 1), (SORT, 1, 0), (MEMORY, 2, 0)]
    assert shown["size"] == 4 and len(shown["lit"]) == 4 and shown["ordered"] is False
    assert got["data"]["result"] == {"saved": 1} and change.ran == 1
    assert ab.status(ab.settings(env.db), NOW) == "free"              # the unlock opened
    assert env.guard.submit(ticket, "x")["ok"] is False               # (one use)


def test_a_wrong_answer_gives_a_new_puzzle_and_keeps_what_is_done(env):
    change, ticket, shown = open_it(env)
    human(env)
    shown = env.guard.submit(ticket, solve(shown["run"]))["data"]["next"]
    assert shown["run"]["done"] == 1
    human(env)
    got = env.guard.submit(ticket, wrong_for(shown["run"]))
    assert got["ok"] is True and got["data"]["wrong"] is True and "new one" in got["data"]["message"]
    after = got["data"]["next"]
    assert after["run"]["step"] == 0 and after["run"]["done"] == 1 and after["expr"] != shown["expr"]
    assert change.ran == 0
    human(env)
    assert env.guard.submit(ticket, solve(after["run"]))["data"]["next"]["mode"] == SORT


def test_too_fast_and_junk_are_wrong_answers_not_errors(env):
    change, ticket, shown = open_it(env)
    got = env.guard.submit(ticket, solve(shown["run"]))                # (answered at once)
    assert got["ok"] is True and got["data"]["wrong"] is True and "Too fast" in got["data"]["message"]
    shown = got["data"]["next"]
    human(env)
    for junk in (None, [], {"a": 1}, "abc", "", [1, 2], 3.5, True):
        got = env.guard.submit(ticket, junk)
        assert got["ok"] is True and got["data"]["wrong"] is True, junk
        human(env)
    assert change.ran == 0


def test_the_app_log_keeps_failed_puzzles_and_not_every_right_answer(env):
    import applog
    change, ticket, shown = open_it(env)
    human(env)
    shown = env.guard.submit(ticket, solve(shown["run"]))["data"]["next"]
    human(env)
    env.guard.submit(ticket, wrong_for(shown["run"]))
    events = applog.query(env.db.conn, limit=100)
    failed = [e["text"] for e in events if e["kind"] == "challenge_failed"]
    assert len(failed) == 1 and "wrong answer in Math, Easy" in failed[0] and "Remove YouTube" in failed[0]
    assert not [e for e in events if e["kind"] == "challenge_passed"]


def test_cancel_drops_the_run_and_the_next_one_starts_over(env):
    change, ticket, shown = open_it(env)
    human(env)
    env.guard.submit(ticket, solve(shown["run"]))
    pending = env.guard._open
    assert pending.run.done == 1
    env.guard.cancel(ticket)
    assert change.cancelled == 1 and pending.run is None
    assert env.guard.submit(ticket, "1")["ok"] is False
    again = Change()
    t2 = env.guard.request(["x"], again.action, again.cancel)["data"]["ticket"]
    fresh = env.guard.begin(t2)["data"]
    assert fresh["run"]["step"] == 0 and fresh["run"]["done"] == 0


def test_an_expired_or_replaced_ticket_drops_its_run(env):
    change, ticket, shown = open_it(env)
    pending = env.guard._open
    env.clock.mono += guard.TICKET_TTL_SEC + 1
    assert env.guard.submit(ticket, "1")["ok"] is False and pending.run is None and change.cancelled == 1
    first, t1, _ = open_it(env)
    pending = env.guard._open
    env.guard.request(["another"], Change().action)
    assert pending.run is None and first.cancelled == 1


def test_begin_again_starts_a_fresh_run(env):
    change, ticket, shown = open_it(env)
    human(env)
    env.guard.submit(ticket, solve(shown["run"]))
    again = env.guard.begin(ticket)["data"]
    assert again["run"]["done"] == 0 and again["run"]["step"] == 0


def test_a_phrase_step_keeps_todays_replies(env):
    ab.save(env.db, {**ab.settings(env.db), "steps": [step(PHRASE, 30, 1, "p")], "grid": False})
    change, ticket, shown = open_it(env)
    assert shown["mode"] == "phrase" and shown["length"] == len(shown["phrase"]) and shown["run"]["kind"] == PHRASE
    human(env, 0.0)
    assert env.guard.submit(ticket, shown["phrase"])["error"] == "Too fast - type it yourself."
    human(env)
    got = env.guard.submit(ticket, shown["phrase"][:-1] + "!")
    assert got["ok"] is False and got["error"] == "That doesn't match - try again."
    assert env.guard.submit(ticket, shown["phrase"])["data"]["passed"] is True and change.ran == 1


def test_a_phrase_in_the_middle_of_a_list_and_in_grid_mode(env):
    ab.save(env.db, {**ab.settings(env.db), "steps": [step(MATH, 0, 1, "m"), step(PHRASE, 30, 1, "p")], "grid": True})
    change, ticket, shown = open_it(env)
    human(env)
    shown = env.guard.submit(ticket, solve(shown["run"]))["data"]["next"]
    assert shown["mode"] == "grid" and len(shown["grid_words"]) == 6 and shown["length"] == 35
    human(env)
    assert env.guard.submit(ticket, ["x"] * 6)["ok"] is False
    assert env.guard.submit(ticket, shown["grid_words"])["data"]["passed"] is True


def test_a_wait_and_a_dry_run_work_at_the_end_of_a_list(env):
    ab.save(env.db, {**ab.settings(env.db), "steps": [step(MATH, 0, 1, "m")], "wait_min": 10})
    change, ticket, shown = open_it(env)
    human(env)
    got = env.guard.submit(ticket, solve(shown["run"]))["data"]
    assert got["passed"] is True and got["waiting_until"] and change.ran == 0 and change.cancelled == 1
    ab.save(env.db, {**ab.settings(env.db), "wait_min": 0, "unlocked_from": None, "unlocked_until": None})
    dry = Change()
    t = env.guard.request(["Try"], dry.action, dry.cancel, dry=True)["data"]["ticket"]
    shown = env.guard.begin(t)["data"]
    human(env)
    got = env.guard.submit(t, solve(shown["run"]))["data"]
    assert got["passed"] is True and got["dry"] is True and dry.ran == 1
    assert ab.unlocked_until(ab.settings(env.db), NOW) is None


def test_waiting_and_closed_modes_have_no_run(env):
    ab.save(env.db, {**ab.settings(env.db), "hours": True, "windows": [{"days": [0], "start": "18:00", "end": "20:00"}]})
    _, ticket, shown = open_it(env)                                     # (Tuesday: outside Monday's hours)
    assert shown["mode"] == "closed" and "run" not in shown
    assert env.guard.submit(ticket, "1")["ok"] is False


def test_the_standalone_window_needs_no_new_api_method():
    from resident import challenge_cli
    assert challenge_cli.STANDALONE_METHODS[:3] == ("challenge_begin", "challenge_submit", "challenge_cancel")
    assert {n for n, _ in webapi.api_methods() if n.startswith("challenge_")} == {"challenge_begin", "challenge_submit",
                                                                                  "challenge_cancel"}


# ---------------------------------------------------------------- the Anti-Bypass page saves lists strictly

@pytest.fixture
def page_env(tmp_path):
    clock = GuardClock()
    ctx = webapi.make_context(tmp_path / "t.db", clock=clock)
    ctx.guard = GuardService(lambda: ctx.db, clock, monotonic=clock.tick, rng=random.Random(3))
    ctx.clock = clock
    ctx.api = core.build_api(ctx)
    return ctx


def api_data(reply):
    assert reply["ok"], reply
    return reply["data"]


def test_the_page_data_has_the_list_the_kinds_and_the_estimate(page_env):
    d = api_data(page_env.api.antibypass_get())
    assert d["config"]["steps"] == [{"id": "s0", "kind": "phrase", "level": 60, "times": 1}]
    assert [k["id"] for k in d["challenge"]["kinds"]] == ["math", "memory", "sort", "phrase", "password", "totp"]
    assert d["challenge"]["max_steps"] == 6 and d["challenge"]["estimate"]["text"] == "under 1 min"
    ab.save(page_env.db, {**ab.settings(page_env.db), "steps": MIXED, "grid": True})
    d = api_data(page_env.api.antibypass_get())
    assert d["config"]["steps"] == MIXED and d["challenge"]["estimate"]["seconds"] == 100 + 20 + round(27 * 1.3)


def test_a_stricter_list_is_saved_after_a_yes_and_a_looser_one_asks_the_challenge(page_env):
    api, ctx = page_env.api, page_env
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "steps": MIXED})
    more = [*MIXED, step(SORT, 2, 3, "d")]
    asked = api_data(api.antibypass_save({"steps": more}))
    assert asked["needs_confirm"] and "Math (Hard) \u00d75" in asked["line"] and "Sorting (Hard) \u00d73" in asked["line"]
    assert ab.settings(ctx.db)["steps"] == MIXED
    assert api_data(api.antibypass_save({"steps": more}, True))["config"]["steps"] == more
    # moving the steps about is not a change of what is asked: saved at once
    moved = [more[1], more[0], more[2], more[3]]
    assert api_data(api.antibypass_save({"steps": moved}))["config"]["steps"] == moved
    # a lowered level / a removed step / fewer times asks the challenge, whatever `confirmed` says
    for looser_list in ([more[0], more[1], more[2]], [more[0], {**more[1], "level": 0}, more[2], more[3]],
                        [more[0], more[1], more[2], {**more[3], "times": 2}]):
        for confirmed in (False, True):
            out = api_data(api.antibypass_save({"steps": looser_list}, confirmed))
            assert out["needs_challenge"] and out["ticket"], (looser_list, confirmed)
            api.challenge_cancel(out["ticket"])
            assert ab.settings(ctx.db)["steps"] == moved


def test_the_ticket_saves_the_looser_list_once_the_challenge_is_passed(page_env):
    api, ctx = page_env.api, page_env
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "steps": [step(MATH, 0, 2, "a"), step(SORT, 0, 1, "b")]})
    out = api_data(api.antibypass_save({"steps": [step(MATH, 0, 2, "a")]}))
    ticket = out["ticket"]
    shown = api_data(api.challenge_begin(ticket))
    while True:
        ctx.clock.mono += 30
        got = api_data(api.challenge_submit(ticket, solve(shown["run"])))
        if got["passed"]:
            break
        shown = got["next"]
    assert [s["id"] for s in ab.settings(ctx.db)["steps"]] == ["a"]


@pytest.mark.parametrize("bad", [
    [], "x", None, [step(MATH, 9, 1, "a")], [step(MATH, 0, 99, "a")], [step(MATH, 0, 0, "a")],
    [step(MATH, 0, 1, "a"), step(SORT, 0, 1, "a")], [step(MATH, 0, 1, f"i{i}") for i in range(7)],
    [{"kind": "math", "level": 0, "times": 1}], [{**step(MATH, 0, 1, "a"), "kind": "paste"}],
])
def test_a_list_that_is_off_is_refused_and_nothing_changes(page_env, bad):
    ab.save(page_env.db, {**ab.settings(page_env.db), "phrase": True, "steps": MIXED})
    r = page_env.api.antibypass_save({"steps": bad})
    assert r["ok"] is False and r["error"]
    r = page_env.api.antibypass_save({"steps": bad}, True)
    assert r["ok"] is False
    assert ab.settings(page_env.db)["steps"] == MIXED


def test_a_page_that_knows_only_a_length_still_works(page_env):
    api, ctx = page_env.api, page_env
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 60})
    out = api_data(api.antibypass_save({"length": 30}))
    assert out["needs_challenge"]
    api.challenge_cancel(out["ticket"])
    out = api_data(api.antibypass_save({"length": 90}))
    assert out["needs_confirm"]
    saved = api_data(api.antibypass_save({"length": 90}, True))
    assert saved["config"]["length"] == 90 and saved["config"]["steps"] == [{"id": "s0", "kind": "phrase", "level": 90, "times": 1}]


def test_the_confirm_line_for_one_phrase_is_what_it_was(page_env):
    from webapi import antibypass_page as page
    assert page.describe(ab.with_defaults({"phrase": True, "length": 90})) == "Loosening a block needs: typing a 90-character phrase"
    assert page.describe(ab.with_defaults({"phrase": True, "length": 90, "grid": True, "complex": True})) \
        == "Loosening a block needs: typing a 90-character phrase (with numbers & capitals) in a 3\u00d73 grid"
    assert page.describe(phrase_cfg(MIXED, grid=True)) == \
        "Loosening a block needs: doing Math (Hard) ×5, then Memory grid (Medium) ×2, then Typed phrase (Medium) (the phrase in a 3×3 grid)"


# ---------------------------------------------------------------- the Sleep card saves lists strictly

SLEEP = {"on": True, "guarded": True, "bedtime": "22:00", "wake": "07:00", "before": "30", "mode": "",
         "tiers": [{"every": "15 min", "after": "22:00"}, {"every": "5 min", "after": "00:00"}], "dismiss_wait": 0,
         "warn_text": "", "text": ""}


def test_the_sleep_card_round_trips_the_lists(page_env):
    api, ctx = page_env.api, page_env
    got = api_data(api.reminders_get())
    assert got["sleep"]["challenge"] is False and got["sleep"]["challenge_steps"] == ch.default_bedtime_steps()
    assert got["challenge_options"]["max_steps"] == 6
    own = [step(MEMORY, 1, 1, "m1")]
    api_data(api.reminder_save_sleep({**SLEEP, "challenge": True, "challenge_steps": [step(MATH, 1, 3, "g")],
                                      "challenge_grid": True, "challenge_complex": True,
                                      "tiers": [{"every": "15 min", "after": "22:00"},
                                                {"every": "5 min", "after": "00:00", "challenge_steps": own}]}))
    s = reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert s["challenge"] is True and s["challenge_steps"] == [step(MATH, 1, 3, "g")] and s["challenge_grid"] is True
    assert reminders.tiers_of(s)[1]["challenge_steps"] == own and "challenge_steps" not in reminders.tiers_of(s)[0]
    view = api_data(api.reminders_get())["sleep"]
    assert view["challenge"] and view["tiers"][1]["challenge_steps"] == own and view["tiers"][0]["challenge_steps"] == []
    # a page that does not send the lists keeps them
    api_data(api.reminder_save_sleep({**SLEEP, "tiers": [{"every": "15 min", "after": "22:00"}, {"every": "5 min", "after": "00:00"}]}))
    s = reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert s["challenge"] and reminders.tiers_of(s)[1]["challenge_steps"] == own


def test_the_sleep_card_refuses_a_bad_list(page_env):
    api = page_env.api
    assert not api.reminder_save_sleep({**SLEEP, "challenge_steps": []})["ok"]
    assert not api.reminder_save_sleep({**SLEEP, "challenge_steps": [step(MATH, 9, 1, "a")]})["ok"]
    assert not api.reminder_save_sleep({**SLEEP, "tiers": [{"every": "5 min", "after": "00:00", "challenge_steps": [step(SORT, 5)]}]})["ok"]
    assert not api.reminder_save_sleep({**SLEEP, "challenge": "yes"})["ok"]
    assert not api.reminder_save_sleep({**SLEEP, "challenge_grid": 1})["ok"]


def test_a_looser_bedtime_challenge_asks_once_while_important(page_env):
    api, ctx = page_env.api, page_env
    api_data(api.reminder_save_sleep({**SLEEP, "challenge": True, "challenge_steps": [step(MATH, 2, 3, "g"), step(SORT, 1, 1, "h")]}))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})        # (Anti-Bypass on: the ticket is real)
    # raising is free
    assert api_data(api.reminder_save_sleep({**SLEEP, "challenge": True, "challenge_steps": [step(MATH, 3, 3, "g"), step(SORT, 1, 1, "h")]}))["saved"]
    # lowering asks (once, with a plain line)
    out = api_data(api.reminder_save_sleep({**SLEEP, "challenge": True, "challenge_steps": [step(MATH, 3, 3, "g")]}))
    assert out["needs_challenge"] and out["changes"] == ["Make the challenge before Dismiss on the bedtime screen easier"]
    off = api_data(api.reminder_save_sleep({**SLEEP, "challenge": False}))
    assert off["needs_challenge"] and not any("un-flag" in line for line in off["changes"])
    both = api_data(api.reminder_save_sleep({**SLEEP, "challenge": False, "dismiss_wait": 0,
                                             "tiers": [{"every": "15 min", "after": "22:00", "challenge_steps": [step(MATH, 0, 1, "z")]}]}))
    assert both["needs_challenge"]
    api.challenge_cancel(both["ticket"])
    s = reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)
    assert s["challenge"] is True and s["challenge_steps"] == [step(MATH, 3, 3, "g"), step(SORT, 1, 1, "h")]
    # the challenge itself saves the lowered list once it is passed
    out = api_data(api.reminder_save_sleep({**SLEEP, "challenge": True, "challenge_steps": [step(MATH, 3, 3, "g")]}))
    shown = api_data(api.challenge_begin(out["ticket"]))
    while True:
        ctx.clock.mono += 30
        got = api_data(api.challenge_submit(out["ticket"], solve(shown["run"])))
        if got["passed"]:
            break
        shown = got["next"]
    assert reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)["challenge_steps"] == [step(MATH, 3, 3, "g")]


def test_a_bedtime_that_is_not_important_changes_its_challenge_freely(page_env):
    api, ctx = page_env.api, page_env
    calm = {**SLEEP, "guarded": False}
    api_data(api.reminder_save_sleep({**calm, "challenge": True, "challenge_steps": [step(MATH, 3, 3, "g")]}))
    ab.save(ctx.db, {**ab.settings(ctx.db), "phrase": True, "length": 30})
    assert api_data(api.reminder_save_sleep({**calm, "challenge": True, "challenge_steps": [step(MATH, 0, 1, "g")]}))["saved"]
    assert api_data(api.reminder_save_sleep({**calm, "challenge": False}))["saved"]
    assert reminders.load(ctx.db, reminders.SLEEP_KEY, reminders.DEFAULT_SLEEP)["challenge"] is False


# ---------------------------------------------------------------- the App log reads the new settings

def test_the_app_log_names_the_new_anti_bypass_steps():
    old = '{"phrase": true, "length": 60}'
    new = '{"phrase": true, "length": 60, "steps": [{"id": "a", "kind": "math", "level": 2, "times": 5}, ' \
          '{"id": "s0", "kind": "phrase", "level": 60, "times": 1}]}'
    assert applog_changes.describe_setting("antibypass", old, new) == [
        ("antibypass_change", "Anti-Bypass changed: challenge steps: Typed phrase Medium x1 → Math Hard x5, Typed phrase Medium x1")]
    assert applog_changes.describe_setting("antibypass", new, new) == []
    assert applog_changes.describe_setting("antibypass", old, '{"phrase": true, "length": 60, "wait_min": 5}') == [
        ("antibypass_change", "Anti-Bypass changed: wait (min) 0 → 5")]            # (the migrated list is not a change)
    odd = '{"phrase": true, "steps": [{"id": "a", "kind": "phrase", "level": 45, "times": 2}]}'
    assert "Typed phrase 45 letters x2" in applog_changes.describe_setting("antibypass", old, odd)[0][1]


def test_the_app_log_names_the_new_sleep_settings():
    import json
    base = {**reminders.DEFAULT_SLEEP, "tiers": [{"from": "22:00", "every_sec": 60}]}
    turned_on = {**base, "challenge": True}
    assert applog_changes.describe_setting("reminders.sleep", json.dumps(base), json.dumps(turned_on)) == [
        ("change", "Reminder - bedtime: challenge before Dismiss turned on")]
    lists = {**turned_on, "challenge_steps": [step(MATH, 2, 3, "g")], "challenge_grid": True,
             "tiers": [{"from": "22:00", "every_sec": 60, "challenge_steps": [step(SORT, 1, 2, "o")]}]}
    text = applog_changes.describe_setting("reminders.sleep", json.dumps(turned_on), json.dumps(lists))[0][1]
    assert "challenge steps: Math Easy x2 → Math Hard x3" in text
    assert "challenge steps from 22:00: the general list → Sorting Medium x2" in text
    assert "challenge word grid turned on" in text
    assert applog_changes.describe_setting("reminders.sleep", json.dumps(base), json.dumps({**base, "dismiss_wait": 5})) == [
        ("change", "Reminder - bedtime: Dismiss waits (s) 0 → 5")]
