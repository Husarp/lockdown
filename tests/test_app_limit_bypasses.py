"""Ways a game could still run outside its hours or past its limit after the first 0.84.1 fix - found in review,
each reproduced first (the test failed) and then fixed. Same harness as test_app_limit_enforcement: the real service
pass and the real tray tracker on one database file, only Windows faked."""
import logging

import pytest

import service
from blocker import apps
from test_app_limit_enforcement import BOOT, FRIDAY, SHIPPING, STEAM, World, add_game, allow, at

EPIC = r"C:\Program Files\Epic Games\HollowGame"
EPIC_BOOT = EPIC + r"\HollowGame.exe"
EPIC_SHIP = EPIC + r"\HollowGame\Binaries\Win64\HollowGame-Win64-Shipping.exe"
STANDALONE = r"D:\Games\Hollow Game"
STANDALONE_BOOT = STANDALONE + r"\HollowGame.exe"
STANDALONE_ENGINE = STANDALONE + r"\bin\engine.exe"     # a child that is not named like the game


@pytest.fixture
def world(tmp_path, monkeypatch):
    return lambda start: World(tmp_path, monkeypatch, start)


def app_path(w: World, item_id: int) -> str | None:
    return next(i for i in w.gui.list_items() if i["id"] == item_id)["app_path"]


def limit(minutes: int) -> dict:
    return {"rule_type": "time_limit", "daily_limit_min": minutes}


def launch_through_starter(w: World, boot_path: str, child: str, child_path: str) -> int:
    """The starter runs, starts the game and exits a moment later (after the service has looked once)."""
    boot = w.launch("hollowgame.exe", boot_path)
    w.step(0.25)
    game = w.launch(child, child_path, parent=boot)
    w.step(0.25)
    w.procs = [p for p in w.procs if p[0] != boot]
    return game


# ---------------------------------------------------------------- a game outside Steam (review: major 1)

def test_epic_game_whose_starter_exited_is_closed_when_the_hours_end(world):
    w = world(at(FRIDAY, "17:50"))
    add_game(w, [allow("16:00", "18:00")], path=EPIC_BOOT)
    w.step()
    game = launch_through_starter(w, EPIC_BOOT, "hollowgame-win64-shipping.exe", EPIC_SHIP)
    w.play(game)
    w.run(9.5)                                  # 17:59:30 - still allowed
    assert w.alive(game)
    w.run(1)                                    # past 18:00, plus the 10 s grace
    assert w.reason(1) == "schedule" and not w.alive(game)


def test_epic_shipping_window_counts_toward_the_daily_limit(world):
    w = world(at(FRIDAY, "10:00"))
    add_game(w, [limit(30)], path=EPIC_BOOT)
    w.step()
    game = launch_through_starter(w, EPIC_BOOT, "hollowgame-win64-shipping.exe", EPIC_SHIP)
    w.play(game)
    w.run(29)
    assert w.alive(game) and 28 * 60 <= w.used(1) <= 30 * 60
    w.run(2)
    assert not w.alive(game)


def test_epic_game_started_while_blocked_starter_gone_before_the_first_look(world):
    """The starter exits before the 0.25 s check ever sees it: the Shipping exe is still the game (by name), and
    anything else from the game's Epic folder is too."""
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "18:00")], path=EPIC_BOOT)
    w.step()
    game = w.launch("hollowgame-win64-shipping.exe", EPIC_SHIP, parent=999)
    other = w.launch("crashpad.exe", EPIC + r"\Engine\crashpad.exe", parent=999)
    w.step(0.25)
    assert not w.alive(game) and not w.alive(other)


