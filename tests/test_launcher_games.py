"""Launcher discovery and the real service/tracker path, with synthetic Windows records only."""
import json
from pathlib import Path

import pytest

from blocker import apps
from db import Database
from importer import distracting
from monitor import applist, launchers, steam, win
from resident.catalog import AppCatalog, _running_windows
from test_app_limit_enforcement import FRIDAY, World, at

REGISTRY_ENTRIES = launchers.registry_entries


@pytest.fixture
def records(tmp_path, monkeypatch):
    data, local = tmp_path / "ProgramData", tmp_path / "LocalAppData"
    monkeypatch.setenv("ProgramData", str(data))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(launchers, "registry_entries", lambda: [])
    monkeypatch.setattr(launchers, "_cache", (0.0, []))
    apps._app_folder.cache_clear()
    yield data, local
    apps._app_folder.cache_clear()


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def game(tmp_path, name="WorldOfTanks", launcher=True):
    folder = tmp_path / "Custom library" / name
    write(folder / "win64" / f"{name}.exe", "game")
    if launcher:
        write(folder / f"{name}Launcher.exe", "starter")
    return folder


def test_epic_manifests_and_secondary_library(records, tmp_path):
    data, _ = records
    folder = game(tmp_path, "Example")
    manifest = {"DisplayName": "Example Game", "InstallLocation": str(folder),
                "LaunchExecutable": "win64/Example.exe"}
    write(data / "Epic/EpicGamesLauncher/Data/Manifests/a.item", json.dumps(manifest))
    write(data / "Epic/EpicGamesLauncher/Data/Manifests/broken.item", "{")
    write(data / "Epic/UnrealEngineLauncher/LauncherInstalled.dat",
          json.dumps({"InstallationList": [{"AppName": "Example", "InstallLocation": str(folder)}]}))
    found = launchers.games()
    games = [g for g in found if g.get("game")]
    assert len(games) == 1
    assert games[0]["name"] == "Example Game" and games[0]["exe"] == "example.exe"
    assert games[0]["launcher"] == "Epic" and not games[0]["steam"]
    assert any(g["exe"] == "epicgameslauncher.exe" for g in found)


@pytest.mark.parametrize("source,values", [
    ("Wargaming", {"displayname": "World of Tanks", "game_path": "FOLDER"}),
    ("Battle.net", {"displayname": "World of Tanks", "installpath": "FOLDER"}),
    ("EA", {"displayname": "World of Tanks", "install dir": "FOLDER"}),
    ("Ubisoft", {"displayname": "World of Tanks", "installdir": "FOLDER"}),
    ("GOG", {"gamename": "World of Tanks", "path": "FOLDER", "exe": "win64/WorldOfTanks.exe"}),
])
def test_launcher_registry_records(records, tmp_path, monkeypatch, source, values):
    folder = game(tmp_path)
    values = {k: str(folder) if v == "FOLDER" else v for k, v in values.items()}
    monkeypatch.setattr(launchers, "registry_entries", lambda: [(source, "wot", values)])
    found = launchers.games()
    picked = next(g for g in found if g.get("game"))
    assert picked["exe"] == "worldoftanks.exe"     # real win64 process wins over the root starter
    assert picked["name"] == "World of Tanks" and picked["launcher"] == source
    assert next(g for g in found if g["exe"] == launchers.LAUNCHERS[source][1])["path"] == ""
    assert apps.app_folder({"app_path": picked["path"], "block_type": "close"}) == str(folder).lower().replace("/", "\\")


def test_wgc_xml_attributes_and_elements(records, tmp_path):
    data, local = records
    one, two = game(tmp_path), game(tmp_path, "Ships")
    write(data / "Wargaming.net/GameCenter/games.xml",
          f'<games><game name="World of Tanks" path="{one}"/></games>')
    write(local / "Wargaming.net/GameCenter/preferences.xml",
          f'<root><game><display_name>Ships</display_name><install_path>{two}</install_path></game></root>')
    assert {g["name"] for g in launchers.games() if g.get("game")} == {"World of Tanks", "Ships"}


def test_riot_yaml_and_client_install_map(records, tmp_path):
    data, _ = records
    one, two = game(tmp_path, "Valorant"), game(tmp_path, "League")
    write(data / "Riot Games/Metadata/valorant.live/valorant.live.product_settings.yaml",
          f'product_display_name: "VALORANT"\nproduct_install_full_path: "{one}"\n')
    write(data / "Riot Games/RiotClientInstalls.json", json.dumps({"associated_client": {str(two): "client.exe"}}))
    assert {g["exe"] for g in launchers.games() if g.get("game")} == {"valorant.exe", "league.exe"}


def test_ea_local_content_xml(records, tmp_path):
    data, _ = records
    folder = game(tmp_path, "Example")
    write(data / "Origin/LocalContent/Example/install.mfst",
          f'<root><game><name>Example</name><InstallLocation>{folder}</InstallLocation></game></root>')
    assert next(g for g in launchers.games() if g.get("game"))["launcher"] == "EA"


