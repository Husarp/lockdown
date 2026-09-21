"""About: which version this is, where to get a newer one, what Lockdown can do, and where it keeps things."""
import subprocess
import threading
import webbrowser

import customtkinter as ctk

import updates
from gui import theme
from gui.components import Card, hairline, page_head
from paths import APP_DIR, DATA_DIR, LOG_PATH
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
            self.check_btn = ctk.CTkButton(buttons, text="Check for updates", width=150, command=self._check)
            self.check_btn.pack(side="left", padx=(0, 8))
            ctk.CTkButton(buttons, text="Open the GitHub page", width=170, **theme.OUTLINE,
                          command=lambda: webbrowser.open(updates.repo_page())).pack(side="left")
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

    def _ask(self):
        found = updates.latest_release()
        self.after(0, self._checked, found)

    def _checked(self, found):
        self.check_btn.configure(state="normal")
        if not found:
            self.update_note.configure(text="Couldn't ask GitHub just now - no connection, or no release "
                                            "published yet.", text_color=theme.WARNING)
        elif found["newer"]:
            self.update_note.configure(text=f"Lockdown {found['version']} is out - click to download it.",
                                       text_color=theme.ALLOWED, cursor="hand2")
            self.update_note.bind("<Button-1>", lambda e, u=found["url"]: webbrowser.open(u))
        else:
            self.update_note.configure(text="This is the newest version.", text_color=theme.ALLOWED)

    @staticmethod
    def _open(path):
        subprocess.Popen(["explorer", str(path)])

    def on_show(self):
        pass
