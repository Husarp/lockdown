"""Notifications page (blocked-visit alert settings + recent visits) and the in-app popup."""
import customtkinter as ctk

import alerts

MUTED = "gray60"
POPUP_MS = 6000


class NotificationsPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.db, self.draft = app.db, app.draft
        ctk.CTkLabel(self, text="Notifications", font=ctk.CTkFont(size=24, weight="bold")).pack(
            anchor="w", padx=30, pady=(16, 12))
        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        self._build_settings(body)
        ctk.CTkLabel(body, text="Recent Blocked Visits", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=10, pady=(4, 6))
        self.visits_box = ctk.CTkFrame(body)
        self.visits_box.pack(fill="x")
        ctk.CTkLabel(body, text="Limit warnings, reports and reminders come in later phases.", text_color=MUTED).pack(
            anchor="w", padx=10, pady=12)
        self.load()
        self.refresh()

    def _build_settings(self, parent):
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=(0, 12))
        box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(box, text="Blocked Visit Alerts", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=2, padx=16, pady=(12, 2), sticky="w")
        ctk.CTkLabel(box, text="Notify me when I try to open a site (or start an app) that is:", text_color=MUTED).grid(
            row=1, column=0, columnspan=2, padx=16, pady=(0, 6), sticky="w")

        self.enabled_vars: dict[str, ctk.BooleanVar] = {}
        self.msg_entries: dict[str, ctk.CTkEntry] = {}
        row = 2
        for reason, (label, _) in alerts.REASONS.items():
            var = ctk.BooleanVar()
            self.enabled_vars[reason] = var
            ctk.CTkCheckBox(box, text=label, variable=var, width=230,
                            command=lambda r=reason, v=var: self.draft.set_setting(f"notify.enabled.{r}", "1" if v.get() else "0")
                            ).grid(row=row, column=0, padx=16, pady=4, sticky="w")
            msg = ctk.CTkEntry(box)
            msg.grid(row=row, column=1, padx=(0, 16), pady=4, sticky="ew")
            msg.bind("<KeyRelease>", lambda _e, r=reason, m=msg: self.draft.set_setting(
                f"notify.msg.{r}", m.get().strip() or alerts.DEFAULT_MESSAGES[r]))
            self.msg_entries[reason] = msg
            row += 1
        ctk.CTkLabel(box, text="Message placeholders: {site}  {reason}  {until}", text_color=MUTED).grid(
            row=row, column=1, padx=(0, 16), sticky="w")

        opts = ctk.CTkFrame(box, fg_color="transparent")
        opts.grid(row=row + 1, column=0, columnspan=2, padx=16, pady=(10, 4), sticky="w")
        ctk.CTkLabel(opts, text="Don't repeat for the same site within").pack(side="left")
        self.cooldown = ctk.CTkOptionMenu(opts, width=90, values=[f"{m} min" for m in alerts.COOLDOWN_OPTIONS],
                                          command=lambda v: self.draft.set_setting("notify.cooldown_min", v.split()[0]))
        self.cooldown.pack(side="left", padx=8)

        fmt_row = ctk.CTkFrame(box, fg_color="transparent")
        fmt_row.grid(row=row + 2, column=0, columnspan=2, padx=16, pady=4, sticky="w")
        ctk.CTkLabel(fmt_row, text="Show as").pack(side="left")
        self.fmt = ctk.CTkSegmentedButton(fmt_row, values=list(alerts.FORMATS.values()), command=lambda v: self.draft.set_setting(
            "notify.format", next(k for k, lbl in alerts.FORMATS.items() if lbl == v)))
        self.fmt.pack(side="left", padx=8)
        ctk.CTkLabel(box, text="Per-site override: the Alerts column in Blocking > All.", text_color=MUTED).grid(
            row=row + 3, column=0, columnspan=2, padx=16, pady=(4, 12), sticky="w")
        self._build_warnings(parent)

    def _build_warnings(self, parent):
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(box, text="Upcoming Blocks", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 6))
        warn = ctk.CTkFrame(box, fg_color="transparent")
        warn.pack(anchor="w", padx=16, pady=4)
        self.warn_switch = ctk.CTkSwitch(warn, text="Warn me", command=lambda: self.draft.set_setting(
            "notify.warn.enabled", "1" if self.warn_switch.get() else "0"))
        self.warn_switch.pack(side="left")
        self.warn_minutes = ctk.CTkOptionMenu(warn, width=90, values=[f"{m} min" for m in alerts.WARN_MINUTE_OPTIONS],
                                              command=lambda v: self.draft.set_setting("notify.warn.minutes", v.split()[0]))
        self.warn_minutes.pack(side="left", padx=8)
        ctk.CTkLabel(warn, text="before a site/app gets blocked (hours, daily limit, allowance)").pack(side="left")
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
        for reason, entry in self.msg_entries.items():
            entry.delete(0, "end")
            entry.insert(0, s[f"notify.msg.{reason}"])
        self.cooldown.set(f"{s['notify.cooldown_min']} min")
        self.fmt.set(alerts.FORMATS[s["notify.format"]])
        self.warn_switch.select() if s["notify.warn.enabled"] == "1" else self.warn_switch.deselect()
        self.warn_minutes.set(f"{s['notify.warn.minutes']} min")
        self.repeat.set(self._repeat_label(int(s["notify.warn.repeat_min"])))
        self.started_switch.select() if s["notify.started.enabled"] == "1" else self.started_switch.deselect()

    def refresh(self):
        for w in self.visits_box.winfo_children():
            w.destroy()
        events = self.db.block_events_after(self.db.last_block_event_id() - 20)[::-1]
        if not events:
            ctk.CTkLabel(self.visits_box, text="No blocked visits yet.", text_color=MUTED).pack(anchor="w", padx=16, pady=12)
            return
        for col, head in enumerate(["Time", "Site", "Hostname", "Reason"]):
            ctk.CTkLabel(self.visits_box, text=head, text_color=MUTED).grid(row=0, column=col, padx=12, pady=(8, 2), sticky="w")
        for r, e in enumerate(events, start=1):
            for col, text in enumerate([e["timestamp"], e["display_name"], e["hostname"],
                                        alerts.REASONS.get(e["reason"], (e["reason"],))[0]]):
                ctk.CTkLabel(self.visits_box, text=text).grid(row=r, column=col, padx=12, pady=2, sticky="w")


class Popup(ctk.CTkToplevel):
    """Small always-on-top message in the bottom-right corner; click or wait to dismiss."""

    def __init__(self, root, message: str):
        super().__init__(root)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        w, h = 360, 90
        x = self.winfo_screenwidth() - w - 16
        y = self.winfo_screenheight() - h - 64
        self.geometry(f"{w}x{h}+{x}+{y}")
        frame = ctk.CTkFrame(self, border_width=1)
        frame.pack(fill="both", expand=True)
        ctk.CTkLabel(frame, text="Lockdown", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=14, pady=(10, 0))
        ctk.CTkLabel(frame, text=message, wraplength=330, justify="left").pack(anchor="w", padx=14, pady=(2, 10))
        for widget in (self, frame, *frame.winfo_children()):
            widget.bind("<Button-1>", lambda e: self.close())
        self.after(POPUP_MS, self.close)

    def close(self):
        if self.winfo_exists():
            self.destroy()