def test_standalone_game_child_with_another_name_is_followed_from_inside_the_allowed_hours(world):
    """Not in any game library and not named like the game: what the starter started from its own folder is
    remembered for every listed app, blocked or not - closed when the hours end, and its window counted."""
    w = world(at(FRIDAY, "17:40"))
    add_game(w, [allow("16:00", "18:00", allowance_min=15)], path=STANDALONE_BOOT)
    w.step()
    game = launch_through_starter(w, STANDALONE_BOOT, "engine.exe", STANDALONE_ENGINE)
    unrelated = w.launch("notepad.exe", r"D:\Games\Hollow Game\tools\notepad.exe")   # same folder, not started by it
    w.play(game)
    w.run(20)                                   # 18:00: 15 minutes allowed during blocked hours start running out
    assert w.alive(game)
    w.run(16)
    assert w.reason(1) == "schedule" and not w.alive(game)
    assert w.alive(unrelated)


def test_renamed_exe_inside_an_epic_game_folder_is_still_the_game(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "18:00")], path=EPIC_BOOT)
    w.step()
    renamed = w.launch("notgame.exe", EPIC + r"\notgame.exe")
    w.step(0.25)
    assert not w.alive(renamed)


def test_the_epic_launcher_and_other_games_are_left_alone(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "18:00")], path=EPIC_BOOT)
    w.step()
    launcher = w.launch("epicgameslauncher.exe",
                        r"C:\Program Files (x86)\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe")
    other_game = w.launch("othergame.exe", r"C:\Program Files\Epic Games\OtherGame\OtherGame.exe")
    w.run(1)
    assert w.alive(launcher) and w.alive(other_game)


@pytest.mark.parametrize("path, folder", [
    (r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64\x.exe", r"c:\program files\epic games\fortnite"),
    (r"C:\XboxGames\Hollow\Content\Hollow.exe", r"c:\xboxgames\hollow"),
    (r"D:\GOG Games\Hollow\Hollow.exe", r"d:\gog games\hollow"),
    (r"C:\Program Files (x86)\GOG Galaxy\Games\Hollow\bin\Hollow.exe", r"c:\program files (x86)\gog galaxy\games\hollow"),
    (r"C:\Riot Games\VALORANT\live\VALORANT.exe", r"c:\riot games\valorant"),
    (BOOT, STEAM.lower()),
    (r"C:\Program Files (x86)\GOG Galaxy\GalaxyClient.exe", None),   # the store's own client: not a game folder
    (r"C:\XboxGames\Hollow.exe", None),
    (r"D:\Games\Hollow\Hollow.exe", None),
])
def test_game_folder(path, folder):
    assert apps.game_folder(path) == folder


def test_unreal_shipping_names():
    assert apps.names_of(r"C:\x\HollowGame.exe") == {"hollowgame.exe", "hollowgame-win64-shipping.exe",
                                                     "hollowgame-wingdk-shipping.exe", "hollowgame-win32-shipping.exe"}
    assert apps.names_of("hollowgame-win64-shipping.exe") == {"hollowgame-win64-shipping.exe"}


# ---------------------------------------------------------------- Steam game added by name only (review: major 2)

def test_steam_game_added_by_name_only_started_as_its_shipping_exe_is_closed(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "18:00")], path=None)
    w.step()
    game = w.launch("hollowgame-win64-shipping.exe", SHIPPING)
    w.play(game)
    w.step(0.25)
    assert not w.alive(game)


def test_name_only_item_learns_its_steam_folder_from_a_game_process_with_another_name(world):
    """Steam starts ProjectX-Win64-Shipping.exe; the game's folder holds the HollowGame.exe on the list, so that is
    where the game lives - and from then on everything from the folder is the game."""
    w = world(at(FRIDAY, "22:00"))
    gid = add_game(w, [allow("16:00", "18:00")], path=None)
    w.files.add(BOOT)
    w.step()
    game = w.launch("projectx-win64-shipping.exe", STEAM + r"\ProjectX\Binaries\Win64\ProjectX-Win64-Shipping.exe")
    w.enforcer.paths_learned_at = float("-inf")
    w.step(0.25)
    assert app_path(w, gid) == BOOT
    w.step(0.25)
    assert not w.alive(game)


