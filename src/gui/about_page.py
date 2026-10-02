"""About: which version this is, where to get a newer one, what Lockdown can do, and where it keeps things."""
import subprocess
import threading
import webbrowser
from pathlib import Path

import customtkinter as ctk

import updates
from gui import theme
from gui.components import Card, hairline, page_head
from paths import APP_DIR, DATA_DIR, LOG_PATH
from trusted_time import now_from_db
from version import RELEASED, VERSION

MUTED = theme.MUTED

# What the app does, in the order you would meet it. Doubles as the tutorial: every line is something you can
# go and do right now.
FEATURES = [
    ("Blocking", [
        "Block a site, an app or a whole category - and tick any number of blockers on it at once.",
        "By time (hours you pick, with minutes of allowance if you want a way out), a time limit per day / "
        "week / month, an opening limit, permanent, or temporary.",
        "Groups: one set of rules over several sites and apps, with a limit they share.",
        "Choose what happens: an app is closed, minimised or cut off the internet; a site can't load, has its "
        "tab closed, or sends the browser back.",
        "Disable something instead of deleting it - it keeps its rules and enforces nothing.",
    ]),
    ("Keeping yourself to it", [
        "Anti-Bypass: anything that loosens a block needs a phrase typed out first (no pasting), only inside "
        "the hours you allow, and after a wait if you set one.",
        "The enforcer runs as a Windows service with its own trusted clock - changing the Windows clock does "
        "nothing.",
        "Emergency unlock: a few uses a week, for when you really need something back.",
        "Pause my blocks (Anti-Bypass page or the tray): everything on your list unblocked for 30 min to the rest "
        "of the day, behind the challenge - protection lists and blocked words stay on.",
    ]),
    ("Knowing where the time goes", [
        "Screen Time: what you used, for how long, by category, with a timeline of the day.",
        "Network Log: which app talked to which site, in the last hour.",
        "Dashboard: today at a glance - limits, what is coming up, the last 7 days.",
    ]),
    ("Looking after yourself", [
        "Reminders: bedtime, breaks, 20-20-20, and your own - each saying whatever you want it to say.",
        "Modes: a named set of blocks you turn on for a while (Deep work, Study), optionally locked.",
        "Protection lists: scam, phishing, malware and adult sites, blocked by Lockdown's own DNS filter.",
    ]),
]