def test_registry_hives_and_views(records, tmp_path, monkeypatch):
    import winreg
    folder = game(tmp_path)
    entries = {
        (winreg.HKEY_CURRENT_USER, winreg.KEY_WOW64_32KEY, r"Software\Wargaming.net\Games\wot"):
            {"Game_Path": str(folder), "DisplayName": "World of Tanks"},
        (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY, launchers.UNINSTALL + r"\Example"):
            {"Publisher": "Blizzard Entertainment", "InstallLocation": str(folder), "DisplayName": "Example"},
    }

    class Key:
        def __init__(self, hive, view, path):
            self.hive, self.view, self.path = hive, view, path

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def open_key(hive, path, reserved=0, flags=0):
        if isinstance(hive, Key):
            assert flags & (winreg.KEY_WOW64_32KEY | winreg.KEY_WOW64_64KEY) == hive.view
            return Key(hive.hive, hive.view, hive.path + "\\" + path)
        return Key(hive, flags & (winreg.KEY_WOW64_32KEY | winreg.KEY_WOW64_64KEY), path)

    def subkeys(key):
        return sorted({p[len(key.path) + 1:].split("\\")[0] for h, v, p in entries
                       if h == key.hive and v == key.view and p.startswith(key.path + "\\")})

    def values(key):
        return list(entries.get((key.hive, key.view, key.path), {}).items())

    monkeypatch.setattr(launchers, "registry_entries", REGISTRY_ENTRIES)
    monkeypatch.setattr(winreg, "OpenKey", open_key)
    monkeypatch.setattr(winreg, "QueryInfoKey", lambda k: (len(subkeys(k)), len(values(k)), 0))
    monkeypatch.setattr(winreg, "EnumKey", lambda k, i: subkeys(k)[i])
    monkeypatch.setattr(winreg, "EnumValue", lambda k, i: (*values(k)[i], winreg.REG_SZ))
    found = launchers.registry_entries()
    assert {source for source, _, _ in found} == {"Wargaming", "Battle.net"}
    assert next(v for s, _, v in found if s == "Wargaming" and "game_path" in v)["game_path"] == str(folder)


@pytest.mark.parametrize("folder", ["C:\\", r"D:\Games", r"C:\Program Files", r"C:\Windows\System32",
                                         r"C:\Users\me", r"C:\Users\me\Downloads", "", "relative/game"])
def test_shared_or_unsafe_game_roots_are_rejected(folder):
    assert not launchers.safe_folder(folder)


def test_a_dedicated_top_level_game_install_is_valid():
    assert launchers.safe_folder(r"D:\WorldOfTanks")


def test_running_picker_keeps_elevated_unknown_path(records, tmp_path, monkeypatch):
    monkeypatch.setattr(win, "top_windows", lambda: [(10, "World of Tanks", ""), (11, "Windows", "")])
    monkeypatch.setattr(win, "content_pid", lambda hwnd: hwnd + 100)
    monkeypatch.setattr(apps, "list_processes", lambda: [(110, "worldoftanks.exe"), (111, "explorer.exe")])
    assert _running_windows()["worldoftanks.exe"] == ""
    catalog = AppCatalog(tmp_path / "catalog.json", list_apps=lambda: [])
    catalog.load_now()
    picked = catalog.search("running", "worldoftanks")["apps"]
    assert len(picked) == 1 and picked[0]["running"] and picked[0]["name"] == "Worldoftanks"
    assert picked[0]["path"] == "" and not catalog.search("running", "explorer")["apps"]


def test_launcher_games_survive_catalog_cache_and_seed(records, tmp_path, monkeypatch):
    folder = game(tmp_path)
    monkeypatch.setattr(launchers, "registry_entries", lambda: [("Wargaming", "wot", {"path": str(folder)})])
    monkeypatch.setattr(applist, "_resolve_shortcuts", lambda: [])
    monkeypatch.setattr(applist, "_uninstall_entries", lambda: [])
    monkeypatch.setattr(steam, "games", lambda folders: [])
    monkeypatch.setattr(win, "running_apps", lambda: {})
    catalog = AppCatalog(tmp_path / "cat.json", list_apps=applist.list_apps, running_now=lambda: {}, seed_games=True,
                         db_path=tmp_path / "db.sqlite")
    catalog.load_now()
    picked = catalog.search("games")["apps"]
    assert len(picked) == 1 and picked[0]["launcher"] == "Wargaming"
    with_db = Database(tmp_path / "db.sqlite")
    assert with_db.categories()[("app", "worldoftanks.exe")] == "games"
    assert ("app", "wgc.exe") not in with_db.categories()
    with_db.close()
    cached = AppCatalog(tmp_path / "cat.json", running_now=lambda: {})
    cached.load_now()
    assert cached.scans == 0 and cached.search("games")["apps"] == picked
    assert cached.search("all", "wgc")["total"] == 1


ROOT = r"D:\Custom library\Tanks"


