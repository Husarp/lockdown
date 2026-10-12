"""The three windows of the new UI, all made hidden when the agent starts: main (closing it only hides it), corner
(small, no frame, always on top, never takes focus: notices and reminder popups) and overlay (full screen, no frame,
always on top: bedtime / strict break). Win32 / pywebview glue only - kept thin; pywebview is imported when used.

No window is shown before its page has said it works (the Api's `page_ready`, after the page's script ran and its link
to Python answered). A window shown before that is a solid dark rectangle - for the full-screen cover, a black screen
with no way out. `show_main` waits for the main page (it is shown the moment the page is ready); `watch_main` gives the
page MAIN_READY_SEC (a start in the tray: MAIN_READY_HIDDEN_SEC) and otherwise calls `on_timeout(reason)` - the agent
then logs it, tries once more and shows a message box (agent.start_failed)."""
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

MAIN_SIZE, MAIN_MIN = (1100, 720), (560, 420)
CORNER_SIZE = (380, 140)
OVERLAY_BG = "#191817"   # solid, no transparency: the dark page background (resident/overlay.BACKGROUNDS has the others)
PAGES = ("main", "overlay", "corner")    # the pages that say when they are ready (webapi/shell.page_ready)
MAIN_READY_SEC = 8           # the main page must say it works within this once the window is wanted on screen ...
MAIN_READY_HIDDEN_SEC = 45   # ... or, started in the tray (at login the PC is busy), within this


@dataclass
class Windows:
    main: object = None
    corner: object = None
    overlay: object = None
    quitting: bool = False   # True once the agent really exits: then closing the main window is allowed
    publisher: object = None   # the main window's Publisher (R04); told when the window is shown / hidden
    overlay_ctl: object = None   # R08: resident.overlay.Overlay (what is on the cover), and the overlay window's Publisher
    overlay_publisher: object = None
    ready: dict = field(default_factory=lambda: {name: threading.Event() for name in PAGES})   # page -> said it works
    ready_hooks: dict = field(default_factory=lambda: {name: [] for name in PAGES})   # page -> [fn()] run when it does
    show_wanted: bool = False   # the main window was asked for before its page was ready: shown once it is
    lock: object = field(default_factory=threading.Lock)


def storage_dir() -> Path:
    """Where WebView2 keeps its profile - a fixed folder under the data folder (not a temp one per launch)."""
    from paths import DATA_DIR
    path = DATA_DIR / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create(base_url: str, background: str, js_api=None, overlay_background: str = OVERLAY_BG) -> Windows:
    """Make the three windows (hidden). `js_api` is the Api object the pages call (R02), or None for now."""
    import webview
    win = Windows()
    win.main = webview.create_window(
        "Lockdown", f"{base_url}/index.html", js_api=js_api, width=MAIN_SIZE[0], height=MAIN_SIZE[1],
        min_size=MAIN_MIN, hidden=True, background_color=background, text_select=False)
    win.corner = webview.create_window(
        "Lockdown notices", f"{base_url}/corner.html", js_api=js_api, width=CORNER_SIZE[0], height=CORNER_SIZE[1],
        frameless=True, on_top=True, focus=False, hidden=True, resizable=False, easy_drag=False, shadow=False,
        background_color=background)
    win.overlay = webview.create_window(      # (sized and placed by resident/overlay.OverlayHost, not "fullscreen")
        "Lockdown overlay", f"{base_url}/overlay.html", js_api=js_api, frameless=True, on_top=True,
        hidden=True, easy_drag=False, shadow=False, resizable=False, background_color=overlay_background)
    win.main.events.closing += lambda: close_means_hide(win)
    win.overlay.events.closing += lambda: win.quitting      # Alt+F4 does nothing (the agent really quitting is allowed)
    win.main.events.minimized += lambda: set_visible(win, False)
    win.main.events.restored += lambda: set_visible(win, True)
    return win


def attach_publisher(win: Windows, log=None):
    """Give the main window its Publisher (starts hidden: only `dirty` flags until it is shown). Stop it with
    `win.publisher.stop()` when the agent exits."""
    from resident.publisher import Publisher
    win.publisher = Publisher(win.main, visible=False, log=log)
    return win.publisher


def set_visible(win: Windows, visible: bool):
    """The main window is now shown (True) or hidden / minimised (False): the publisher stops or resumes pushing.
    pywebview has no "hidden" event, so show_main / close_means_hide call this themselves."""
    if win.publisher is not None:
        win.publisher.set_visible(visible)


def close_means_hide(win: Windows) -> bool:
    """The main window's close button: hide it and keep running in the tray. False tells pywebview not to close."""
    if win.quitting:
        return True
    win.main.hide()
    set_visible(win, False)
    return False


