"""Anti-Bypass "3×3 grid": nine boxes; one lights up at random and you click it and type the word shown, then another
box lights up with the next word. Boxes are never focused for you (and Tab skips them), so a macro can't just type
blindly - it would have to find and click the right box every time. Pasting is blocked."""
import random

import customtkinter as ctk

from gui import theme

MUTED = theme.MUTED
NO_PASTE = ("<<Paste>>", "<Control-v>", "<Control-V>", "<Shift-Insert>", "<Button-3>", "<<PasteSelection>>")


def block_paste(entry: ctk.CTkEntry):
    for seq in NO_PASTE:
        entry._entry.bind(seq, lambda e: "break")


class WordGrid(ctk.CTkFrame):
    """on_done() when every word was typed; status(text, error) for progress messages."""

    def __init__(self, master, words: list[str], on_done, status, rng=random):
        super().__init__(master, fg_color="transparent")
        self.words, self.on_done, self.status, self.rng = words, on_done, status, rng
        self.index, self.active = 0, None
        self.done = False
        self._error = False
        self._phase = 0
        self.prompt = ctk.CTkLabel(self, text="", font=ctk.CTkFont("Consolas", 18), anchor="w")
        self.prompt.pack(anchor="w", pady=(0, 8))
        board = ctk.CTkFrame(self, fg_color="transparent")
        board.pack(anchor="w")
        self.boxes: list[ctk.CTkEntry] = []
        for i in range(9):
            box = ctk.CTkEntry(board, width=150, height=40, justify="center", font=ctk.CTkFont("Consolas", 15),
                               border_width=2)
            box.grid(row=i // 3, column=i % 3, padx=4, pady=4)
            box._entry.configure(takefocus=0)   # Tab never lands in a box: click the lit one
            block_paste(box)
            box._entry.bind("<KeyRelease>", lambda e, b=box: self._typed(b))
            self.boxes.append(box)
        self._next()
        self._breathe()

    def _breathe(self):
        """The lit box's border gently pulses between the accent and a softer accent (~2.4 s) so the eye finds it."""
        if not self.winfo_exists() or self.done:
            return
        self._phase = (self._phase + 1) % 16
        t = self._phase / 8 if self._phase <= 8 else (16 - self._phase) / 8
        if self.active is not None and self.active.winfo_exists() and not self._error:
            try:
                self.active.configure(border_color=theme._mix(theme.pick(theme.ACCENT), theme.pick(theme.SURFACE2),
                                                              t * 0.55))
            except Exception:
                pass
        self.after(150, self._breathe)

    def _next(self):
        if self.index == len(self.words):
            self.done = True
            self.prompt.configure(text="All words typed.")
            for box in self.boxes:
                box.configure(state="disabled", border_color=theme.BORDER)
            self.on_done()
            return
        choices = [b for b in self.boxes if b is not self.active]
        self.active = self.rng.choice(choices)
        for box in self.boxes:
            box.configure(state="normal")
            box.delete(0, "end")
            lit = box is self.active
            box.configure(state="normal" if lit else "disabled", border_color=theme.ACCENT if lit else theme.BORDER,
                          fg_color=theme.SURFACE2 if lit else theme.SURFACE)
        self._error = False
        self.master.focus_set()   # nothing focused: the lit box has to be clicked
        self.prompt.configure(text=f"Word {self.index + 1} of {len(self.words)}:   {self.words[self.index]}")
        self.status("Click the orange box and type the word.", False)

    def _typed(self, box):
        if box is not self.active:
            return
        text, word = box.get().strip(), self.words[self.index]
        if text == word:
            self.index += 1
            self._next()
        elif not word.startswith(text):
            self._error = True
            box.configure(border_color=theme.DANGER)
            self.status("Typo - fix it to go on.", True)
        else:
            self._error = False
            box.configure(border_color=theme.ACCENT)
            self.status(f"{self.index} of {len(self.words)} words done.", False)
