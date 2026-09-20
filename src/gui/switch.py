"""A switch drawn the way design plate 3m draws it: a 34x18 pill track with a 14px knob sitting INSIDE it (2px
inset) and a 1px edge on the knob, so the knob never melts into a white card and the track never reads thin.
customtkinter's CTkSwitch draws the knob at the full track height, which is what made ours look skinny.

Drop-in for the CTkSwitch calls in this app (theme.apply() installs it as ctk.CTkSwitch): text, command, font,
width, variable; get() / select() / deselect() / toggle(); configure(text= / command= / font= / text_color= /
state=)."""
import tkinter as tk

import customtkinter as ctk

from gui import paint, theme

TRACK_W, TRACK_H, KNOB, INSET = 34, 18, 14, 2
KNOB_EDGE = ("#8A939F", "#3A434F")        # the 1px knob edge (light / dark)
OFF_DISABLED = ("#E4E7EB", "#1C2128")
KNOB_DISABLED = ("#C9CFD7", "#5C6875")


class Switch(ctk.CTkFrame):
    def __init__(self, master, text: str = "", command=None, font=None, width: int | None = None, variable=None,
                 text_color=None, state: str = "normal", **_ignored):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self._command, self._variable, self._state = command, variable, state
        self._on = bool(variable.get()) if variable is not None else False
        self._hover = False
        s = self._scale = ctk.ScalingTracker.get_widget_scaling(self)
        self.canvas = tk.Canvas(self, width=int(TRACK_W * s), height=int(TRACK_H * s), highlightthickness=0, bd=0,
                                cursor="hand2")
        self.canvas.grid(row=0, column=0, sticky="w")
        self.label = ctk.CTkLabel(self, text=text, font=font or theme.body(13), anchor="w",
                                  text_color=text_color or theme.TEXT, cursor="hand2")
        if text:
            self.label.grid(row=0, column=1, sticky="w", padx=(8, 0))
        if width:   # a fixed total width, like CTkSwitch's width=, so rows of switches line up
            # (logical px - CTkFrame scales them; and an explicit height, else propagate-off keeps CTk's 200px)
            self.configure(width=width, height=26)
            self.grid_propagate(False)
            self.grid_columnconfigure(1, weight=1)
            self.grid_rowconfigure(0, weight=1)
        for w in (self.canvas, self.label):
            w.bind("<Button-1>", self._click)
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))
        if variable is not None:
            variable.trace_add("write", lambda *_: self._from_variable())
        ctk.AppearanceModeTracker.add(self._mode_changed, self)
        self.after(1, self._paint)   # (bg of the master is known once it's laid out)

    # ---------- CTkSwitch API ----------

    def get(self) -> int:
        return 1 if self._on else 0

    def select(self):
        self._set(True)

    def deselect(self):
        self._set(False)

    def toggle(self):
        self._set(not self._on)

    def configure(self, **kw):
        if "text" in kw:
            self.label.configure(text=kw.pop("text"))
            if not self.label.winfo_manager():
                self.label.grid(row=0, column=1, sticky="w", padx=(8, 0))
        if "command" in kw:
            self._command = kw.pop("command")
        if "font" in kw:
            self.label.configure(font=kw.pop("font"))
        if "text_color" in kw:
            self.label.configure(text_color=kw.pop("text_color"))
        if "state" in kw:
            self._state = kw.pop("state")
            self._paint()
        if kw:
            super().configure(**kw)

    def cget(self, key: str):
        if key == "text":
            return self.label.cget("text")
        return super().cget(key)

    # ---------- behaviour ----------

    def _set(self, on: bool):
        self._on = on
        if self._variable is not None and bool(self._variable.get()) != on:
            self._variable.set(on)
        self._paint()

    def _from_variable(self):
        if bool(self._variable.get()) != self._on:
            self._on = bool(self._variable.get())
            self._paint()

    def _click(self, _event=None):
        if self._state == "disabled":
            return
        self._set(not self._on)
        if self._command:
            self._command()

    def _set_hover(self, on: bool):
        self._hover = on
        self._paint()

    def _mode_changed(self, _mode=None):
        self._paint()

    # ---------- drawing ----------

    def _bg(self) -> str:
        try:
            return self._apply_appearance_mode(self._detect_color_of_master())
        except Exception:
            return theme.pick(theme.SURFACE)

    def _paint(self):
        if not self.winfo_exists():
            return
        s, c = self._scale, self.canvas
        w, h = TRACK_W * s, TRACK_H * s
        bg = self._bg()
        disabled = self._state == "disabled"
        if disabled:
            track = theme.pick(OFF_DISABLED)
        elif self._on:
            track = theme.pick(theme.ACCENT_PRESS if self._hover else theme.ACCENT)
        else:
            track = theme.pick(theme.TRACK)
        # drawn with Pillow, not with canvas ovals: those come out with hard, stair-stepped edges
        art = paint.Art(w, h, bg)
        art.rrect(0, 0, w, h, h / 2, fill=track)
        k = KNOB * s
        x0 = (w - INSET * s - k) if self._on else INSET * s
        y0 = (h - k) / 2
        knob = theme.pick(KNOB_DISABLED if disabled else theme.WHITE)
        edge = theme.pick(KNOB_DISABLED if disabled else KNOB_EDGE)
        art.ellipse(x0, y0, x0 + k, y0 + k, fill=knob, outline=edge, width=s)
        self._photo = art.photo()   # kept: Tk only keeps a pointer to the image
        c.delete("all")
        c.configure(bg=bg)
        c.create_image(0, 0, image=self._photo, anchor="nw")
