"""Sites and apps that are Distracting by default: only ones that are purely for fun (games, game stores / launchers,
streaming video, short-video apps) - not ones many people also use for work or school (YouTube, Reddit, X, Discord,
Spotify, Pinterest...). Your Steam games go to Games instead (they used to be Distracting, before that category
existed - seed_games puts the ones you never touched right). They're written into your categories once (a new version of
this list adds only what's new); anything you already categorised is left alone, and you can change them any time
on Screen Time - Modes that block "Distracting" follow them."""

VERSION = 2
VERSION_KEY = "categories.defaults"   # the list version already imported

# Games go to Games (the same place your Steam library goes); stores, launchers and streaming stay Distracting,
# because they are not the game itself.
GAME_SITES = ["roblox.com", "poki.com", "crazygames.com", "miniclip.com", "y8.com", "friv.com"]
SITES = [
    "tiktok.com", "snapchat.com", "9gag.com", "twitch.tv", "kick.com", "netflix.com", "disneyplus.com", "hulu.com",
    "max.com", "hbomax.com", "primevideo.com", "crunchyroll.com", "steamcommunity.com",
    "store.steampowered.com", "epicgames.com", "ign.com",
]
GAME_APPS = [
    "league of legends.exe", "valorant.exe", "valorant-win64-shipping.exe", "fortniteclient-win64-shipping.exe",
    "robloxplayerbeta.exe", "minecraft.exe", "osu!.exe", "cs2.exe", "dota2.exe", "gta5.exe", "rocketleague.exe",
    "overwatch.exe", "genshinimpact.exe", "r5apex.exe", "rainbowsix.exe",
]
APPS = [
    "steam.exe", "epicgameslauncher.exe", "battle.net.exe", "riotclientservices.exe", "leagueclient.exe",
    "minecraftlauncher.exe", "eadesktop.exe", "origin.exe", "upc.exe", "ubisoftconnect.exe", "galaxyclient.exe",
]


def seed(db) -> int:
    """Import the list (once per list version). Returns how many categories were added or moved.
    Version 2 added the Games category: a game the list had put in Distracting and you never changed moves
    over, one you did change stays where you put it."""
    was = int(db.get_setting(VERSION_KEY, "0") or 0)
    if was >= VERSION:
        return 0
    saved = db.categories()
    added = 0
    for kind, names, category in (("site", SITES, "distracting"), ("app", APPS, "distracting"),
                                  ("site", GAME_SITES, "games"), ("app", GAME_APPS, "games")):
        for name in names:
            if (kind, name) not in saved:
                db.set_category(kind, name, category)
                added += 1
            elif was and category == "games" and saved[(kind, name)] == "distracting":
                db.set_category(kind, name, "games")   # the list put it there before Games existed
                added += 1
    db.set_setting(VERSION_KEY, str(VERSION))
    return added


GAMES_KEY = "categories.steam_games"   # the one-off pass below has run
SEEN_KEY = "categories.steam_seen"     # the Steam games already given a category once


def seed_games(db, exes) -> int:
    """Your Steam games (exe names) go into Games unless you chose otherwise - each game only once, so changing
    one sticks. Games seeded as Distracting before that category existed are moved over, unless you have since
    put them somewhere else."""
    import json
    done = set(json.loads(db.get_setting(SEEN_KEY, "[]")))
    saved = db.categories()
    added = 0
    if db.get_setting(GAMES_KEY, "") != "1":
        for exe in done:
            if saved.get(("app", exe)) == "distracting":
                db.set_category("app", exe, "games")
                added += 1
        db.set_setting(GAMES_KEY, "1")
    for exe in exes:
        if exe in done:
            continue
        done.add(exe)
        if ("app", exe) not in saved:
            db.set_category("app", exe, "games")
            added += 1
    db.set_setting(SEEN_KEY, json.dumps(sorted(done)))
    return added
