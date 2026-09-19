"""Forgiving search for the app's search boxes: every word you type must appear in the text - exactly, or with a small
typo (1 for words of 4-6 letters, 2 for longer ones; swapped letters count as one), also while still typing it
("lethl" finds "Lethal Company"). Capitals and accents don't matter. Exact matches come first."""
import re
import unicodedata

_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(c for c in text if not unicodedata.combining(c))


def typos_allowed(word: str) -> int:
    return 0 if len(word) <= 3 else 1 if len(word) <= 6 else 2


def distance(a: str, b: str, limit: int) -> int:
    """Edits (insert, delete, change, swap two neighbours) from a to b; anything over `limit` is returned as limit+1."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2, prev = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if prev2 is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def score(query: str, text: str) -> int | None:
    """How well `text` matches (0 = every word found exactly; higher = more typos), or None if it doesn't."""
    words = _WORD.findall(normalize(query))
    if not words:
        return 0
    text = normalize(text)
    tokens = _WORD.findall(text)
    total = 0
    for q in words:
        if q in text:
            continue
        allowed = typos_allowed(q)
        if not allowed:
            return None
        n = len(q)
        best = min((distance(q, cand, allowed) for t in tokens
                    for cand in {t, t[:n], t[:n + 1], t[:max(n - 1, 1)]}), default=allowed + 1)
        if best > allowed:
            return None
        total += best
    return total


def rank(items: list, query: str, text_of, tie=None) -> list:
    """The items that match, best first (then by `tie(item)`, else in their own order)."""
    found = [(s, i, item) for i, item in enumerate(items) if (s := score(query, text_of(item))) is not None]
    found.sort(key=lambda f: (f[0], tie(f[2]) if tie else f[1]))
    return [item for _s, _i, item in found]
