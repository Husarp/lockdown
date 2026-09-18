"""Friendly names and icons for the apps/sites in the activity log."""
from gui import app_browser, icons


def app_name_path(exe: str, items: list[dict]) -> tuple[str, str | None]:
    """(name, exe path) - from the blocklist, the Start Menu app list (once loaded), or the exe name."""
    if exe == "pythonw.exe":   # Lockdown's own window
        return "Lockdown", None
    for item in items:
        if item["item_type"] == "app" and item["target"].lower() == exe:
            return item["display_name"], item.get("app_path")
    for app in app_browser._cache or []:
        if app["exe"] == exe:
            return app["name"], app["path"]
    stem = exe[:-4] if exe.endswith(".exe") else exe
    return stem[:1].upper() + stem[1:], None


def name_of(kind: str, name: str, items: list[dict]) -> str:
    return app_name_path(name, items)[0] if kind == "app" else name


def icon_of(kind: str, name: str, items: list[dict], size: int = 18):
    if kind == "app":
        return icons.get_app(name, app_name_path(name, items)[1], size)
    return icons.get(name, size)
