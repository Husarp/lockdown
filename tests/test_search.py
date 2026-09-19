import search
from gui.app_browser import matches


def test_small_typos_are_fine():
    assert search.score("lethal", "Lethal Company") == 0
    assert search.score("lethl", "Lethal Company") == 1          # still typing, one letter wrong
    assert search.score("dicsord", "Discord") == 1               # swapped letters = one typo
    assert search.score("terarria", "Terraria") == 1
    assert search.score("yotube", "YouTube youtube.com") == 1
    assert search.score("ŁÓDŹ", "lodz") == 0                     # capitals / accents don't matter
    assert search.score("xyz", "Discord") is None
    assert search.score("lz", "Lethal") is None                  # short words must be exact
    assert search.score("", "anything") == 0


def test_rank_puts_exact_matches_first():
    items = ["Discrd Nitro", "Discord", "Disco Elysium"]
    assert search.rank(items, "discord", lambda s: s) == ["Discord", "Discrd Nitro", "Disco Elysium"]   # 0, 1, 2 typos


def test_steam_games_listed_under_steam():
    apps = [{"name": "PEAK", "exe": "peak.exe", "running": False, "steam": True},
            {"name": "Discord", "exe": "discord.exe", "running": True},
            {"name": "Steam", "exe": "steam.exe", "running": False},
            {"name": "Lethal Company", "exe": "lethal company.exe", "running": True, "steam": True}]
    assert [a["name"] for a in matches(apps, "steam")] == ["Steam", "Lethal Company", "PEAK"]
    assert [a["name"] for a in matches(apps, "lethl")] == ["Lethal Company"]