def test_learning_skips_a_renamed_copy_on_the_desktop_and_prefers_the_game_folder(world):
    w = world(at(FRIDAY, "10:00"))
    gid = add_game(w, [limit(60)], path=None)
    w.step()
    w.launch("hollowgame.exe", r"C:\Users\adam\Desktop\HollowGame.exe")
    w.enforcer.paths_learned_at = float("-inf")
    w.step()
    assert not app_path(w, gid)
    w.launch("hollowgame-win64-shipping.exe", SHIPPING)
    w.files.add(BOOT)
    w.enforcer.paths_learned_at = float("-inf")
    w.step()
    assert app_path(w, gid) == BOOT


def test_the_shipping_exe_started_directly_counts_as_a_launch(world):
    w = world(at(FRIDAY, "10:00"))
    add_game(w, [{"rule_type": "switch_limit", "daily_switch_limit": 1, "switch_mode": "visit"}])
    w.step()
    first = w.launch("hollowgame-win64-shipping.exe", SHIPPING)
    w.run(1)
    assert w.alive(first) and w.reason(1) is None
    w.procs = [p for p in w.procs if p[0] != first]
    w.run(1)
    second = w.launch("hollowgame-win64-shipping.exe", SHIPPING)
    w.step()
    assert w.reason(1) == "switches" and not w.alive(second)


# ---------------------------------------------------------------- the service's counting (review: major 3)

def _games_category(w: World, minutes: int) -> int:
    w.gui.set_category("app", "hollowgame.exe", "games")
    return w.gui.add_item("Games", ["games"], "category", rules=[limit(minutes)], block_type="close")


def test_a_category_limit_fills_while_the_tray_app_is_closed(world):
    w = world(at(FRIDAY, "10:00"))
    cid = _games_category(w, 30)
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.play(game)
    w.run(5)
    w.tray_running = False
    w.run(24)
    assert w.alive(game) and 28 * 60 <= w.used(cid) <= 30 * 60
    w.run(2)
    assert not w.alive(game)


def test_launches_are_counted_while_the_tray_app_is_closed(world):
    w = world(at(FRIDAY, "10:00"))
    add_game(w, [{"rule_type": "switch_limit", "daily_switch_limit": 2, "switch_mode": "visit"}])
    w.tray_running = False
    w.run(1)
    for _ in range(2):
        game = w.launch("hollowgame.exe", BOOT)
        w.run(0.5)
        assert w.alive(game)
        w.procs = [p for p in w.procs if p[0] != game]
        w.run(0.5)
    third = w.launch("hollowgame.exe", BOOT)
    w.step()
    assert w.reason(1) == "switches" and not w.alive(third)


# ---------------------------------------------------------------- the tray can't read the game's path (minor 1)

def test_a_protected_game_window_without_a_path_is_counted(world):
    """Anti-cheat refuses the tray's query: it gets the window's name and process number but no path. The service
    (SYSTEM) can read it and says which listed app that process belongs to."""
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "18:00", allowance_min=15)])     # Steam, the starter's path
    w.step()
    game = w.launch("engine.exe", STEAM + r"\Engine\engine.exe")   # not named like the game
    w.play(game)
    w.front = ("engine.exe", None)
    w.refuse.add(game)                                            # (and the service can't close it yet)
    w.run(14)
    assert w.reason(1) is None
    w.run(2)
    assert w.reason(1) == "schedule"


# ---------------------------------------------------------------- two blocks on one exe (minor 3)

def test_a_minimize_item_for_the_same_exe_does_not_undo_close(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "18:00")])
    w.gui.add_item("Zz hollow minimize", ["hollowgame.exe"], "app", rules=[{"rule_type": "permanent"}],
                   block_type="minimize")
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.step(0.25)
    assert not w.alive(game)


