"""Challenge kinds: what Anti-Bypass and the bedtime screen ask before they let something loosen.

A challenge is an ordered LIST OF STEPS, done top to bottom; a step is {"id", "kind", "level", "times"}:
- math: sums typed as a whole number (4 levels: Easy, Medium, Hard, Very hard)
- memory: a grid whose lit squares you tap back after pressing Ready (3 levels)
- sort: tap the items from smallest to largest (3 levels)
- phrase: type a random phrase (its level is its length in characters; 3x3 grid, numbers & CAPITALS, own phrase)
- password, totp (SECRET_KINDS, Anti-Bypass only - never the bedtime screen): your own password / an authenticator code,
  asked once (no level, times always 1), checked by challenge_secrets.Checker
An Anti-Bypass run may also be passed whole by the recovery code (`Run(recovery=True)`, see challenge_secrets.py).
This module makes and judges every puzzle (the page only gets what it has to draw and never the answer), checks
step lists (strictly for a save, leniently for stored data), compares two lists (stricter or looser) and runs one
challenge (`Run`). It is UI-free and does not depend on GuardService, so the tray's bedtime cover uses `Run` as it is.
The phone has the same engine in Kotlin (ChallengeEngine) built from the same spec: keep ids, levels and numbers equal.

Part of Anti-Bypass: read the ANTI-BYPASS RULE note at the top of antibypass.py before changing anything here. A
change that makes a challenge easier to pass (shorter, fewer, quicker, answer given to the page) is a loosening.
"""
import random
import re
import string
import time
from fractions import Fraction
from math import gcd

KINDS = ("math", "memory", "sort", "phrase")           # the kinds that cost effort: Anti-Bypass and the bedtime screen
SECRET_KINDS = ("password", "totp")                    # need something you know / hold: Anti-Bypass only
AB_KINDS = KINDS + SECRET_KINDS                         # what the Anti-Bypass list may hold
LABELS = {"math": "Math", "memory": "Memory grid", "sort": "Sorting", "phrase": "Typed phrase",
          "password": "Own password", "totp": "Authenticator code"}
LEVEL_NAMES = {"math": ("Easy", "Medium", "Hard", "Very hard"), "memory": ("Easy", "Medium", "Hard"),
               "sort": ("Easy", "Medium", "Hard")}
PHRASE_LENGTHS = {"Short": 30, "Medium": 60, "Long": 120, "Very long": 250}   # quick picks; any length in between works
PHRASE_MIN, PHRASE_MAX = 30, 2000
MAX_STEPS = 6
MIN_TIMES, MAX_TIMES = 1, 20
MAX_ID = 24
PHRASE_MS_PER_CHAR = 35        # typing the phrase faster than this per character isn't a human ("Too fast")
MIN_SEC = {"math": 1.2, "memory": 0.15, "sort": 0.15}   # fastest plausible: a puzzle / per lit square / per item
# "Takes about", seconds for ONE of a step (a phrase: 3 + 0.4 per character, x1.3 as a 3x3 grid)
SECONDS_ONE = {"math": (6, 10, 20, 35), "memory": (8, 10, 15), "sort": (6, 10, 20), "password": (8,), "totp": (20,)}
PHRASE_SEC_BASE, PHRASE_SEC_PER_CHAR, GRID_FACTOR = 3, 0.4, 1.3
LONG_SEC = 10 * 60             # above this the list gets a soft "this will take about ..." note
MINUS, TIMES, DIVIDE = "−", "×", "÷"

RIGHT, WRONG, MISMATCH, TOO_FAST, PASSED = "right", "wrong", "mismatch", "too_fast", "passed"
REFUSED, BUSY = "refused", "busy"      # a password / code / recovery code that is not right; one that came during a wait

_ID = re.compile(r"[A-Za-z0-9_-]{1,%d}" % MAX_ID)
_DIGITS = re.compile(r"[0-9]{1,15}")


# ---------------------------------------------------------------- kinds, levels, labels