class AboutPage(ctk.CTkScrollableFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        page_head(self, "About")

        card = Card(self, "This version")
        card.pack(fill="x", pady=(0, 14))
        line = ctk.CTkFrame(card.body, fg_color="transparent")
        line.pack(fill="x", pady=(4, 0))
        ctk.CTkLabel(line, text=f"Lockdown {VERSION}", font=theme.semi(15)).pack(side="left")
        ctk.CTkLabel(line, text=f"released {RELEASED}", text_color=MUTED).pack(side="left", padx=10)
        self.update_note = ctk.CTkLabel(card.body, text="", text_color=MUTED, anchor="w", justify="left")
        self.update_note.pack(anchor="w", pady=(6, 0))
        buttons = ctk.CTkFrame(card.body, fg_color="transparent")
        buttons.pack(anchor="w", pady=(8, 2))
        if updates.repo_page():
            # APP-STANDARDS 2: the switch, the version with Check now, GitHub (the releases page), and Get update
            # once there is one. A failed update shows Try again next to GitHub - nothing opens by itself.
            self.check_btn = ctk.CTkButton(buttons, text="Check now", width=120, command=self._check)
            self.check_btn.pack(side="left", padx=(0, 8))
            # only appears once a newer version has actually been found (see show_found)
            self.get_btn = ctk.CTkButton(buttons, text="Get update", width=130, command=self._get)
            # only after an update failed (see _failed)
            self.retry_btn = ctk.CTkButton(buttons, text="Try again", width=110, command=self._retry)
            self.page_btn = ctk.CTkButton(buttons, text="GitHub", width=100, **theme.OUTLINE,
                                          command=lambda: webbrowser.open(updates.releases_page(self.found)))
            self.page_btn.pack(side="left")
            self.bar = ctk.CTkProgressBar(card.body, height=8)
            self.bar.set(0)
            self.found = None
            self.downloading = False
            self.failed_file = None   # the installer that downloaded but didn't start (Try again runs it again)
            auto = ctk.CTkCheckBox(card.body, text=f"Check for updates automatically (when Lockdown starts or you "
                                                   f"open it, and every {updates.EVERY_HOURS} hours)",
                                   checkbox_width=18, checkbox_height=18,
                                   command=lambda: self._set(updates.AUTO_KEY, auto.get()))
            auto.pack(anchor="w", pady=(10, 0))
            tell = ctk.CTkCheckBox(card.body, text="Tell me when a new version is found",
                                   checkbox_width=18, checkbox_height=18,
                                   command=lambda: self._set(updates.NOTIFY_KEY, tell.get()))
            tell.pack(anchor="w", pady=(4, 0))
            auto.select() if updates.auto_on(self.app.db) else auto.deselect()
            tell.select() if updates.notify_on(self.app.db) else tell.deselect()
        else:
            ctk.CTkLabel(buttons, text="No repository set yet, so there is nowhere to check for updates.",
                         text_color=MUTED).pack(side="left")

        card = Card(self, "What Lockdown can do")
        card.pack(fill="x", pady=(0, 14))
        for n, (heading, lines) in enumerate(FEATURES):
            if n:
                hairline(card.body).pack(fill="x", pady=8)
            ctk.CTkLabel(card.body, text=heading, font=theme.semi(13), anchor="w").pack(anchor="w", pady=(2, 4))
            for text in lines:
                row = ctk.CTkFrame(card.body, fg_color="transparent")
                row.pack(anchor="w", fill="x")
                ctk.CTkLabel(row, text="•", text_color=theme.ACCENT, width=14, anchor="w").pack(side="left",
                                                                                                anchor="n")
                ctk.CTkLabel(row, text=text, text_color=MUTED, justify="left", anchor="w",
                             wraplength=720).pack(side="left", anchor="w")

        card = Card(self, "Where things are")
        card.pack(fill="x", pady=(0, 14))
        for label, path, openable in (("Your settings, blocks and history", DATA_DIR, True),
                                      ("The program", APP_DIR, True),
                                      ("The log", LOG_PATH, False)):
            row = ctk.CTkFrame(card.body, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, width=210, anchor="w", text_color=MUTED).pack(side="left")
            ctk.CTkLabel(row, text=str(path), anchor="w").pack(side="left")
            if openable:
                ctk.CTkButton(row, text="Open", width=64, height=26, **theme.SECONDARY,
                              command=lambda p=path: self._open(p)).pack(side="right")
        ctk.CTkLabel(card.body, text="An update never touches the data folder; uninstalling only deletes it if "
                                     "you tick the box.", text_color=MUTED, wraplength=720, justify="left",
                     anchor="w").pack(anchor="w", pady=(8, 2))

    # ---------- updates ----------

    def _check(self):
        self.check_btn.configure(state="disabled")
        self.update_note.configure(text="Asking GitHub...", text_color=MUTED)
        threading.Thread(target=self._ask, daemon=True).start()   # the network, off the Tk thread

    # The worker threads below never call Tk (not even self.after - that is Tcl from the wrong thread, a crash /
    # hang source): they hand results to the window's call queue, which runs them on the Tk thread.

    def _ask(self):
        found = updates.latest_release()
        self.app.call_soon(self._checked, found)

    def _checked(self, found):
        self.check_btn.configure(state="normal")
        if found:   # (kept as well: the banner and the tray say the same as this page)
            updates.checked(self.app.db, now_from_db(self.app.db), found)
        self.show_found(found)
        self.app.refresh_update(popup=False)

    def show_found(self, found):
        """What a check found - from the button here, or kept from the automatic one (see on_show)."""
        self.found = found
        self.retry_btn.pack_forget()   # (after a failed update: a fresh answer replaces Try again)
        if not found:
            self.update_note.configure(text="Couldn't ask GitHub just now - no connection, or no release "
                                            "published yet.", text_color=theme.WARNING)
        elif updates.can_install(found):
            updates.said(self.app.db, found["version"])   # you have seen it: no notice about this one again
            size = f" ({found['size'] / 1048576:.0f} MB)" if found["size"] else ""
            self.update_note.configure(text=f"Lockdown {found['version']} is out{size}.",
                                       text_color=theme.ALLOWED)
            self.get_btn.pack(side="left", padx=(0, 8), before=self.page_btn)   # the thing to do, first
        elif found["newer"]:      # a release with no installer attached: the page is all we can offer
            self.update_note.configure(text=f"Lockdown {found['version']} is out - open the GitHub page to "
                                            "get it.", text_color=theme.ALLOWED)
        else:
            self.update_note.configure(text="This is the newest version.", text_color=theme.ALLOWED)

    # ---------- downloading and installing it ----------

    def _get(self):
        if not updates.can_install(self.found) or self.downloading:
            return
        self.downloading = True
        self.failed_file = None
        self.retry_btn.pack_forget()
        self.get_btn.configure(state="disabled")
        self.check_btn.configure(state="disabled")
        self.bar.set(0)
        self.bar.pack(fill="x", pady=(8, 0))
        self.update_note.configure(text=f"Downloading Lockdown {self.found['version']}...",
                                   text_color=MUTED)
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        found = self.found
        try:
            dest = updates.download_path(found["version"])   # (deleted at the next start: updates.remove_downloads)
            updates.download(found["asset"], dest, progress=self._progress)
        except Exception as error:
            self.app.call_soon(self._failed, error)
            return
        self.app.call_soon(self._got, dest)

    def _progress(self, done, total):
        """(worker thread, once per downloaded chunk) Only the newest value reaches the bar - no flood."""
        if total:
            self.app.call_latest("update-progress", self.bar.set, done / total)

    def _failed(self, error, dest=None):
        """The in-app update failed (the download, or starting the installer): say so, with Try again and GitHub
        next to each other - nothing opens by itself; you choose (APP-STANDARDS 3)."""
        self.downloading = False
        self.failed_file = dest
        self.bar.pack_forget()
        self.get_btn.pack_forget()
        self.get_btn.configure(state="normal")
        self.check_btn.configure(state="normal")
        self.retry_btn.pack(side="left", padx=(0, 8), before=self.page_btn)
        what = (f"The installer didn't start ({error})" if dest else
                f"The download didn't finish ({type(error).__name__} - no connection, or GitHub didn't answer)")
        self.update_note.configure(text=f"{what}. Press Try again, or get it from GitHub.",
                                   text_color=theme.WARNING)

    def _retry(self):
        """Try again: start the installer that already downloaded, or download it again."""
        if self.downloading:
            return
        self.retry_btn.pack_forget()
        if self.failed_file is not None and Path(self.failed_file).exists():
            self.downloading = True
            self.get_btn.configure(state="disabled")
            self.check_btn.configure(state="disabled")
            self._got(self.failed_file)
        else:
            self.show_found(self.found)
            self._get()

    def _got(self, dest):
        self.bar.set(1)
        self.update_note.configure(text="Starting the installer - Lockdown will close. Windows will ask for "
                                        "permission, and may warn that the installer is unsigned.",
                                   text_color=MUTED)
        try:
            updates.install(dest)
        except Exception as error:   # it used to fail silently and sit at 100%
            self._failed(error, dest)
            return
        self.after(1500, self._close_for_update)

    def _close_for_update(self):
        """Not the tray's Exit: that one marks Lockdown as deliberately quit and keeps it off until you log in
        again. An update is meant to come straight back, so only the window and the tray icon go."""
        self.app._shutdown()
        self.app.destroy()

    def _set(self, key, value):
        self.app.db.set_setting(key, "1" if value else "0")

    @staticmethod
    def _open(path):
        subprocess.Popen(["explorer", str(path)])

    def on_show(self):
        """A newer version the automatic check already found is offered here straight away - no need to press
        "Check for updates" first."""
        if updates.repo_page() and not self.downloading:
            found = updates.available(self.app.db)
            if found and (self.found or {}).get("version") != found["version"]:
                self.show_found(found)