@pytest.fixture
def pc(records, tmp_path, monkeypatch):
    monkeypatch.setattr(launchers, "installations", lambda refresh=False: [{"folder": ROOT}])
    return World(tmp_path, monkeypatch, at(FRIDAY, "10:00"))


def test_game_started_by_launcher_is_counted_and_closed_even_without_a_tray_name(pc):
    w = pc
    gid = w.gui.add_item("Tanks", ["worldoftankslauncher.exe"], "app", rules=[
        {"rule_type": "time_limit", "daily_limit_min": 1}], block_type="close",
        app_path=ROOT + r"\WorldOfTanksLauncher.exe")
    w.step()
    client = w.launch("wgc.exe", r"C:\Program Files\Wargaming.net\GameCenter\wgc.exe")
    game_pid = w.launch("worldoftanks.exe", ROOT + r"\win64\WorldOfTanks.exe", parent=client)
    sibling = w.launch("other.exe", r"D:\Custom library\OtherGame\other.exe", parent=client)
    w.play(game_pid)
    w.step(0.25)   # the service sees and publishes the newly running game
    def advance(ticks):
        for _ in range(ticks):
            w.ts += 10
            w.tracker.tick(w.tray_db, lambda: (None, None, 0.0, None, game_pid),
                           running_exes=lambda: {n for _, _, n in w.procs})
            w.enforcer.enforce_once()
            w.enforcer.enforce_apps()
    advance(4)   # service identifies it; tray's own query/snapshot cannot
    assert w.alive(game_pid) and w.used(gid) > 20
    advance(5)
    assert not w.alive(game_pid) and w.reason(gid) == "limit"
    assert w.alive(client) and w.alive(sibling)


def test_service_retries_initially_unreadable_path(pc):
    w = pc
    gid = w.gui.add_item("Tanks", ["worldoftankslauncher.exe"], "app", rules=[{"rule_type": "permanent"}],
                        app_path=ROOT + r"\WorldOfTanksLauncher.exe")
    w.step()
    pid = w.launch("engine.exe", None)
    w.step()
    assert w.alive(pid)
    w.paths[pid] = ROOT + r"\win64\engine.exe"
    w.run(0.5)
    assert not w.alive(pid) and w.reason(gid) == "permanent"


@pytest.mark.parametrize("secondary", [False, True])
def test_service_identified_main_process_counts_on_either_monitor(pc, secondary):
    w = pc
    gid = w.gui.add_item("Tanks", ["worldoftanks.exe"], "app", rules=[
        {"rule_type": "time_limit", "daily_limit_min": 1}], app_path=ROOT + r"\win64\WorldOfTanks.exe")
    pid = w.launch("worldoftanks.exe", ROOT + r"\win64\WorldOfTanks.exe")
    w.step()
    w.step()   # publish the service's identification; the tray has no name or path
    w.ts += 5
    others = [(None, None, None, pid)] if secondary else []
    w.tracker.tick(w.tray_db, lambda: (None, None, 0.0, None, None if secondary else pid, others),
                   running_exes=lambda: {"worldoftanks.exe"})
    w.tracker.flush(w.tray_db)
    assert w.used(gid) >= 5


def test_defaults_extend_existing_install_without_resetting_choices(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.set_setting(distracting.VERSION_KEY, str(distracting.VERSION))
    db.set_category("app", "cs2.exe", "distracting")   # the owner moved it after the previous migration
    db.set_category("app", "worldoftankslauncher.exe", "productive")
    distracting.seed(db)
    assert db.categories()[("app", "worldoftanks.exe")] == "games"
    assert db.categories()[("app", "worldoftankslauncher.exe")] == "productive"
    assert db.categories()[("app", "cs2.exe")] == "distracting"
    assert db.categories()[("app", "wgc.exe")] == "distracting"
    assert distracting.seed(db) == 0
    db.close()


@pytest.mark.parametrize("marker", ["wgc_metadata/info.xml", ".egstore/install", ".build.info", "uplay_install.state",
                                   "__Installer/installerdata.xml", "goggame-123.info"])
def test_install_markers_work_without_the_owners_registry(records, tmp_path, marker):
    folder = game(tmp_path)
    write(folder / marker, "")
    item = {"app_path": str(folder / "win64/WorldOfTanks.exe"), "block_type": "close"}
    assert apps.app_folder(item) == str(folder).lower().replace("/", "\\")
    assert launchers.game_root(str(folder.parent / "Other/other.exe")) is None


def test_install_record_appearing_later_is_not_cached_as_missing(records, tmp_path, monkeypatch):
    folder = game(tmp_path)
    item = {"app_path": str(folder / "win64/WorldOfTanks.exe"), "block_type": "close"}
    assert apps.app_folder(item) is None
    monkeypatch.setattr(launchers, "registry_entries", lambda: [("Wargaming", "wot", {"path": str(folder)})])
    launchers.installations(refresh=True)
    assert apps.app_folder(item) == str(folder).lower().replace("/", "\\")
