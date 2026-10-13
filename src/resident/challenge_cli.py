"""The challenge outside the main page: the uninstaller's `--challenge TEXT` window, and the tray asking for it.

Python decides everything (webapi/guard.py): the page only shows what GuardService issued and types. Here:
- `exit_code(ctx, text, show)`: ask GuardService for a ticket whose stored action is "exit 0", let `show(ticket)` put the
  challenge on screen until it is closed, then answer 0 (passed) or 1. No Anti-Bypass challenge set = 0 at once.
  It is the uninstaller's challenge (`uninstall`): the list without the password / authenticator steps (forgotten or
  lost = uninstalling is the way out), and passing it never unlocks anything else.
- `run(argv)`: the Windows glue - a small window with js/challenge.js (challenge.html), its own temporary WebView2
  profile (the resident agent may be running with the normal one), and an Api that has only the challenge methods.
  If its page does not say it works within READY_SEC (or WebView2 is missing), the window goes, the reason is logged and
  `try_again` runs: ONE fresh `--challenge TEXT --retried` process answers instead (and a message box says so). A
  second try that fails too answers 1 = not passed (the uninstaller then refuses, which is safe) - never stuck.
- `ask_in_main(win, ctx, changes, action)`: tray Exit and friends - run the action now if Anti-Bypass is "free", else
  show the main window and push `challenge_open` so the page opens the challenge.
"""
import threading
from pathlib import Path
from urllib.parse import quote

DEFAULT_TEXT = "Changing Lockdown"
WINDOW_SIZE, WINDOW_MIN = (700, 660), (540, 440)
# The only Api methods the standalone window may call (the page is untrusted like any other).
STANDALONE_METHODS = ("challenge_begin", "challenge_submit", "challenge_cancel", "get_theme", "antibypass_status",
                      "log_client_error", "page_ready")
READY_SEC = 8           # the page must say it works within this, or the challenge is tried once more


def challenge_text(argv: list[str]) -> str:
    """The words after --challenge (what the challenge is for), or a general line when there are none."""
    i = argv.index("--challenge")
    text = argv[i + 1].strip() if i + 1 < len(argv) and not argv[i + 1].startswith("--") else ""
    return text or DEFAULT_TEXT


def exit_code(ctx, text: str, show) -> int:
    """0 = nothing to pass or passed, 1 = cancelled, closed, in the wait, or outside the allowed hours.
    `show(ticket)` returns when the challenge is over (window closed)."""
    passed = []
    reply = ctx.guard.request([text], lambda: passed.append(True), uninstall=True)
    data = reply.get("data") if reply.get("ok") else None
    if not (isinstance(data, dict) and data.get("needs_challenge")):
        return 0 if reply.get("ok") else 1
    ticket = data["ticket"]
    try:
        show(ticket)
    finally:
        ctx.guard.cancel(ticket)        # (nothing left to cancel after a pass)
    return 0 if passed else 1


def standalone_api(ctx, close_window):
    """The Api object for the standalone window: the challenge methods plus `challenge_window_close`."""
    from webapi import core
    reg = core.Registry()
    for name in STANDALONE_METHODS:
        reg._methods[name] = core.registry.get(name)

    def challenge_window_close(ctx, passed=False):
        """The challenge is over (passed, cancelled or OK): close this window."""
        threading.Timer(0.05, close_window).start()      # (after this reply has gone back to the page)
    reg._register("ui", challenge_window_close)
    return core.build_api(ctx, reg)


def run(argv: list[str]) -> int:
    """`Lockdown.exe --challenge TEXT`. Exit code 0 = passed."""
    import shutil
    import tempfile

    import webapi
    from resident import diag, overlay, windows
    from resident.assets import AssetServer

    ctx = webapi.make_context()
    server = AssetServer(log=diag.note)
    ready, gave_up = threading.Event(), []
    ctx.on_page_ready = lambda name: ready.set()

    def show(ticket):
        import webview
        base = server.start()
        box = {}
        api = standalone_api(ctx, lambda: box["win"].destroy())
        box["win"] = webview.create_window(
            "Anti-Bypass", f"{base}/challenge.html?ticket={quote(ticket)}", js_api=api, width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1], min_size=WINDOW_MIN, on_top=True, background_color=overlay.saved_background(),
            text_select=False)

        def watch():     # (runs once the window loop is up)
            renderer = getattr(webview, "renderer", None)
            if renderer == "edgechromium" and ready.wait(READY_SEC):
                return
            gave_up.append(f"its page did not say it works (renderer {renderer!r}, files served {server.served})")
            diag.note(f"challenge window: {gave_up[0]}", important=True)
            try:
                box["win"].destroy()
            except Exception:
                pass
        profile = tempfile.mkdtemp(prefix="lockdown-challenge-")
        try:
            windows.start(watch, storage=Path(profile))
        finally:
            shutil.rmtree(profile, ignore_errors=True)

    try:
        code = exit_code(ctx, challenge_text(argv), show)
    finally:
        server.stop()
    return try_again(argv, gave_up[0]) if gave_up else code


def try_again(argv: list[str], reason: str) -> int:
    """The challenge window could not start (the reason is in the logs already): ONE fresh `--challenge TEXT --retried`
    process, started BEFORE the message box (a modal box must not hold it up), and its exit code is the answer (0 =
    passed). A run that already is the second try, or one that can't be started, answers 1 = not passed."""
    import subprocess

    import agent
    from paths import gui_command
    from resident import diag
    retry = None
    if agent.RETRIED not in argv:
        try:
            retry = subprocess.Popen(gui_command("--challenge", challenge_text(argv), agent.RETRIED))
        except OSError as error:
            diag.note(f"challenge window: the second try could not be started: {error!r}", important=True)
    agent.message_box(agent.failure_text("The Anti-Bypass challenge window", reason, retry is not None))
    return retry.wait() if retry is not None else 1


def ask_in_main(win, ctx, changes: list[str], action, force: bool = False, cancel=None) -> bool:
    """Run `action` now if Anti-Bypass allows it; otherwise show the main window and push `challenge_open` so the page
    opens the challenge (the action then runs in Python when it is passed). Returns True if the action ran now."""
    from resident import windows
    reply = ctx.guard.request(changes, action, cancel, force)
    data = reply.get("data") if reply.get("ok") else None
    if isinstance(data, dict) and data.get("needs_challenge"):
        windows.open_challenge(win, data)
        return False
    return bool(reply.get("ok"))
