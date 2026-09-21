"""What does Tk actually report when you type? Run it, type the letters that come out wrong, close the window.

    .venv\\Scripts\\python.exe scripts\\keycheck.py

Every keypress is written to build\\keycheck.log: the keysym, the character Tk hands over, the modifier state,
which keyboard layout is active, and what the app would make of it. Nothing is changed - it only watches.
"""
import ctypes
import sys
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
LOG = ROOT / "build" / "keycheck.log"

import mojibake                                    # noqa: E402  (after sys.path)
from gui.shortcuts import character                # noqa: E402

LOG.parent.mkdir(parents=True, exist_ok=True)
log = LOG.open("w", encoding="utf-8", buffering=1)


def layout() -> str:
    hkl = ctypes.windll.user32.GetKeyboardLayout(0)
    return f"0x{hkl & 0xFFFF:04X} -> {mojibake.keyboard_codepage()}"


def note(text: str):
    log.write(text + "\n")
    box.insert("end", text + "\n")
    box.see("end")


root = tk.Tk()
root.title("Lockdown - what your keyboard sends")
root.geometry("900x520")
tk.Label(root, text="Type the letters that come out wrong (ą ć ę ł ń ó ś ź ż), then close this window.",
         font=("Segoe UI", 11)).pack(anchor="w", padx=12, pady=(12, 4))
entry = tk.Entry(root, font=("Segoe UI", 14), width=50)
entry.pack(anchor="w", padx=12, pady=6)
entry.focus_set()
box = tk.Text(root, font=("Consolas", 9), height=20)
box.pack(fill="both", expand=True, padx=12, pady=12)

note(f"system codepage cp{ctypes.windll.kernel32.GetACP()} · layout now {layout()}")
note(f"tk {root.tk.call('info', 'patchlevel')} · python {sys.version.split()[0]}")
note("-" * 96)
note(f"{'event':16}{'keysym':14}{'char':10}{'codepoint':12}{'state':10}{'layout':22}what the app would type")


def watch(kind):
    def handler(event):
        char = event.char
        note(f"{kind:16}{event.keysym:14}{char!r:10}"
             f"{('U+%04X' % ord(char)) if len(char) == 1 else '-':12}"
             f"0x{event.state:04X}    {layout():22}{character(event)!r}")
    return handler


for sequence, name in (("<Key>", "Key"), ("<Control-Key>", "Control-Key"), ("<Alt-Key>", "Alt-Key")):
    entry.bind(sequence, watch(name), add="+")
entry.bind("<KeyRelease>", lambda e: note(f"{'  box now':16}{entry.get()!r}"), add="+")

root.mainloop()
log.close()
print(f"Written to {LOG}")
