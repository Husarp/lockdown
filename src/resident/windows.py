"""The three windows of the new UI, all made hidden when the agent starts: main (closing it only hides it), corner
(small, no frame, always on top, never takes focus: notices and reminder popups) and overlay (full screen, no frame,
always on top: bedtime / strict break). Win32 / pywebview glue only - kept thin; pywebview is imported when used."""
import queue
import threading
from dataclasses import dataclass
from pathlib import Path

MAIN_SIZE, MAIN_MIN = (1100, 720), (560, 420)
CORNER_SIZE = (380, 140)
OVERLAY_BG = "#101418"   # solid, no transparency (R08 may set it per theme)


@dataclass
class Windows:
    main: object = None
    corner: object = None
    overlay: object = None
    quitting: bool = False   # True once the agent really exits: then closing the main window is allowed
    publisher: object = None   # the main window's Publisher (R04); told when the window is shown / hidden


def storage_dir() -> Path:
    """Where WebView2 keeps its profile - a fixed folder under the data folder (not a temp one per launch)."""
    from paths import DATA_DIR
    path = DATA_DIR / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return path


def create(base_url: str, background: str, js_api=None) -> Windows:
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
    win.overlay = webview.create_window(
        "Lockdown overlay", f"{base_url}/overlay.html", js_api=js_api, frameless=True, on_top=True,
        fullscreen=True, hidden=True, easy_drag=False, shadow=False, background_color=OVERLAY_BG)
    win.main.events.closing += lambda: close_means_hide(win)
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
    """Bring the main window up (tray Open, a second launch)."""
    win.main.show()
    win.main.restore()
    set_visible(win, True)


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
    for w in (win.main, win.corner, win.overlay):
        try:
            w.destroy()
        except Exception:
            pass


def start(win_ready=None, storage: Path | None = None):
    """Run the GUI loop until all windows are gone. EdgeChromium = WebView2; our own server, not pywebview's."""
    import webview
    webview.start(win_ready, gui="edgechromium", http_server=False, private_mode=False,
                  storage_path=str(storage or storage_dir()))