def show_main(win: Windows):
    """Bring the main window up (tray Open, a second launch). Before its page has said it works this only notes the wish:
    the window comes up the moment the page is ready (or, if it never is, the agent gives up on this run - watch_main)."""
    with win.lock:
        if not win.ready["main"].is_set():
            win.show_wanted = True
            return
    _show_now(win)


def _show_now(win: Windows):
    win.main.show()
    win.main.restore()
    set_visible(win, True)


def on_page_ready(win: Windows, name: str, fn):
    """Run fn() when page `name` says it is ready (at once if it already has)."""
    with win.lock:
        if not win.ready[name].is_set():
            win.ready_hooks[name].append(fn)
            return
    fn()


def page_ready(win: Windows, name: str) -> bool:
    """Page `name` says it works (webapi/shell.page_ready). The first time: its hooks run, and the main window comes up if
    it was asked for in the meantime. Returns False for an unknown name or a repeat."""
    if name not in PAGES:
        return False
    with win.lock:
        if win.ready[name].is_set():
            return False
        win.ready[name].set()
        hooks, win.ready_hooks[name] = win.ready_hooks[name], []
        show = name == "main" and win.show_wanted
        win.show_wanted = False if show else win.show_wanted
    for fn in hooks:
        try:
            fn()
        except Exception:
            pass
    if show:
        _show_now(win)
    return True


def watch_main(win: Windows, start_visible: bool, on_timeout, describe=None, visible_sec: float | None = None,
               hidden_sec: float | None = None, clock=time.monotonic, step: float = 0.2):
    """On a thread of its own: wait for the main page to say it works. Started on screen (or asked for later: tray Open,
    a second launch), it gets `visible_sec` (MAIN_READY_SEC) from that moment; started in the tray, `hidden_sec`
    (MAIN_READY_HIDDEN_SEC). Too late: on_timeout(reason) once - `describe()` adds what was seen (pywebview's events,
    the files served)."""
    visible_sec = MAIN_READY_SEC if visible_sec is None else visible_sec
    hidden_sec = MAIN_READY_HIDDEN_SEC if hidden_sec is None else hidden_sec
    start = clock()

    def run():
        wanted_at = start if start_visible else None
        while not win.ready["main"].wait(step):
            if win.quitting:
                return
            now = clock()
            if wanted_at is None and win.show_wanted:
                wanted_at = now
            deadline = start + hidden_sec
            if wanted_at is not None:
                deadline = min(deadline, wanted_at + visible_sec)
            if now >= deadline:
                waited = now - (wanted_at if wanted_at is not None else start)
                reason = f"the main window's page did not say it was ready within {waited:.0f} s"
                if describe is not None:
                    try:
                        reason += f" ({describe()})"
                    except Exception:
                        pass
                on_timeout(reason)
                return

    thread = threading.Thread(target=run, name="lockdown-main-ready", daemon=True)
    thread.start()
    return thread


def open_challenge(win: Windows, data: dict):
    """Open the Anti-Bypass challenge for `data` ({needs_challenge, ticket, ...}) where the owner can see it: on the
    full-screen cover while that is up (the main window would sit behind it), otherwise in the main window."""
    cover, cover_publisher = getattr(win, "overlay_ctl", None), getattr(win, "overlay_publisher", None)
    if cover is not None and getattr(cover, "on_screen", cover.shown) and cover_publisher is not None:
        cover_publisher.publish("challenge_open", data)
        return
    show_main(win)
    win.publisher.publish("challenge_open", data)


def pump_open_events(events: queue.Queue, win: Windows):
    """A second launch (single_instance) puts "open" into `events`: show the main window. Runs on its own thread."""
    def run():
        while True:
            if events.get() == "open":
                show_main(win)
    threading.Thread(target=run, name="lockdown-open-events", daemon=True).start()


def quit_agent(win: Windows):
    """Really close everything (the GUI loop then ends)."""
    win.quitting = True
    host = getattr(getattr(win, "overlay_ctl", None), "host", None)
    if host is not None:         # (R08: the covers on the other monitors are windows too)
        try:
            host.close_covers()
        except Exception:
            pass
    for w in (win.main, win.corner, win.overlay):
        events = getattr(w, "events", None)
        if events is not None and hasattr(events, "shown") and not events.shown.is_set():
            continue             # (never made - pywebview gave up on it: destroy() would wait 20 s for nothing)
        try:
            w.destroy()
        except Exception:
            pass


def start(win_ready=None, storage: Path | None = None):
    """Run the GUI loop until all windows are gone. EdgeChromium = WebView2; our own server, not pywebview's."""
    import webview
    webview.start(win_ready, gui="edgechromium", http_server=False, private_mode=False,
                  storage_path=str(storage or storage_dir()))
