"""App icons: taken big and scaled down (resident/icons.py), the old blurry cache made again once, and the icon places
the app catalog knows for apps never run (monitor/applist.py, monitor/steam.py, resident/catalog.py).

Nothing here needs Windows: icon files (.ico / .png / .jpg) are read by Pillow anywhere, and the registry, shortcuts and
Steam folders are fakes. What only a Windows machine proves: SHDefExtractIcon / DrawIconEx on a real exe, the real
shortcut IconLocation, the real uninstall list and Steam's real files."""
import io
import json

from PIL import Image

from monitor import applist, steam
from resident import catalog as cat
from resident import icons

PNG = b"\x89PNG\r\n\x1a\nfake-icon"


def png_size(data: bytes):
    image = Image.open(io.BytesIO(data))
    return image.size, image.mode


# ================================================================ taking the icon

def test_split_location_reads_windows_icon_places(monkeypatch):
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    assert icons.split_location('"C:\\App\\app.exe",0') == ("C:\\App\\app.exe", 0)
    assert icons.split_location("C:\\x.dll, -101") == ("C:\\x.dll", -101)
    assert icons.split_location("C:\\a,b\\game.exe") == ("C:\\a,b\\game.exe", 0)
    assert icons.split_location("$SystemRoot\\a.ico,2")[1] == 2
    assert icons.split_location("") == ("", 0)


def test_fit_scales_down_keeps_transparency_and_never_scales_up():
    big = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    big.paste((255, 0, 0, 255), (64, 64, 192, 192))
    out = icons.fit(big)
    assert out.size == (icons.ICON_SIZE, icons.ICON_SIZE) and out.mode == "RGBA"
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((32, 32)) == (255, 0, 0, 255)
    assert icons.fit(Image.new("RGB", (32, 32))).size == (32, 32)
    assert icons.fit(Image.new("RGBA", (128, 64))).size == (64, 64)          # (a wide picture: centred in a square)


def test_an_ico_file_gives_its_biggest_picture_scaled_down(tmp_path):
    ico = tmp_path / "app.ico"
    big = Image.new("RGBA", (256, 256), (255, 0, 0, 255))
    small = Image.new("RGBA", (32, 32), (0, 0, 255, 255))
    big.save(ico, sizes=[(32, 32), (256, 256)], append_images=[small])
    data = icons.extract_exe_icon(f'"{ico}",0')
    assert png_size(data) == ((icons.ICON_SIZE, icons.ICON_SIZE), "RGBA")
    assert Image.open(io.BytesIO(data)).convert("RGBA").getpixel((10, 10))[:3] == (255, 0, 0)   # the 256 px one


def test_jpg_and_png_files_are_read_and_a_missing_file_or_an_exe_off_windows_is_none(tmp_path):
    Image.new("RGB", (32, 32), (0, 200, 0)).save(tmp_path / "icon.jpg")
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(tmp_path / "logo.png")
    assert png_size(icons.extract_exe_icon(str(tmp_path / "icon.jpg"))) == ((32, 32), "RGBA")
    assert png_size(icons.extract_exe_icon(str(tmp_path / "logo.png"))) == ((64, 64), "RGBA")
    assert icons.extract_exe_icon(str(tmp_path / "none.ico")) is None
    assert icons.extract_exe_icon("C:\\Program Files\\App\\app.exe") is None       # (Windows only)
    assert icons.extract_exe_icon("") is None


# ================================================================ the service: places, cache version, retry

def service(tmp_path, paths=None, sources=None, works=None, **kw):
    tried = []

    def extract(place):
        tried.append(place)
        return PNG + place.encode() if works is None or place in works else None

    s = icons.IconService(tmp_path / "icons", path_of=({} if paths is None else paths).get,
                          sources_of=lambda e: (sources or {}).get(e, []),
                          extract=extract, old_dir=tmp_path / "none", start=False, **kw)
    return s, tried


def test_the_catalogs_icon_places_go_first_then_the_path(tmp_path):
    s, tried = service(tmp_path, paths={"a.exe": "C:\\A\\a.exe"},
                       sources={"a.exe": ["C:\\A\\a.ico,0", "C:\\A\\a.exe", "C:\\Steam\\x.jpg"]},
                       works={"C:\\A\\a.exe"})
    s.handle("app/a.exe.png", {})
    s.run_pending()
    assert tried == ["C:\\A\\a.ico,0", "C:\\A\\a.exe"]                   # (the path is not tried twice)
    assert s.handle("app/a.exe.png", {}).body == PNG + b"C:\\A\\a.exe"


def test_an_app_with_only_catalog_places_gets_its_icon(tmp_path):
    s, tried = service(tmp_path, sources={"game.exe": ["D:\\Steam\\steam\\games\\h.ico"]})
    s.handle("app/game.exe.png", {})
    s.run_pending()
    assert s.handle("app/game.exe.png", {}).content_type == "image/png"


