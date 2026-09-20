"""The right-click menu for an app or a site, wherever you can search one up: Browse apps and the Screen Time
lists. Set its category (without having to use it first, which used to be the only way), block it, put it in one
of your groups, or find its .exe on disk.

Everything here goes through the draft, so nothing is applied until Lockdown saves it like any other change."""
import subprocess
import tkinter as tk

from blocker.apps import PROTECTED, make_block_type
from gui import categories, theme


def open_menu(widget, app, kind: str, name: str, display: str | None = None, path: str | None = None,
              on_done=lambda: None):
    """Show the menu at the mouse. Note tk_popup does not return until the menu is dismissed."""
    build_menu(widget, app, kind, name, display, path, on_done).tk_popup(widget.winfo_pointerx(),
                                                                        widget.winfo_pointery())


def build_menu(widget, app, kind: str, name: str, display: str | None = None, path: str | None = None,
               on_done=lambda: None) -> tk.Menu:
    """kind: "app" (name = its .exe) or "site" (name = the hostname). `app` is the LockdownApp."""
    display = display or name
    menu = tk.Menu(widget, tearoff=False)
    current = _current_category(app, kind, name)
    sub, images = categories.build_menu(widget, app.db, kind, name, current, on_done, app.guard)
    widget._category_images = images   # Tk shows nothing if the swatches are garbage-collected
    menu.add_cascade(label="  Category", menu=sub)
    menu.add_separator()
    if name.lower() in PROTECTED:
        menu.add_command(label="  Part of Windows - can't be blocked", state="disabled")
    else:
        menu.add_command(label="  Block it...", command=lambda: _block(app, kind, name, display, path))
        groups = app.draft.sorted_groups()
        if groups:
            in_group = tk.Menu(menu, tearoff=False)
            for g in groups:
                in_group.add_command(label=f"  {g['name']}",
                                     command=lambda g=g: _add_to_group(app, g, kind, name, display, path, on_done))
            menu.add_cascade(label="  Add to group", menu=in_group)
        else:
            menu.add_command(label="  Add to group (you have none yet)", state="disabled")
    if kind == "app" and path:
        menu.add_separator()
        menu.add_command(label="  Show in Explorer", command=lambda: _reveal(path))
    return menu


def _current_category(app, kind: str, name: str) -> str:
    import stats
    items = list(app.draft.items.values())
    cat = stats.category_of(kind, name, app.db.categories(), items)
    return cat if cat in {c["key"] for c in categories.load(app.db)} else "neutral"


def _item_for(app, kind: str, name: str, display: str, path: str | None) -> dict:
    """The blocklist item for this app / site, put on the list first if it isn't already."""
    existing = app.draft.find_item(name)
    if existing:
        return existing
    block_type = make_block_type(["close"]) if kind == "app" else None
    return app.draft.add_item(display, [name], "manual", kind, block_type, path)


def _block(app, kind: str, name: str, display: str, path: str | None):
    """Open Blocking > Add with it filled in - you still choose the blockers and press Add."""
    app.show_page("Blocking")
    page = app.pages["Blocking"]
    if kind == "app":
        page.add_prefilled(app={"name": display, "exe": name, "path": path})
    else:
        page.add_prefilled(site=(display, name))


def _add_to_group(app, group: dict, kind: str, name: str, display: str, path: str | None, on_done):
    """Put it on the blocklist (if it isn't) and into the group, so the group's shared rules cover it."""
    item = _item_for(app, kind, name, display, path)
    members = dict(group["members"])
    if item["id"] not in members:
        members[item["id"]] = {}
        app.draft.set_group(group["id"], group["name"], group["rules"], members)
    if "Blocking" in app.pages:
        app.pages["Blocking"].confirm(f"{display} added to {group['name']}")
    on_done()


def _reveal(path: str):
    try:
        subprocess.Popen(["explorer", "/select,", path])   # explorer wants the comma glued to /select
    except OSError:
        pass