def level_range(kind: str) -> tuple[int, int]:
    """(lowest, highest) level of a kind: the phrase's level is its length in characters; a secret kind has only 0."""
    if kind in SECRET_KINDS:
        return 0, 0
    return (PHRASE_MIN, PHRASE_MAX) if kind == "phrase" else (0, len(LEVEL_NAMES[kind]) - 1)


def level_label(kind: str, level: int) -> str:
    """"Hard"; for a phrase the quick pick its length is ("Medium"), else "45 letters"; a secret kind has none ("")."""
    if kind in SECRET_KINDS:
        return ""
    if kind == "phrase":
        return next((name for name, n in PHRASE_LENGTHS.items() if n == level), f"{level} letters")
    return LEVEL_NAMES[kind][level]


def step_label(step: dict) -> str:
    """"Math, Hard" (the line "Step 1 of 3 - Math, Hard - 3 of 5"); "Own password" (no level)."""
    level = level_label(step["kind"], step["level"])
    return f"{LABELS[step['kind']]}, {level}" if level else LABELS[step["kind"]]


def steps_text(steps: list[dict]) -> str:
    """The list in a sentence: "Math (Hard) x5, then Typed phrase (Medium), then Own password"."""
    def one(s):
        level = level_label(s["kind"], s["level"])
        return LABELS[s["kind"]] + (f" ({level})" if level else "") + (f" ×{s['times']}" if s["times"] > 1 else "")
    return ", then ".join(one(s) for s in steps)


def options(kinds=KINDS) -> dict:
    """What an editor needs to offer: kinds with their levels and seconds, the limits and the phrase numbers. A secret
    kind (Anti-Bypass only: pass kinds=AB_KINDS) has no levels and is always done once (`secret`, `times` 1)."""
    out = []
    for kind in kinds:
        entry = {"id": kind, "label": LABELS[kind]}
        if kind in SECRET_KINDS:
            entry.update(levels=[], seconds=list(SECONDS_ONE[kind]), secret=True, times=1)
        elif kind == "phrase":
            entry.update(levels=[{"level": n, "label": name} for name, n in PHRASE_LENGTHS.items()],
                         min_level=PHRASE_MIN, max_level=PHRASE_MAX)
        else:
            entry.update(levels=[{"level": i, "label": name} for i, name in enumerate(LEVEL_NAMES[kind])],
                         seconds=list(SECONDS_ONE[kind]))
        out.append(entry)
    return {"kinds": out, "max_steps": MAX_STEPS, "min_times": MIN_TIMES, "max_times": MAX_TIMES,
            "phrase_seconds": {"base": PHRASE_SEC_BASE, "per_char": PHRASE_SEC_PER_CHAR, "grid": GRID_FACTOR},
            "long_seconds": LONG_SEC}


# ---------------------------------------------------------------- step lists

def default_steps(length: int = 60) -> list[dict]:
    """The list a config from before step lists reads as: one phrase of its length, once."""
    return [{"id": "s0", "kind": "phrase", "level": _clamp(length, PHRASE_MIN, PHRASE_MAX) or 60, "times": 1}]


def default_bedtime_steps() -> list[dict]:
    """What "Challenge before Dismiss" starts with: two easy sums."""
    return [{"id": "b0", "kind": "math", "level": 0, "times": 2}]


def new_id(taken=(), rng=random) -> str:
    """A fresh step id (editors make their own; this is for Python and tests)."""
    while True:
        found = "".join(rng.choice(string.ascii_lowercase + string.digits) for _ in range(8))
        if found not in taken:
            return found