def test_old_small_app_icons_are_made_again_once(tmp_path):
    folder = tmp_path / "icons"
    folder.mkdir()
    (folder / "app_a.exe.png").write_bytes(b"old 32px")
    (folder / "site_example.com.png").write_bytes(PNG)
    s, tried = service(tmp_path, paths={"a.exe": "C:\\a.exe"})
    assert s.handle("app/a.exe.png", {}).content_type == "image/svg+xml"   # (the old one is gone: a tile, and a job)
    assert (folder / "site_example.com.png").exists() and (folder / icons.APP_MARK).read_text() == icons.APP_CACHE_VERSION
    s.run_pending()
    assert s.handle("app/a.exe.png", {}).body == PNG + b"C:\\a.exe"
    again, _ = service(tmp_path, paths={"a.exe": "C:\\a.exe"})             # the next start keeps the new ones
    assert again.handle("app/a.exe.png", {}).body == PNG + b"C:\\a.exe"


def test_a_new_cache_is_marked_with_the_first_icon(tmp_path):
    s, _ = service(tmp_path, paths={"a.exe": "C:\\a.exe"})
    s.handle("app/a.exe.png", {})
    s.run_pending()
    assert (tmp_path / "icons" / icons.APP_MARK).read_text() == icons.APP_CACHE_VERSION
    again, _ = service(tmp_path)
    assert again.handle("app/a.exe.png", {}).content_type == "image/png"


def test_icons_that_failed_before_the_catalog_loaded_are_tried_again(tmp_path):
    paths, ready = {}, []
    s, _ = service(tmp_path, paths=paths, on_ready=ready.append, clock=lambda: 5.0)
    s.handle("app/new.exe.png", {})
    s.run_pending()
    assert ready == [] and s.queued == 0
    s.handle("app/new.exe.png", {})
    assert s.queued == 0                                                   # (failed a moment ago: not asked again)
    paths["new.exe"] = "C:\\New\\new.exe"                                  # the catalog has loaded
    s.retry_apps()
    s.run_pending()
    assert ready == ["app:new.exe"] and s.handle("app/new.exe.png", {}).content_type == "image/png"


# ================================================================ the catalog keeps the places

def test_the_catalog_keeps_icon_places_and_answers_them(tmp_path):
    apps = [{"name": "A", "exe": "a.exe", "path": "C:\\a.exe", "steam": False, "icons": ["C:\\a.ico,0", "C:\\a.exe"]},
            {"name": "B", "exe": "b.exe", "path": "C:\\b.exe", "steam": False}]
    c = cat.AppCatalog(tmp_path / "c.json", list_apps=lambda: apps)
    c.load_now()
    assert c.icon_sources("A.EXE") == ["C:\\a.ico,0", "C:\\a.exe"] and c.icon_sources("b.exe") == []
    saved = json.loads((tmp_path / "c.json").read_text())
    assert saved["v"] == cat.FORMAT and "icons" not in saved["apps"][1]
    again = cat.AppCatalog(tmp_path / "c.json", list_apps=lambda: [])
    again.load_now()
    assert again.icon_sources("a.exe") == ["C:\\a.ico,0", "C:\\a.exe"]   # (from the file, no scan within the TTL)
    assert "icons" not in again.search()["apps"][0]                         # (the page is never given them)


def test_a_format_1_file_is_shown_then_scanned_again_at_once(tmp_path):
    (tmp_path / "c.json").write_text(json.dumps({"v": 1, "saved": 9e12, "apps": [
        {"name": "Old", "exe": "old.exe", "path": "C:\\old.exe", "steam": False}]}))
    seen = []

    def scan():
        seen.append(c.search()["apps"][0]["name"])
        return [{"name": "New", "exe": "new.exe", "path": "C:\\new.exe", "steam": False, "icons": ["C:\\n.ico"]}]

    c = cat.AppCatalog(tmp_path / "c.json", list_apps=scan)
    c.load_now()
    assert seen == ["Old"] and c.icon_sources("new.exe") == ["C:\\n.ico"]


# ================================================================ where the places come from

def test_icon_places_put_the_program_in_its_place_without_repeats():
    assert applist.icon_places("C:\\A\\a.exe", ["C:\\A\\a.exe,0"], [None]) == []         # only the program itself
    assert applist.icon_places("C:\\A\\a.exe", ["C:\\A\\a.ico,0"], ["C:\\A\\b.exe"]) == \
        ["C:\\A\\a.ico,0", "C:\\A\\a.exe", "C:\\A\\b.exe"]
    assert applist.icon_places("C:\\G\\g.exe", ["D:\\x.ico", "C:\\G\\g.exe", "D:\\y.jpg"]) == \
        ["D:\\x.ico", "C:\\G\\g.exe", "D:\\y.jpg"]


