"""Main window: sidebar navigation + content area."""
import queue
import time

import customtkinter as ctk

from db import Database
from gui.blocking import BlockingPage
from gui.tray import Tray
from service import HEARTBEAT_KEY

# (sidebar label, phase in which the page gets built; None = available now)
PAGES = [
    ("Dashboard", 4),
    ("Blocking", None),
    ("Anti-Bypass", 7),
    ("Screen Time", 4),
    ("Network Log", 5),
    ("Modes", 6),
    ("Notifications", 6),
    ("Settings", 8),
]
SERVICE_TIMEOUT_SEC = 15


class LockdownApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Lockdown")
        self.geometry("1100x720")
        self.minsize(900, 560)

        self.db = Database()
        self.service_running = False
        self.pages: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(0, weight=1)

        # Tray callbacks arrive on the tray's thread; hand them to Tk via a queue.
        self.tray_events: queue.Queue = queue.Queue()
        self.tray = Tray(on_open=lambda: self.tray_events.put("open"), on_exit=lambda: self.tray_events.put("exit"))
        self.tray.start()
        self.protocol("WM_DELETE_WINDOW", self.withdraw)  # close = minimize to tray

        self.show_page("Blocking")
        self._poll_tray()
        self._poll_status()

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, width=190, corner_radius=0)
        bar.grid(row=0, column=0, sticky="nsw")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="LOCKDOWN", font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(24, 20), sticky="w")
        for i, (name, _) in enumerate(PAGES, start=1):
            btn = ctk.CTkButton(bar, text=name, anchor="w", height=36, corner_radius=6,
                                fg_color="transparent", text_color=("gray10", "gray90"),
                                hover_color=("gray75", "gray25"), command=lambda n=name: self.show_page(n))
            btn.grid(row=i, column=0, padx=10, pady=2, sticky="ew")
            self.nav_buttons[name] = btn
        bar.grid_rowconfigure(len(PAGES) + 1, weight=1)
        self.status_label = ctk.CTkLabel(bar, text="", justify="left", anchor="w")
        self.status_label.grid(row=len(PAGES) + 2, column=0, padx=20, pady=20, sticky="w")

    def show_page(self, name: str):
        if name not in self.pages:
            phase = dict(PAGES)[name]
            if phase is None:
                page = BlockingPage(self.content, self)
            else:
                page = ctk.CTkFrame(self.content, fg_color="transparent")
                ctk.CTkLabel(page, text=name, font=ctk.CTkFont(size=24, weight="bold")).pack(
                    anchor="w", padx=30, pady=(24, 8))
                ctk.CTkLabel(page, text=f"Coming in Phase {phase}.", text_color="gray60").pack(anchor="w", padx=30)
            page.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = page
        self.pages[name].tkraise()
        for n, btn in self.nav_buttons.items():
            btn.configure(fg_color=("gray75", "gray25") if n == name else "transparent")

    def _poll_tray(self):
        while not self.tray_events.empty():
            event = self.tray_events.get()
            if event == "open":
                self.deiconify()
                self.lift()
                self.focus_force()
            elif event == "exit":
                self.tray.stop()
                self.destroy()
                return
        self.after(200, self._poll_tray)

    def _poll_status(self):
        heartbeat = float(self.db.get_setting(HEARTBEAT_KEY, "0"))
        running = time.time() - heartbeat < SERVICE_TIMEOUT_SEC
        blocked = len(self.db.list_items())
        if running != self.service_running:
            self.service_running = running
            if "Blocking" in self.pages:
                self.pages["Blocking"].refresh()  # status column depends on it
        self.status_label.configure(
            text=("● Service running" if running else "● Service not running"),
            text_color=("#3fb950" if running else "#f85149"))
        self.tray.update(running, f"{blocked} sites blocked")
        self.after(3000, self._poll_status)