def test_an_old_full_path_item_plus_a_minimize_category_still_closes(world):
    w = world(at(FRIDAY, "22:00"))
    add_game(w, [allow("16:00", "18:00")], target=BOOT)
    w.gui.set_category("app", "hollowgame.exe", "games")
    w.gui.add_item("Games", ["games"], "category", rules=[{"rule_type": "permanent"}], block_type="minimize")
    w.step()
    game = w.launch("hollowgame.exe", BOOT)
    w.step(0.25)
    assert not w.alive(game)


def test_merge_blocks_keeps_every_flag_close_winning():
    close = {"item": {"block_type": "close", "app_path": None}, "reason": "schedule"}
    mini = {"item": {"block_type": "minimize,internet", "app_path": r"C:\x\a.exe"}, "reason": "permanent"}
    merged = apps.merge_blocks(mini, close)
    assert merged["reason"] == "schedule"
    assert apps.block_flags(merged["item"]["block_type"]) == {"close", "internet"}
    assert merged["item"]["app_path"] == r"C:\x\a.exe"


# ---------------------------------------------------------------- a reused process number (minor 4)

def test_a_reused_process_number_is_not_the_game(world):
    w = world(at(FRIDAY, "17:50"))
    add_game(w, [allow("16:00", "18:00")], path=STANDALONE_BOOT)
    w.step()
    game = launch_through_starter(w, STANDALONE_BOOT, "engine.exe", STANDALONE_ENGINE)
    w.run(1)
    # the game exits; Windows hands its number to an unrelated program with the same name, started later
    w.procs = [p for p in w.procs if p[0] != game]
    w.step(0.25)
    w.procs.append((game, 4, "engine.exe"))
    w.paths[game] = r"D:\Tools\engine.exe"
    w.started[game] = w.ts
    w.run(15)                                   # past 18:00
    assert w.reason(1) == "schedule"
    assert w.alive(game)


def test_a_refused_close_of_a_followed_child_is_logged(world, caplog):
    w = world(at(FRIDAY, "17:59"))
    add_game(w, [allow("16:00", "18:00")], path=EPIC_BOOT)
    w.step()
    game = launch_through_starter(w, EPIC_BOOT, "hollowgame-win64-shipping.exe", EPIC_SHIP)
    w.refuse.add(game)
    with caplog.at_level(logging.WARNING, logger=service.log.name):
        w.run(1.5)
    assert w.alive(game)
    assert sum("Couldn't close blocked app" in r.message for r in caplog.records) == 1


def test_a_game_steam_started_is_not_part_of_a_steam_item(world):
    """Following what an app starts must not make every game Steam ever started "Steam": a game from a library
    inside Steam's own folder is its own app (closed and counted as itself)."""
    w = world(at(FRIDAY, "17:50"))
    steam_dir = r"C:\Program Files (x86)\Steam"
    w.gui.add_item("Steam", ["steam.exe"], "app", rules=[allow("16:00", "18:00")], block_type="close",
                   app_path=steam_dir + r"\steam.exe")
    w.step()
    client = w.launch("steam.exe", steam_dir + r"\steam.exe")
    helper = w.launch("steamwebhelper.exe", steam_dir + r"\bin\cef\steamwebhelper.exe", parent=client)
    game = w.launch("other.exe", steam_dir + r"\steamapps\common\Other\other.exe", parent=client)
    w.run(11)                                   # past 18:00 and the grace
    assert not w.alive(client) and not w.alive(helper)
    assert w.alive(game)


def test_the_published_member_list_is_machine_state_not_part_of_a_backup(world):
    import json

    import backup
    from monitor.usage import MEMBERS_KEY
    assert MEMBERS_KEY in backup.RUNTIME_KEYS
    w = world(at(FRIDAY, "10:00"))
    add_game(w, [limit(60)], path=EPIC_BOOT)
    w.step()
    game = launch_through_starter(w, EPIC_BOOT, "engine.exe", EPIC + r"\Engine\engine.exe")
    w.step()
    assert json.loads(w.gui.get_setting(MEMBERS_KEY)) == {str(game): ["hollowgame.exe"]}
