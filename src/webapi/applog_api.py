"""Api for Settings > App log (src/applog.py): read the events a block at a time, get the text to copy, and save the
shareable file. Nothing here changes a block, so nothing needs the challenge.

- `applog_get(chip, search, before, limit, days)`: the newest events first (filtered by the chip or "Only…" filter, the
  search box and the period: `days` 0 = all, 1 = today, 7 / 30 = the last 7 / 30 days); `before` is the id of the oldest
  event the page has, to get the next older block. The page paints them in a virtual list.
- `applog_text(chip, search, days)`: the text "Copy" puts on the clipboard (header + the newest 1000 events as filtered).
- `applog_save()`: "Save log…": the owner's own Save dialog (the page never names a path), then lockdown-log-YYYY-MM-DD.txt
  with the version, Windows, the newest 1000 events and the ends of lockdown.log and lockdown-newui.log.
"""
import applog

from . import core, settings
from .core import ApiError

MAX_LIMIT = 500
MAX_SEARCH = 100
TEXT_FILTER = "Text file (*.txt)"


def _checked(chip, search, before=None, limit=1, days=0) -> tuple[str, str, int | None, int]:
    if not isinstance(chip, str) or chip not in applog.CHIP_NAMES:
        raise ApiError("Pick one of the filters.")
    if type(days) is not int or days not in applog.PERIODS:
        raise ApiError("Pick All time, Today, 7 days or 30 days.")
    if not isinstance(search, str) or len(search) > MAX_SEARCH:
        raise ApiError("That search is too long.")
    if before is not None and (type(before) is not int or before < 1):
        raise ApiError("Bad request: a number expected.")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ApiError("Bad request: a number between 1 and 500 expected.")
    return chip, search.strip(), before, limit


@core.read
def applog_get(ctx, chip="all", search="", before=None, limit=300, days=0):
    """{rows: [{id, ts, source, kind, text}] newest first, more: older ones exist, total: how many events there are,
    chips: [...]}."""
    chip, search, before, limit = _checked(chip, search, before, limit, days)
    conn = ctx.db.conn
    rows = applog.query(conn, chip, search, before, limit + 1, since=applog.since_text(days, ctx.now()))
    return {"rows": rows[:limit], "more": len(rows) > limit, "total": applog.count(conn) if before is None else None,
            "chips": list(applog.CHIP_NAMES)}


@core.read
def applog_text(ctx, chip="all", search="", days=0):
    """The text to copy: the header and the newest events (as filtered), without the ends of the two log files."""
    chip, search, _before, _limit = _checked(chip, search, days=days)
    return {"text": applog.report(ctx.db.conn, chip=chip, search=search, with_logs=False, when=ctx.now(), days=days)}


def _ask_save_path(window, name: str) -> str | None:
    """The file the owner picked in the native Save dialog, or None when cancelled (thin glue: replaced in the tests)."""
    if window is None:
        raise ApiError("The Save dialog isn't available.")
    return settings._first(window.create_file_dialog(settings._dialog_kind("SAVE"), save_filename=name,
                                                     file_types=(TEXT_FILTER,)))


@core.ui
def applog_save(ctx):
    """"Save log…": ask where (the owner's own dialog), then write the whole report. -> {cancelled: true} or {path, events}."""
    now = ctx.now()
    path = _ask_save_path(settings._window(ctx), applog.file_name(now))
    if not path:
        return {"cancelled": True}
    if not path.lower().endswith(".txt"):
        path += ".txt"
    text = applog.report(ctx.db.conn, when=now)
    try:
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)
    except OSError as e:
        raise ApiError(f"Couldn't save the file: {e}") from None
    return {"path": path, "events": min(applog.count(ctx.db.conn), applog.REPORT_EVENTS)}
