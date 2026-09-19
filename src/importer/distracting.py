"""Sites and apps that are Distracting by default: only ones that are purely for fun (games, game stores / launchers,
streaming video, short-video apps) - not ones many people also use for work or school (YouTube, Reddit, X, Discord,
Spotify, Pinterest...). Your Steam games count too. They're written into your categories once (a new version of
this list adds only what's new); anything you already categorised is left alone, and you can change them any time
on Screen Time - Modes that block "Distracting" follow them."""

VERSION = 1
VERSION_KEY = "categories.defaults"   # the list version already imported

SITES = [
    "tiktok.com", "snapchat.com", "9gag.com", "twitch.tv", "kick.com", "netflix.com", "disneyplus.com", "hulu.com",
    "max.com", "hbomax.com", "primevideo.com", "crunchyroll.com", "roblox.com", "steamcommunity.com",
    "store.steampowered.com", "epicgames.com", "ign.com", "poki.com", "crazygames.com", "miniclip.com", "y8.com",
    "friv.com",
]
APPS = [
    "steam.exe", "epicgameslauncher.exe", "battle.net.exe", "riotclientservices.exe", "leagueclient.exe",
    "league of legends.exe", "valorant.exe", "valorant-win64-shipping.exe", "fortniteclient-win64-shipping.exe",
    "robloxplayerbeta.exe", "minecraftlauncher.exe", "minecraft.exe", "osu!.exe", "eadesktop.exe", "origin.exe",
    "upc.exe", "ubisoftconnect.exe", "galaxyclient.exe", "cs2.exe", "dota2.exe", "gta5.exe", "rocketleague.exe",
    "overwatch.exe", "genshinimpact.exe", "r5apex.exe", "rainbowsix.exe",
]


def seed(db) -> int:
    """Import the list (once per list version). Returns how many categories were added."""
    if int(db.get_setting(VERSION_KEY, "0") or 0) >= VERSION:
        return 0
    saved = db.categories()
    added = 0
    for kind, names in (("site", SITES), ("app", APPS)):
        for name in names:
            if (kind, name) not in saved:
                db.set_category(kind, name, "distracting")
                added += 1
    db.set_setting(VERSION_KEY, str(VERSION))
    return added


def seed_games(db, exes) -> int:
    """Your Steam games (exe names) are Distracting unless you chose otherwise - each game only once, so changing
    one sticks."""
    import json
    done = set(json.loads(db.get_setting("categories.steam_seen", "[]")))
    saved = db.categories()
    added = 0
    for exe in exes:
        if exe in done:
            continue
        done.add(exe)
        if ("app", exe) not in saved:
            db.set_category("app", exe, "distracting")
            added += 1
    db.set_setting("categories.steam_seen", json.dumps(sorted(done)))
    return added
