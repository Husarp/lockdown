"""Built-in quick-list of popular time-wasting sites.

The hosts file has no wildcards, so each site lists its main hostnames explicitly
(www. variants are added automatically).

A site's page and its video are usually different domains: youtube.com serves the page, but the video comes
from a random host under googlevideo.com. Blocking only the page leaves an open player downloading to the end,
so the media domains are part of the site. They are matched with everything under them, which the hosts file
can't do - the DNS filter does it (see service.Enforcer.blocked_name).
"""

MEDIA_HOSTS_KEY = "media_hosts_added"   # the one-off pass below has run

POPULAR_SITES: dict[str, dict[str, list[str]]] = {
    "Social": {
        "Facebook": ["facebook.com", "m.facebook.com", "fb.com", "fbcdn.net"],
        "Instagram": ["instagram.com", "cdninstagram.com"],
        "Twitter/X": ["x.com", "twitter.com", "mobile.twitter.com"],
        "TikTok": ["tiktok.com", "tiktokcdn.com", "tiktokv.com"],
        "Reddit": ["reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "redd.it"],
        "Snapchat": ["snapchat.com", "web.snapchat.com"],
        "LinkedIn": ["linkedin.com"],
    },
    "Streaming": {
        "YouTube": ["youtube.com", "m.youtube.com", "youtu.be", "googlevideo.com"],
        "Netflix": ["netflix.com", "nflxvideo.net"],
        "Twitch": ["twitch.tv", "m.twitch.tv", "ttvnw.net"],
        "Disney+": ["disneyplus.com"],
        "Hulu": ["hulu.com"],
        "HBO Max": ["max.com", "hbomax.com", "play.max.com"],
        "Crunchyroll": ["crunchyroll.com"],
    },
    "Gaming": {
        "Steam Community": ["steamcommunity.com", "store.steampowered.com"],
        "Epic Games": ["epicgames.com", "store.epicgames.com"],
        "Roblox": ["roblox.com", "web.roblox.com"],
        "IGN": ["ign.com"],
    },
    "News/Feeds": {
        "CNN": ["cnn.com", "edition.cnn.com"],
        "BBC": ["bbc.com", "bbc.co.uk"],
        "Fox News": ["foxnews.com"],
        "Hacker News": ["news.ycombinator.com"],
        "BuzzFeed": ["buzzfeed.com"],
    },
    "Shopping": {
        "Amazon": ["amazon.com", "smile.amazon.com"],
        "eBay": ["ebay.com"],
        "AliExpress": ["aliexpress.com"],
        "Temu": ["temu.com"],
        "Shein": ["shein.com"],
    },
    "Other": {
        "Pinterest": ["pinterest.com"],
        "Tumblr": ["tumblr.com"],
        "9GAG": ["9gag.com"],
        "Imgur": ["imgur.com"],
    },
}


def hosts_of(target: str) -> list[str] | None:
    """The full hostname list of the known site this item is, or None if it isn't one of them."""
    have = set(target.lower().split())
    for sites in POPULAR_SITES.values():
        for hostnames in sites.values():
            if have & set(hostnames):
                return hostnames
    return None


def add_media_hosts(db) -> list[str]:
    """One-off: sites blocked before the media domains existed only have their page hostnames, so a blocked
    YouTube kept streaming from googlevideo.com. Give each known site the hostnames it is missing - once, so
    one you remove yourself stays removed. Returns the names that changed."""
    if db.get_setting(MEDIA_HOSTS_KEY, "") == "1":
        return []
    changed = []
    for item in db.list_items():
        if item["item_type"] != "site":
            continue
        known = hosts_of(item["target"])
        missing = [h for h in known or [] if h not in item["target"].lower().split()]
        if missing:
            db.update_item(item["id"], item["display_name"], item["target"].split() + missing, item["notify"],
                           item["rules"], item.get("block_type"), item.get("app_path"),
                           bool(item.get("disabled")))
            changed.append(item["display_name"])
    db.set_setting(MEDIA_HOSTS_KEY, "1")
    return changed