def test_the_uninstall_entry_whose_folder_holds_the_program_gives_its_icon():
    folders = applist.uninstall_folders([
        ('"C:\\Program Files\\Tool\\tool.ico"', "C:\\Program Files\\Tool\\"),
        ("C:\\Program Files\\Tool\\bin\\main.exe,1", ""),                     # (no InstallLocation: its folder)
        ("C:\\Program Files\\Other\\unins000.exe", "C:\\Program Files\\Other"),   # an uninstaller's: not used
        ("C:\\Junk\\x.ico", "C:\\Program Files")])                             # too shallow: not used
    assert applist.uninstall_icon("C:\\Program Files\\Tool\\bin\\Main.exe", folders) == \
        "C:\\Program Files\\Tool\\bin\\main.exe,1"                                    # (the deepest folder)
    assert applist.uninstall_icon("C:\\Program Files\\Tool\\tool.exe", folders) == '"C:\\Program Files\\Tool\\tool.ico"'
    assert applist.uninstall_icon("C:\\Program Files\\Toolbox\\t.exe", folders) is None   # (not a folder inside)
    assert applist.uninstall_icon("C:\\Program Files\\Other\\other.exe", folders) is None
    assert applist.uninstall_icon("C:\\Program Files\\Lone\\lone.exe", folders) is None


def test_list_apps_brings_the_shortcut_uninstall_and_steam_icons(monkeypatch):
    monkeypatch.setattr(applist.win, "top_windows", lambda: [])
    monkeypatch.setattr(applist, "_resolve_shortcuts", lambda: [
        ("Tool", "C:\\Program Files\\Tool\\tool.exe", "C:\\Program Files\\Tool\\big.ico,0"),
        ("Plain", "C:\\Program Files\\Plain\\plain.exe", ""),
        ("Same", "C:\\Program Files\\Same\\same.exe", "C:\\Program Files\\Same\\same.exe,0")])
    monkeypatch.setattr(applist, "_uninstall_entries", lambda: [("C:\\Program Files\\Plain\\plain.ico", ""),
                                                                ("C:\\Steam\\steam\\games\\u.ico", "D:\\Steam\\common\\G")])
    monkeypatch.setattr(steam, "games", lambda folders=(): [
        {"name": "Game", "exe": "game.exe", "path": "D:\\Steam\\common\\G\\game.exe", "steam": True,
         "icons": ["C:\\Steam\\steam\\games\\h.ico", "D:\\Steam\\common\\G\\game.exe", "C:\\Steam\\appcache\\librarycache\\7_icon.jpg"]}])
    apps = {a["exe"]: a for a in applist.list_apps()}
    assert apps["tool.exe"]["icons"] == ["C:\\Program Files\\Tool\\big.ico,0", "C:\\Program Files\\Tool\\tool.exe"]
    assert apps["plain.exe"]["icons"] == ["C:\\Program Files\\Plain\\plain.exe", "C:\\Program Files\\Plain\\plain.ico"]
    assert apps["same.exe"]["icons"] == []
    assert [i.rsplit("\\", 1)[-1] for i in apps["game.exe"]["icons"]] == ["u.ico", "h.ico", "game.exe", "7_icon.jpg"]


def test_steams_shortcuts_and_library_cache_give_the_game_icons(tmp_path, monkeypatch):
    menu = tmp_path / "Start Menu" / "Steam"
    menu.mkdir(parents=True)
    ico = tmp_path / "Steam" / "steam" / "games" / "abc.ico"
    ico.parent.mkdir(parents=True)
    ico.write_bytes(b"ico")
    (menu / "Game.url").write_text(f"[InternetShortcut]\nURL=steam://rungameid/7\nIconIndex=0\nIconFile={ico}\n")
    (menu / "Gone.url").write_text("[InternetShortcut]\nURL=steam://rungameid/8\nIconFile=C:\\nowhere.ico\n")
    assert steam.shortcut_icons([tmp_path / "Start Menu", tmp_path / "missing"]) == {"7": str(ico)}
    root = tmp_path / "Steam"
    cache = root / "appcache" / "librarycache"
    (cache / "9").mkdir(parents=True)
    (cache / "7_icon.jpg").write_bytes(b"j")
    (cache / "9" / "header.jpg").write_bytes(b"j")
    (cache / "9" / ("a" * 40 + ".jpg")).write_bytes(b"j")
    assert steam.library_icon(root, "7") == str(cache / "7_icon.jpg")
    assert steam.library_icon(root, "9") == str(cache / "9" / ("a" * 40 + ".jpg"))
    assert steam.library_icon(root, "10") is None and steam.library_icon(root, "../x") is None
    game = root / "steamapps" / "common" / "Game"
    game.mkdir(parents=True)
    (game / "Game.exe").write_bytes(b"x")
    (root / "steamapps" / "appmanifest_7.acf").write_text('"AppState"\n{\n"appid" "7"\n"name" "Game"\n"installdir" "Game"\n}')
    monkeypatch.setattr(steam, "steam_path", lambda: root)
    [found] = steam.games([tmp_path / "Start Menu"])
    assert found["icons"] == [str(ico), str(game / "Game.exe"), str(cache / "7_icon.jpg")]
