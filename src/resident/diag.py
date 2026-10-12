"""Start-up notes of the new window, for when it fails on a real PC (we can't run WebView2 here).

Everything goes to %TEMP%\\lockdown-newui.log (this user's temp folder; the file starts over when it grows past
MAX_BYTES). `important=True` lines also go to the Lockdown log (C:\\ProgramData\\Lockdown\\lockdown.log), where the
service and the window write. pywebview's own warnings and errors (WebView2 failing to start, a script error in its
bridge) are caught here too: `capture_pywebview()`. Never raises."""
import logging
import tempfile
import time
from pathlib import Path

FILE_NAME = "lockdown-newui.log"
MAX_BYTES = 512 * 1024


def path() -> Path:
    return Path(tempfile.gettempdir()) / FILE_NAME


def note(message: str, important: bool = False):
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        p = path()
        if p.exists() and p.stat().st_size > MAX_BYTES:
            p.unlink()
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{stamp} {message}\n")
    except OSError:
        pass
    if important:
        try:
            import paths
            with open(paths.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{stamp},000 WARNING Lockdown window: {message}\n")
        except OSError:
            pass
        _app_log(message)


def _app_log(message: str):
    """The important notes also go to the App log: a window that could not start, or was tried again, is told as such."""
    import applog
    text = " ".join(str(message).split())
    if "tried once more" in text or "second try" in text:
        kind = "window_retry"
    elif ("could not be used" in text or "could not start" in text or "failed to start" in text or "challenge window" in text
          or "did not end" in text):
        kind = "window_failed"
    else:
        kind = "problem"
    applog.problem(text[:400], kind)


class _PywebviewHandler(logging.Handler):
    def emit(self, record):
        try:
            text = record.getMessage()
            if record.exc_info:
                import traceback
                text += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
            note(f"pywebview {record.levelname}: {text}", important=record.levelno >= logging.ERROR)
        except Exception:
            pass


def capture_pywebview():
    """pywebview's warnings and errors into these notes (its own handler writes to a console the .exe doesn't have)."""
    logger = logging.getLogger("pywebview")
    if not any(isinstance(h, _PywebviewHandler) for h in logger.handlers):
        handler = _PywebviewHandler(logging.WARNING)
        logger.addHandler(handler)