def _whole(value) -> int | None:
    """An integer from an int or an integral float (not a bool), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def _clamp(value, low: int, high: int) -> int | None:
    n = _whole(value)
    return None if n is None else max(low, min(high, n))


def _kinds_text(kinds) -> str:
    names = [LABELS[k] for k in kinds]
    return ", ".join(names[:-1]) + " or " + names[-1]


def validate_steps(raw, kinds=KINDS) -> list[dict]:
    """A step list the page sent, checked strictly (a save): 1..MAX_STEPS rows, each with a unique id, a kind of `kinds`
    (the bedtime screen: KINDS; Anti-Bypass: AB_KINDS), a level in that kind's range and times MIN_TIMES..MAX_TIMES (a
    secret kind: level 0, once). Nothing is clamped - a wrong value is refused with a plain message (ValueError).
    Returns clean rows with just the four keys."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("Add at least one step.")
    if len(raw) > MAX_STEPS:
        raise ValueError(f"At most {MAX_STEPS} steps, please.")
    out, ids = [], set()
    for i, row in enumerate(raw, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Step {i}: kind, level and times expected.")
        kind = row.get("kind")
        if kind not in kinds:
            raise ValueError(f"Step {i}: pick {_kinds_text(kinds)}.")
        low, high = level_range(kind)
        level, times = _whole(row.get("level")), _whole(row.get("times"))
        if kind in SECRET_KINDS and (level != 0 or times != 1):
            raise ValueError(f"Step {i}: {LABELS[kind]} is asked once and has no level.")
        if level is None or not low <= level <= high:
            what = "a phrase length" if kind == "phrase" else "a level"
            raise ValueError(f"Step {i}: {what} from {low} to {high} expected.")
        if times is None or not MIN_TIMES <= times <= MAX_TIMES:
            raise ValueError(f"Step {i}: how many times, from {MIN_TIMES} to {MAX_TIMES}.")
        ident = row.get("id")
        if not isinstance(ident, str) or not _ID.fullmatch(ident):
            raise ValueError(f"Step {i}: a step needs an id (letters, numbers, - and _).")
        if ident in ids:
            raise ValueError(f"Step {i}: two steps have the same id.")
        ids.add(ident)
        out.append({"id": ident, "kind": kind, "level": level, "times": times})
    return out


def read_steps(raw, kinds=KINDS) -> list[dict]:
    """A stored step list (a config, a backup, a copy from another device), read leniently: unreadable rows and kinds
    not in `kinds` are left out (the bedtime screen never reads a password step), levels and times are clamped into
    range (a secret kind: level 0, once), a bad or repeated id gets a plain one, more than MAX_STEPS are cut.
    May come back empty - the caller then uses its default (default_steps / default_bedtime_steps)."""
    out, ids = [], set()
    for row in raw if isinstance(raw, list) else []:
        if len(out) >= MAX_STEPS:
            break
        if not isinstance(row, dict) or row.get("kind") not in kinds:
            continue
        kind = row["kind"]
        level, times = _clamp(row.get("level"), *level_range(kind)), _clamp(row.get("times"), MIN_TIMES, MAX_TIMES)
        if kind in SECRET_KINDS and times is not None:
            times = 1
        if level is None or times is None:
            continue
        ident = row.get("id")
        if not isinstance(ident, str) or not _ID.fullmatch(ident) or ident in ids:
            n = len(out)
            while f"r{n}" in ids:
                n += 1
            ident = f"r{n}"
        ids.add(ident)
        out.append({"id": ident, "kind": kind, "level": level, "times": times})
    return out


def _strength(row: dict, own: int) -> int:
    """How hard a row is to compare: a phrase while your own is in use counts that phrase's length (non-space)."""
    return own if row["kind"] == "phrase" and own else row["level"]


def steps_looser(old: list[dict], new: list[dict], own_old: int = 0, own_new: int = 0) -> bool:
    """Is the new list easier somewhere? Rows are matched by id: an old row that is gone, has another kind, a lower
    level or fewer times is looser. New rows and a different order are free (the work stays). `own_old` / `own_new`:
    the non-space length of the own phrase in use then (0 = none), which decides a phrase row's strength."""
    by_id = {r["id"]: r for r in new}
    for row in old:
        now = by_id.get(row["id"])
        if (now is None or now["kind"] != row["kind"] or now["times"] < row["times"]
                or _strength(now, own_new) < _strength(row, own_old)):
            return True
    return False


def has_phrase(steps: list[dict]) -> bool:
    return any(s["kind"] == "phrase" for s in steps)


def secret_kinds_in(steps: list[dict]) -> set:
    """The secret kinds the list asks ({"password", "totp"} or part of it)."""
    return {s["kind"] for s in steps} & set(SECRET_KINDS)


# ---------------------------------------------------------------- how long it takes

def step_seconds(step: dict, grid: bool = False, own_length: int = 0) -> float:
    """Seconds ONE of this step takes. `own_length`: your own phrase's length while it is in use (0 = none)."""
    if step["kind"] in SECRET_KINDS:
        return SECONDS_ONE[step["kind"]][0]
    if step["kind"] != "phrase":
        return SECONDS_ONE[step["kind"]][step["level"]]
    return (PHRASE_SEC_BASE + PHRASE_SEC_PER_CHAR * (own_length or step["level"])) * (GRID_FACTOR if grid else 1)


def estimate(steps: list[dict], grid: bool = False, own_length: int = 0) -> dict:
    """"Takes about": {seconds, text ("under 1 min", "about 3 min"), long (over 10 min), note (the soft sentence, or "")}."""
    seconds = round(sum(step_seconds(s, grid, own_length) * s["times"] for s in steps))
    text = "under 1 min" if seconds < 60 else f"about {int(seconds / 60 + 0.5)} min"
    long = seconds > LONG_SEC
    return {"seconds": seconds, "text": text, "long": long, "note": f"This will take {text}." if long else ""}


# ---------------------------------------------------------------- puzzles

class Puzzle:
    """One puzzle. `view` is what the screen may draw; `answer` (the right answer) and the checking stay in here."""

    def __init__(self, kind: str, view: dict, answer, check, min_sec: float):
        self.kind, self.view, self.answer, self._check, self.min_sec = kind, view, answer, check, min_sec

    def check(self, value) -> bool:
        try:
            return bool(self._check(value))
        except (TypeError, ValueError, OverflowError):
            return False


def new_phrase(length: int, complex: bool = False, rng=random) -> str:
    """Random phrase in groups of 5 ("kqzph mxacd ..."). Lowercase letters by default; complex also adds capital
    letters and digits (harder to read and type)."""
    charset = string.ascii_lowercase + (string.ascii_uppercase + string.digits if complex else "")
    chars = "".join(rng.choice(charset) for _ in range(length))
    return " ".join(chars[i:i + 5] for i in range(0, length, 5))


def phrase_puzzle(length: int, rng=random, grid: bool = False, complex: bool = False, own: str = "") -> Puzzle:
    """The phrase to type: your own while one is given (a blank one counts as none), else a fresh random one. In grid
    mode the words are typed one at a time (the answer is the list of words)."""
    phrase = own if own.strip() else new_phrase(length, complex, rng)
    if grid:
        words = phrase.split()
        return Puzzle("phrase", {"grid_words": words}, words,
                      lambda v: isinstance(v, list) and all(isinstance(w, str) for w in v) and v == words,
                      sum(len(w) for w in words) * PHRASE_MS_PER_CHAR / 1000)
    return Puzzle("phrase", {"phrase": phrase}, phrase, lambda v: isinstance(v, str) and v == phrase,
                  len(phrase) * PHRASE_MS_PER_CHAR / 1000)


def _expr(a, sign: str, b) -> str:
    return f"{a} {sign} {b}"


def math_puzzle(level: int, rng=random) -> Puzzle:
    """A sum, drawn by the screen as a picture. The answer is one whole number, never negative."""
    find_x = False
    if level <= 1 and (op := rng.choice("+-*/" if level else "+-")) in "+-":
        a, b = rng.randint(10, 99), rng.randint(10, 99)
        if op == "-" and a < b:
            a, b = b, a
        expr, answer = (_expr(a, MINUS, b), a - b) if op == "-" else (_expr(a, "+", b), a + b)
    elif level == 1 and op == "*":
        a, b = rng.randint(2, 19), rng.randint(2, 9)
        expr, answer = _expr(a, TIMES, b), a * b
    elif level == 1:
        b, q = rng.randint(2, 12), rng.randint(2, 12)
        expr, answer = _expr(b * q, DIVIDE, b), q
    elif level == 2:        # (a +- b) x c +- d
        inner_minus = rng.random() < 0.5
        if inner_minus:
            a, b = sorted(rng.sample(range(10, 100), 2), reverse=True)
        else:
            a, b = rng.randint(10, 99), rng.randint(10, 99)
        c, d = rng.randint(2, 9), rng.randint(10, 99)
        base = ((a - b) if inner_minus else (a + b)) * c
        end_minus = rng.random() < 0.5 and base >= d
        expr = f"({_expr(a, MINUS if inner_minus else '+', b)}) {TIMES} {c} {MINUS if end_minus else '+'} {d}"
        answer = base - d if end_minus else base + d
    elif rng.random() < 0.5:        # level 3: a x b ...
        a, b = rng.randint(11, 99), rng.randint(11, 99)
        expr, answer = _expr(a, TIMES, b), a * b
    else:                           # ... or find x:  a*x +- b = c
        a, x = rng.randint(2, 12), rng.randint(2, 20)
        minus = rng.random() < 0.5
        b = rng.randint(1, min(99, a * x - 1)) if minus else rng.randint(1, 99)
        expr, answer, find_x = f"{a}x {MINUS if minus else '+'} {b} = {a * x - b if minus else a * x + b}", x, True

    def check(value):
        if isinstance(value, str):
            text = value.strip()
            return bool(_DIGITS.fullmatch(text)) and int(text) == answer
        return _whole(value) == answer
    return Puzzle("math", {"expr": expr, "find_x": find_x}, answer, check, MIN_SEC["math"])


MEMORY_LEVELS = ((4, 4, False), (5, 6, False), (5, 8, True))      # grid side, lit squares, in order?


def memory_puzzle(level: int, rng=random) -> Puzzle:
    """Squares to remember: cells are numbered row by row from 0. `lit` is in the order they have to be tapped when
    `ordered` (the screen shows their numbers), else sorted. The answer is the tapped cells in tap order."""
    size, lit, ordered = MEMORY_LEVELS[level]
    cells = rng.sample(range(size * size), lit)
    if not ordered:
        cells.sort()

    def check(value):
        if not isinstance(value, list) or len(value) != lit or any(_whole(v) is None or isinstance(v, float) for v in value):
            return False
        return value == cells if ordered else sorted(value) == sorted(cells)
    return Puzzle("memory", {"size": size, "lit": list(cells), "ordered": ordered}, list(cells), check,
                  MIN_SEC["memory"] * lit)


def _tenths_text(t: int) -> str:
    sign = "-" if t < 0 else ""
    return f"{sign}{abs(t) // 10}" if t % 10 == 0 else f"{sign}{abs(t) // 10}.{abs(t) % 10}"


def _sort_easy(rng):
    return [(Fraction(n), str(n)) for n in rng.sample(range(1, 100), 6)]


def _sort_medium(rng):
    decimals = rng.randint(2, 5)            # eight numbers in -20..99, some with one decimal place
    tenths = set()
    while len(tenths) < decimals:
        t = rng.randint(-200, 990)
        if t % 10:
            tenths.add(t)
    while len(tenths) < 8:
        tenths.add(rng.randint(-20, 99) * 10)
    return [(Fraction(t, 10), _tenths_text(t)) for t in tenths]


def _sort_hard(rng):
    """Ten mixed values between -3 and 3 as fractions (denominators 2, 4, 5, 8), one-place decimals and whole percents,
    at least one of each form and at least one negative. All different (compared exactly)."""
    def fraction(neg):
        b = rng.choice((2, 4, 5, 8))
        n = rng.choice([n for n in range(1, 3 * b + 1) if gcd(n, b) == 1])
        return Fraction(-n if neg else n, b), f"{'-' if neg else ''}{n}/{b}"

    def decimal(neg):
        t = rng.choice([t for t in range(1, 31) if t % 10])
        return Fraction(-t if neg else t, 10), _tenths_text(-t if neg else t)

    def percent(neg):
        p = rng.randint(1, 300)
        return Fraction(-p if neg else p, 100), f"{'-' if neg else ''}{p}%"
    forms = [fraction, decimal, percent]
    chosen = forms + [rng.choice(forms) for _ in range(7)]
    items, seen = [], set()
    for i, make in enumerate(chosen):
        while True:
            value, text = make(i == 0 or rng.random() < 0.4)     # (the first one is always a negative)
            if value not in seen:
                seen.add(value)
                items.append((value, text))
                break
    return items


def sort_puzzle(level: int, rng=random) -> Puzzle:
    """Items to tap from smallest to largest. `items` is what the screen shows; an item's id is its place in that list,
    and the answer is the ids in tap order. All values differ, so there is one right order."""
    items = (_sort_easy, _sort_medium, _sort_hard)[level](rng)
    while True:
        rng.shuffle(items)
        values = [v for v, _ in items]
        if values != sorted(values):
            break
    n = len(items)

    def check(value):
        if not isinstance(value, list) or len(value) != n or any(_whole(v) is None or isinstance(v, float) for v in value):
            return False
        if sorted(value) != list(range(n)):
            return False
        tapped = [values[i] for i in value]
        return all(a < b for a, b in zip(tapped, tapped[1:]))
    answer = sorted(range(n), key=lambda i: values[i])
    return Puzzle("sort", {"items": [text for _, text in items]}, answer, check, MIN_SEC["sort"] * n)


def secret_puzzle(kind: str, secrets) -> Puzzle:
    """A password / authenticator step: nothing to draw. The checking is the Checker's (challenge_secrets): the right
    answer is never known here."""
    check = secrets.check_password if kind == "password" else secrets.check_totp
    return Puzzle(kind, {}, None, check, 0)


def make_puzzle(step: dict, rng=random, grid: bool = False, complex: bool = False, own: str = "",
                secrets=None) -> Puzzle:
    """The next puzzle of a step (grid, complex and own only matter to a phrase; `secrets` - a challenge_secrets.Checker
    - to a password or authenticator step)."""
    kind, level = step["kind"], step["level"]
    if kind in SECRET_KINDS:
        return secret_puzzle(kind, secrets)
    if kind == "phrase":
        return phrase_puzzle(level, rng, grid, complex, own)
    return {"math": math_puzzle, "memory": memory_puzzle, "sort": sort_puzzle}[kind](level, rng)


# ---------------------------------------------------------------- one run of a challenge

class Run:
    """One challenge being done: the steps in order, `times` puzzles each. The screen asks `view()` for what to draw
    and sends every answer to `answer()`; the right answer and the memory / sorting solution never leave this object.
    A wrong answer is a NEW puzzle of the same step (what is done stays done). A typed phrase that does not match
    stays (fix it and send again); one that came too fast starts its clock again. Cancel = drop the Run.
    A password / authenticator step (`kinds` with SECRET_KINDS - Anti-Bypass only - and a `secrets` Checker) stays until
    it is right. With `recovery` (an Anti-Bypass run that is not a practice, and a recovery code exists) the code may be
    sent instead of any answer ({"recovery": code}): it passes the WHOLE run at once and is used up - `new_code` then
    holds the new code the Checker made in its place (show it once).
    Independent of GuardService and of the clock: `rng` and `clock` (monotonic seconds) are swapped by tests."""

    def __init__(self, steps: list[dict], grid: bool = False, complex: bool = False, own: str = "",
                 rng=random, clock=time.monotonic, kinds=KINDS, secrets=None, recovery: bool = False):
        self.steps = read_steps(steps, kinds)
        if not self.steps:
            raise ValueError("A challenge needs at least one step.")
        if secrets is None and (recovery or secret_kinds_in(self.steps)):
            raise ValueError("A password or authenticator step needs its checker.")
        self.grid, self.complex, self.own = grid, complex, own or ""
        self._rng, self._clock, self._secrets = rng, clock, secrets
        self.index = self.done = 0
        self.finished = False
        self.last_too_fast = False     # the last answer to a puzzle that is not a phrase was refused for being too quick
        self.last_refused = ""         # what the last REFUSED answer was: "password", "totp" or "recovery"
        self.recovery = bool(recovery and secrets.has_recovery())     # the recovery code may pass this run
        self.new_code = None           # the new recovery code once the old one passed the run (shown once)
        self._new_puzzle()

    def _new_puzzle(self):
        self.puzzle = make_puzzle(self.steps[self.index], self._rng, self.grid, self.complex, self.own, self._secrets)
        self._shown = self._clock()

    @property
    def step(self) -> dict:
        return self.steps[min(self.index, len(self.steps) - 1)]

    def view(self) -> dict:
        """What to draw: {steps: [{kind, level, times, label}], step (index, 0-based), done (in this step), kind,
        level, finished, + the puzzle's fields: math {expr, find_x}, memory {size, lit, ordered}, sort {items},
        phrase {phrase} or in grid mode {grid_words}}."""
        step = self.step
        out = {"steps": [{"kind": s["kind"], "level": s["level"], "times": s["times"], "label": step_label(s)}
                         for s in self.steps],
               "step": self.index, "done": self.done, "kind": step["kind"], "level": step["level"],
               "finished": self.finished, "recovery": self.recovery}
        out.update({k: list(v) if isinstance(v, list) else v for k, v in self.puzzle.view.items()})
        return out

    def answer(self, value) -> str:
        """RIGHT (the next puzzle is up), WRONG (a new puzzle of this step), MISMATCH / TOO_FAST (a typed phrase, same
        phrase again), REFUSED / BUSY (a password, code or recovery code that is not right / came while the Checker
        makes wrong tries wait - the same step again), or PASSED (that was the last one). A puzzle other than a phrase
        answered under its fastest plausible time counts as WRONG (and `last_too_fast` says so). The recovery code
        ({"recovery": code}, while `recovery`) passes the whole run (PASSED) or is REFUSED / BUSY like a password."""
        if self.finished:
            return PASSED
        puzzle = self.puzzle
        fast = self._clock() - self._shown < puzzle.min_sec
        self.last_too_fast = False
        if isinstance(value, dict) and list(value) == ["recovery"]:
            return self._recovery(value["recovery"])
        if puzzle.kind in SECRET_KINDS:
            if self._secrets.wait_left() > 0:
                return BUSY
            if not puzzle.check(value):
                self.last_refused = puzzle.kind
                return REFUSED
        elif puzzle.kind == "phrase":
            if not puzzle.check(value):
                return MISMATCH
            if fast:
                self._shown = self._clock()         # (the clock starts over: a macro has to wait the full time again)
                return TOO_FAST
        elif fast or not puzzle.check(value):
            self.last_too_fast = fast
            self._new_puzzle()
            return WRONG
        self.done += 1
        if self.done >= self.steps[self.index]["times"]:
            self.index, self.done = self.index + 1, 0
        if self.index >= len(self.steps):
            return self._finish()
        self._new_puzzle()
        return RIGHT

    def _finish(self) -> str:
        self.finished = True
        self.index = len(self.steps) - 1
        self.done = self.steps[self.index]["times"]
        return PASSED

    def _recovery(self, typed) -> str:
        """The recovery code instead of an answer: the whole run passes when it is right (it is used up and a new one
        made: `new_code`); not offered in this run, wrong, or during a wait for wrong tries -> REFUSED / BUSY."""
        self.last_refused = "recovery"
        if not self.recovery:
            return REFUSED
        if self._secrets.wait_left() > 0:
            return BUSY
        self.new_code = self._secrets.use_recovery(typed)
        if not self.new_code:
            return REFUSED
        return self._finish()
