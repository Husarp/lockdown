"""What the tray icon's menu does in the new agent (Tray on_mode / on_update / on_pause / on_resume), and how the
agent opens a page of the main window (`hub.open_page`).

Anything that loosens a block goes through the GuardService, exactly as the page's own Api does: replacing or stopping
a LOCKED mode asks for the challenge (the Anti-Bypass challenge opens in the main window - `challenge_open` push), a
pause is started on the Anti-Bypass page (its card; the Api asks for the challenge there), and ending a pause is free.

The handlers run on the tray's thread (pystray): they do their little database work there and return."""
import webbrowser

import modes
import pause
import updates

# The legacy page names workers use ("See the week" -> "Screen Time") -> the router ids of the new window.
PAGE_IDS = {"Dashboard": "dashboard", "Blocking": "blocking", "Anti-Bypass": "antibypass", "Screen Time": "screentime",
            "Network Log": "netlog", "Modes": "modes", "Reminders": "reminders", "Notifications": "notifications",
            "Settings": "settings", "About": "about"}


def page_id(name: str) -> str | None:
    """A legacy page name or a router id -> the router id (None for an unknown page)."""
    if name in PAGE_IDS.values():
        return name
    return PAGE_IDS.get(name)


def mode_on_text(mode: dict, end) -> str:
    return f"{mode['name']} mode on" + (f" until {end:%H:%M}" if end else "")


def locked_by_hand(db, now):
    """The mode that was started by hand and is locked (replacing or stopping it needs the challenge), or None."""
    state = modes.active(db, now)
    return state if state and state["locked"] and not state["scheduled"] else None


def start_mode(hub, db, mode_id: str, until=None, force: bool = False) -> bool:
    """Start a mode now and say so. Not when the mode is gone, nor while another mode started by hand is locked on
    (unless `force`: the challenge was passed). True if it was started."""
    mode = next((m for m in modes.load(db) if m["id"] == mode_id), None)
    now = hub.clock.now(db)
    if mode is None or (not force and locked_by_hand(db, now)):
        return False
    modes.start(db, mode["id"], now, until, False)
    state = modes.active(db, now)
    hub.notify(mode_on_text(mode, state["until"] if state else until), force=True)
    _poke(hub)
    return True


def stop_mode(hub, db, force: bool = False):
    now = hub.clock.now(db)
    state = modes.active(db, now)
    modes.stop(db, now, force)
    if state and not state["scheduled"]:
        hub.notify(f"{state['mode']['name']} mode off", force=True)
    _poke(hub)


def _poke(hub):
    if hub.status_worker is not None:
        hub.status_worker.poke()        # the tray and the mute rule see the change at once


class TrayActions:
    """guard: webapi GuardService.   show_main(): bring the main window up.   publish_main(event, payload): push to it."""

    def __init__(self, hub, guard, show_main, publish_main, browse=webbrowser.open):
        self.hub, self.guard = hub, guard
        self.show_main, self.publish_main, self.browse = show_main, publish_main, browse

    # ------------------------------------------------------------------ the main window
    def open_page(self, name: str, **extra):
        """Bring the main window up on a page ("Screen Time" or "screentime")."""
        page = page_id(name)
        self.show_main()
        if page:
            self.publish_main("show_page", {"page": page, **extra})

    def open_challenge(self, data: dict):
        """Show the main window with the challenge open (`data`: the GuardService's {needs_challenge, ticket, ...})."""
        self.show_main()
        self.publish_main("challenge_open", data)

    def _guarded(self, changes: list[str], action, force: bool = False):
        reply = self.guard.request(changes, action, force=force)
        data = reply.get("data") if reply.get("ok") else None
        if isinstance(data, dict) and data.get("needs_challenge"):
            self.open_challenge(data)

    # ------------------------------------------------------------------ the tray menu
    def mode(self, mode_id):
        """Modes > a mode (start it, until stopped) or "Stop mode" (None)."""
        hub = self.hub
        db = hub.open_db()
        try:
            now = hub.clock.now(db)
            locked = locked_by_hand(db, now)
            if mode_id:
                mode = next((m for m in modes.load(db) if m["id"] == mode_id), None)
                if mode is None:
                    hub.notify("That mode doesn't exist any more.", force=True)
                elif locked:
                    self._guarded([f"End the locked {locked['mode']['name']} mode (locked until "
                                   f"{locked['until']:%H:%M}) and start {mode['name']}"],
                                  lambda: self._run(start_mode, mode_id, force=True), force=True)
                else:
                    start_mode(hub, db, mode_id)
            elif locked:
                self._guarded([f"Stop the locked {locked['mode']['name']} mode (locked until {locked['until']:%H:%M})"],
                              lambda: self._run(stop_mode, force=True), force=True)
            else:
                stop_mode(hub, db)
        finally:
            db.close()

    def _run(self, fn, *args, **kwargs):
        """Run a stored (challenge-gated) action with a database of its own: it may run on another thread, later."""
        db = self.hub.open_db()
        try:
            fn(self.hub, db, *args, **kwargs)
        finally:
            db.close()

    def update(self):
        """"Install update": the About page's flow (R17 sets hub.install_update), else the page itself."""
        db = self.hub.open_db()
        try:
            found = updates.available(db)
        finally:
            db.close()
        if not found:
            return
        if not updates.can_install(found):      # a release with no installer attached: only the page can help
            self.browse(found["url"])
        elif self.hub.install_update:
            self.hub.install_update(found)
        else:
            self.open_page("About")

    def pause(self):
        """"Pause my blocks...": the card on the Anti-Bypass page (starting a pause asks for the challenge there)."""
        self.open_page("antibypass", focus="pause")

    def resume(self):
        """"Resume blocking": your blocks are back at once. Never needs the challenge."""
        db = self.hub.open_db()
        try:
            pause.resume(db)
        finally:
            db.close()
        _poke(self.hub)
