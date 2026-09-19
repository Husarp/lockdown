from blocker import apps
from gui.app_browser import matches
from monitor import steam


def test_manifest_and_library_folders(tmp_path):
    text = '"AppState"\n{\n\t"appid"\t\t"105600"\n\t"name"\t\t"Terraria"\n\t"installdir"\t\t"Terraria"\n}'
    assert steam.parse_manifest(text)["name"] == "Terraria" and steam.parse_manifest(text)["installdir"] == "Terraria"
    other = tmp_path / "Games" / "SteamLibrary"
    (other / "steamapps").mkdir(parents=True)
    (tmp_path / "steamapps").mkdir()
    (tmp_path / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + str(tmp_path).replace("\\", "\\\\") + '"\n\t}\n'
        '\t"1"\n\t{\n\t\t"path"\t\t"' + str(other).replace("\\", "\\\\") + '"\n\t}\n}')
    assert steam.library_folders(tmp_path) == [tmp_path, other]


def test_main_exe_picks_the_game_not_helpers(tmp_path):
    game = tmp_path / "Lethal Company"
    (game / "MonoBleedingEdge").mkdir(parents=True)
    (game / "Lethal Company.exe").write_bytes(b"x" * 100)
    (game / "UnityCrashHandler64.exe").write_bytes(b"x" * 5000)             # bigger, but a crash reporter
    (game / "MonoBleedingEdge" / "tool.exe").write_bytes(b"x" * 9000)
    assert steam.main_exe(game, "Lethal Company").name == "Lethal Company.exe"
    other = tmp_path / "AoE"
    (other / "bin").mkdir(parents=True)
    (other / "RelicCardinal.exe").write_bytes(b"x" * 9000)                  # no name match: the biggest one
    (other / "small.exe").write_bytes(b"x" * 10)
    assert steam.main_exe(other, "Age of Empires IV").name == "RelicCardinal.exe"


def test_steam_game_folder_is_the_whole_game():
    path = r"C:\Program Files (x86)\Steam\steamapps\common\tModLoader\LaunchUtils\busybox64.exe"
    assert apps.helper_folder(path) == r"c:\program files (x86)\steam\steamapps\common\tmodloader"
    assert apps.helper_folder(r"D:\SteamLibrary\steamapps\common\PEAK\PEAK.exe") == r"d:\steamlibrary\steamapps\common\peak"


def test_app_search_words_and_running_first():
    found = [{"name": "Lethal Company", "exe": "lethal company.exe", "running": False},
             {"name": "Discord", "exe": "discord.exe", "running": True},
             {"name": "Company of Heroes", "exe": "reliccoh.exe", "running": True}]
    assert [a["name"] for a in matches(found, "company")] == ["Company of Heroes", "Lethal Company"]
    assert [a["name"] for a in matches(found, "lethal comp")] == ["Lethal Company"]
    assert [a["name"] for a in matches(found, "")][0] in ("Company of Heroes", "Discord")
