"""Notifications page (alert settings, weekly summary) and the in-app popup. The alert messages are in a section
you open (built the first time) - fewer widgets = no lag. (Blocked visits are on the Dashboard.)"""
import customtkinter as ctk

from gui import theme
from gui.components import Collapsible, Segmented, help_icon, page_head, accent_bar
from gui.widgets import Corner

import alerts
import digest
from rules import DAY_NAMES

MUTED = theme.MUTED
POPUP_MS = 8000


class NotificationsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.db, self.draft = app.db, app.draft
        page_head(self, "Notifications").pack(
            anchor="w", padx=30, pady=(16, 12))
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self.msg_entries: dict[str, ctk.CTkEntry] = {}
        self._build_settings(body)
        self.load()

    def _build_settings(self, parent):
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=(0, 12))
        box.grid_columnconfigure(1, weight=1)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, padx=16, pady=(12, 2), sticky="w")
        accent_bar(head).pack(side="left", padx=(0, 9))
        ctk.CTkLabel(head, text="Blocked Visit Alerts", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        ctk.CTkLabel(box, text="Notify me when I try to open a site (or start an app) that is:", text_color=MUTED).grid(
            row=1, column=0, columnspan=2, padx=16, pady=(0, 6), sticky="w")

        # each alert as a bordered, padded cell in a 4-column grid (design 3i); a ticked cell gets an accent edge
        self.enabled_vars: dict[str, ctk.BooleanVar] = {}
        self.cells: dict[str, ctk.CTkFrame] = {}
        checks = ctk.CTkFrame(box, fg_color="transparent")
        checks.grid(row=2, column=0, columnspan=2, padx=16, sticky="ew")
        for col in range(4):
            checks.grid_columnconfigure(col, weight=1, uniform="a")
        for i, (reason, (label, _)) in enumerate(alerts.REASONS.items()):
            var = ctk.BooleanVar()
            self.enabled_vars[reason] = var
            cell = ctk.CTkFrame(checks, fg_color=theme.BG, border_width=1, border_color=theme.BORDER, corner_radius=3)
            cell.grid(row=i // 4, column=i % 4, padx=4, pady=4, sticky="ew")
            ctk.CTkCheckBox(cell, text=label, variable=var, checkbox_width=18, checkbox_height=18,
                            command=lambda r=reason: self._alert_toggled(r)).pack(anchor="w", padx=10, pady=9)
            self.cells[reason] = cell
        messages = Collapsible(box, "Messages", self._build_messages, note="change what each alert says")
        messages.grid(row=3, column=0, columnspan=2, padx=16, pady=(8, 0), sticky="ew")
        row = 3

        opts = ctk.CTkFrame(box, fg_color="transparent")
        opts.grid(row=row + 1, column=0, columnspan=2, padx=16, pady=(10, 4), sticky="w")
        ctk.CTkLabel(opts, text="Don't repeat for the same site within").pack(side="left")
        self.cooldown = ctk.CTkOptionMenu(opts, width=90, values=[f"{m} min" for m in alerts.COOLDOWN_OPTIONS],
                                          command=lambda v: self.draft.set_setting("notify.cooldown_min", v.split()[0]))
        self.cooldown.pack(side="left", padx=8)

        fmt_row = ctk.CTkFrame(box, fg_color="transparent")
        fmt_row.grid(row=row + 2, column=0, columnspan=2, padx=16, pady=4, sticky="w")
        ctk.CTkLabel(fmt_row, text="Show as").pack(side="left")
        self.fmt = Segmented(fmt_row, values=list(alerts.FORMATS.values()), command=lambda v: self.draft.set_setting(
            "notify.format", next(k for k, lbl in alerts.FORMATS.items() if lbl == v)))
        self.fmt.pack(side="left", padx=8)
        help_icon(fmt_row, "Windows notifications clear themselves from the notification centre (the bell) a few "
                           "seconds later, so these one-time alerts don't pile up as unread. Only Lockdown's own "
                           "notifications are cleared.").pack(side="left", padx=4)
        ctk.CTkLabel(box, text="Per-site override: the Alerts column in Blocking > Overview.", text_color=MUTED).grid(
            row=row + 3, column=0, columnspan=2, padx=16, pady=(4, 12), sticky="w")
        # the two lower cards side by side (design 3i): Upcoming blocks (wider) | Weekly summary
        cols = ctk.CTkFrame(parent, fg_color="transparent")
        cols.pack(fill="x")
        cols.grid_columnconfigure(0, weight=4, uniform="n")
        cols.grid_columnconfigure(1, weight=3, uniform="n")
        left = ctk.CTkFrame(cols, fg_color="transparent")
        left.grid(row=0, column=0, sticky="new", padx=(0, 7))
        right = ctk.CTkFrame(cols, fg_color="transparent")
        right.grid(row=0, column=1, sticky="new", padx=(7, 0))
        self._build_warnings(left)
        self._build_digest(right)

    def _alert_toggled(self, reason: str):
        on = self.enabled_vars[reason].get()
        self.draft.set_setting(f"notify.enabled.{reason}", "1" if on else "0")
        self._paint_cells()

    def _paint_cells(self):
        for reason, cell in self.cells.items():
            on = self.enabled_vars[reason].get()
            cell.configure(border_color=(theme._mix(theme.ACCENT[0], theme.BG[0], 0.45),
                                         theme._mix(theme.ACCENT[1], theme.BG[1], 0.45)) if on else theme.BORDER)

    def _build_messages(self, parent):
        parent.grid_columnconfigure(1, weight=1)
        for row, (reason, (label, _)) in enumerate(alerts.REASONS.items()):
            ctk.CTkLabel(parent, text=label, anchor="w", width=230).grid(row=row, column=0, pady=3, sticky="w")
            msg = ctk.CTkEntry(parent)
            msg.grid(row=row, column=1, pady=3, sticky="ew")
            msg.bind("<KeyRelease>", lambda _e, r=reason, m=msg: self.draft.set_setting(
                f"notify.msg.{r}", m.get().strip() or alerts.DEFAULT_MESSAGES[r]))
            msg.insert(0, self.draft.settings[f"notify.msg.{reason}"])
            self.msg_entries[reason] = msg
            ctk.CTkButton(parent, text="Reset", width=70, height=28, **theme.OUTLINE,
                          command=lambda r=reason: self._reset_message(r)).grid(row=row, column=2, padx=(8, 0), pady=3)
        ctk.CTkLabel(parent, text="Placeholders: {site}  {reason}  {until}", text_color=MUTED).grid(
            row=len(alerts.REASONS), column=1, sticky="w")

    def _reset_message(self, reason: str):
        entry = self.msg_entries[reason]
        entry.delete(0, "end")
        entry.insert(0, alerts.DEFAULT_MESSAGES[reason])
        self.draft.set_setting(f"notify.msg.{reason}", alerts.DEFAULT_MESSAGES[reason])

    def _build_digest(self, parent):
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=(0, 12))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(anchor="w", padx=16, pady=(12, 6))
        accent_bar(head).pack(side="left", padx=(0, 9))
        ctk.CTkLabel(head, text="Weekly Summary", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.pack(anchor="w", padx=16, pady=(0, 12))
        self.digest_switch = ctk.CTkSwitch(line, text="Every", command=self._save_digest)   # "Every Sunday at 19:00"
        self.digest_switch.pack(side="left")
        self.digest_day = ctk.CTkOptionMenu(line, width=120, values=DAY_NAMES, command=lambda v: self._save_digest())
        self.digest_day.pack(side="left", padx=8)
        ctk.CTkLabel(line, text="at").pack(side="left")
        self.digest_time = ctk.CTkOptionMenu(line, width=90, values=[f"{h:02d}:00" for h in range(24)],
                                             command=lambda v: self._save_digest())
        self.digest_time.pack(side="left", padx=8)
        help_icon(line, "Screen time compared with the week before, blocked visits, your top app and site, the days "
                        "within your goal and your streaks.").pack(side="left", padx=4)
        g = lambda k: digest.get(self.db, k)
        self.digest_switch.select() if g("digest.enabled") == "1" else self.digest_switch.deselect()
        self.digest_day.set(DAY_NAMES[int(g("digest.day"))])
        self.digest_time.set(g("digest.time"))

    def _save_digest(self):
        self.db.set_setting("digest.enabled", "1" if self.digest_switch.get() else "0")
        self.db.set_setting("digest.day", str(DAY_NAMES.index(self.digest_day.get())))
        self.db.set_setting("digest.time", self.digest_time.get())

    def _build_warnings(self, parent):
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=(0, 12))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(anchor="w", padx=16, pady=(12, 6))
        accent_bar(head).pack(side="left", padx=(0, 9))
        ctk.CTkLabel(head, text="Upcoming Blocks", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        warn = ctk.CTkFrame(box, fg_color="transparent")
        warn.pack(anchor="w", padx=16, pady=4)
        self.warn_switch = ctk.CTkSwitch(warn, text="Warn me", command=lambda: self.draft.set_setting(
            "notify.warn.enabled", "1" if self.warn_switch.get() else "0"))
        self.warn_switch.pack(side="left")
        self.warn_minutes = ctk.CTkOptionMenu(warn, width=90, values=[f"{m} min" for m in alerts.WARN_MINUTE_OPTIONS],
                                              command=lambda v: self.draft.set_setting("notify.warn.minutes", v.split()[0]))
        self.warn_minutes.pack(side="left", padx=8)
        ctk.CTkLabel(warn, text="before something gets blocked").pack(side="left")
        repeat = ctk.CTkFrame(box, fg_color="transparent")
        repeat.pack(anchor="w", padx=16, pady=4)
        ctk.CTkLabel(repeat, text="While I'm using it, remind me again every").pack(side="left")
        self.repeat = ctk.CTkOptionMenu(repeat, width=90, values=[self._repeat_label(m) for m in alerts.REPEAT_OPTIONS],
                                        command=lambda v: self.draft.set_setting(
                                            "notify.warn.repeat_min", "0" if v == "never" else v.split()[0]))
        self.repeat.pack(side="left", padx=8)
        ctk.CTkLabel(repeat, text="until it's blocked", text_color=MUTED).pack(side="left")
        self.started_switch = ctk.CTkSwitch(box, text="Notify me when a block starts", command=lambda: self.draft.set_setting(
            "notify.started.enabled", "1" if self.started_switch.get() else "0"))
        self.started_switch.pack(anchor="w", padx=16, pady=(4, 6))
        ctk.CTkLabel(box, text="Blocks from the same group are announced together (\"Night schedule starts in 5 min: ...\").",
                     text_color=MUTED).pack(anchor="w", padx=16, pady=(0, 12))

    @staticmethod
    def _repeat_label(minutes: int) -> str:
        return "never" if minutes == 0 else f"{minutes} min"

    def load(self):
        """Show the draft's values (on start and after Discard)."""
        s = self.draft.settings
        for reason, var in self.enabled_vars.items():
            var.set(s[f"notify.enabled.{reason}"] == "1")
        self._paint_cells()
        for reason, entry in self.msg_entries.items():
            entry.delete(0, "end")
            entry.insert(0, s[f"notify.msg.{reason}"])
        self.cooldown.set(f"{s['notify.cooldown_min']} min")
        self.fmt.set(alerts.FORMATS[s["notify.format"]])
        self.warn_switch.select() if s["notify.warn.enabled"] == "1" else self.warn_switch.deselect()
        self.warn_minutes.set(f"{s['notify.warn.minutes']} min")
        self.repeat.set(self._repeat_label(int(s["notify.warn.repeat_min"])))
        self.started_switch.select() if s["notify.started.enabled"] == "1" else self.started_switch.deselect()


class Popup(ctk.CTkToplevel):
    """Small message that slides up and fades in at the bottom-right; an accent edge, the Lockdown mark, a close ✕
    and (design 3l) "Open Lockdown" / "Mute 1 h" buttons. Click the text or wait (it stays while the mouse is over
    it) to dismiss. `on_open()` brings Lockdown up, `on_mute()` holds popups for an hour."""

    def __init__(self, root, message: str, on_open=None, on_mute=None):
        super().__init__(root)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass
        self.w, self.h = 400, 136 if (on_open or on_mute) else 96
        self.x = self.winfo_screenwidth() - self.w - 16
        self.target_y = self.winfo_screenheight() - self.h - 64
        self.arrived = False
        frame = ctk.CTkFrame(self, border_width=1, corner_radius=4)
        frame.pack(fill="both", expand=True)
        ctk.CTkFrame(frame, width=3, height=1, fg_color=theme.ACCENT, corner_radius=0).pack(side="left", fill="y")
        inner = ctk.CTkFrame(frame, fg_color="transparent")
        inner.pack(side="left", fill="both", expand=True, padx=12, pady=10)
        head = ctk.CTkFrame(inner, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="", image=theme.icon("shield-check", theme.ACCENT, 15), width=15).pack(side="left")
        ctk.CTkLabel(head, text=" Lockdown", font=theme.semi(12)).pack(side="left")
        ctk.CTkLabel(head, text="✕", text_color=MUTED, font=theme.body(11), cursor="hand2").pack(side="right")
        ctk.CTkLabel(head, text="now", text_color=MUTED, font=theme.body(10)).pack(side="right", padx=(0, 8))
        ctk.CTkLabel(inner, text=message, wraplength=350, justify="left", font=theme.body(12), anchor="w").pack(
            anchor="w", pady=(6, 0))
        self.hovered = False
        for widget in (self, frame, inner, head, *head.winfo_children(), *inner.winfo_children()):
            widget.bind("<Button-1>", lambda e: self.close())
        if on_open or on_mute:
            buttons = ctk.CTkFrame(inner, fg_color="transparent")
            buttons.pack(anchor="w", pady=(10, 0))
            if on_open:
                ctk.CTkButton(buttons, text="Open Lockdown", width=110, height=28, **theme.OUTLINE,
                              command=lambda: (self.close(), on_open())).pack(side="left", padx=(0, 8))
            if on_mute:
                ctk.CTkButton(buttons, text="Mute 1 h", width=80, height=28, **{**theme.OUTLINE, "text_color": MUTED},
                              command=lambda: (self.close(), on_mute())).pack(side="left")
        Corner.add(self, self.w, self.h)   # stacked above the ones already there, never on top of them
        self.bind("<Enter>", lambda e: setattr(self, "hovered", True))
        self.bind("<Leave>", lambda e: (setattr(self, "hovered", False), self.after(POPUP_MS, self._maybe_close)))
        self._animate(0)
        self.after(POPUP_MS, self._maybe_close)

    def corner_place(self, x: int, y: int):
        """Where the stack wants it. While it is still sliding in, the animation takes it from here."""
        self.x, self.target_y = x, y
        if self.arrived:
            self.geometry(f"{self.w}x{self.h}+{x}+{y}")

    def _animate(self, step: int):
        if not self.winfo_exists():
            return
        frac = min(1.0, step / 6)
        self.arrived = frac >= 1.0
        self.geometry(f"{self.w}x{self.h}+{self.x}+{int(self.target_y + 14 * (1 - frac))}")
        try:
            self.attributes("-alpha", frac)
        except Exception:
            pass
        if frac < 1.0:
            self.after(22, lambda: self._animate(step + 1))

    def _maybe_close(self):
        if not self.hovered:
            self.close()

    def close(self):
        if self.winfo_exists():
            self.destroy()
