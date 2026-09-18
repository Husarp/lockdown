"""Built-in quick-list of popular time-wasting sites.

The hosts file has no wildcards, so each site lists its main hostnames explicitly
(www. variants are added automatically).
"""

POPULAR_SITES: dict[str, dict[str, list[str]]] = {
    "Social": {
        "Facebook": ["facebook.com", "m.facebook.com", "fb.com"],
        "Instagram": ["instagram.com"],
        "Twitter/X": ["x.com", "twitter.com", "mobile.twitter.com"],
        "TikTok": ["tiktok.com"],
        "Reddit": ["reddit.com", "old.reddit.com", "new.reddit.com", "np.reddit.com", "redd.it"],
        "Snapchat": ["snapchat.com", "web.snapchat.com"],
        "LinkedIn": ["linkedin.com"],
    },
    "Streaming": {
        "YouTube": ["youtube.com", "m.youtube.com", "youtu.be"],
        "Netflix": ["netflix.com"],
        "Twitch": ["twitch.tv", "m.twitch.tv"],
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
