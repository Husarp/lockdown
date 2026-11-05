"""Display-only Network Log labels. Never used to decide what to block.

Add dedicated service/CDN suffixes here. Shared hosts (CloudFront, Akamai, etc.)
are deliberately left alone: a connection cannot identify their customer.
"""

SERVICE_DOMAINS = {
    "YouTube": ("youtube.com", "youtu.be", "googlevideo.com", "ytimg.com", "youtube-nocookie.com", "youtubei.googleapis.com"),
    "Google": ("google.com", "googleapis.com", "gstatic.com", "googleusercontent.com", "ggpht.com", "gmail.com", "doubleclick.net", "googlesyndication.com"),
    "Discord": ("discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media"),
    "Steam": ("steampowered.com", "steamcommunity.com", "steamstatic.com", "steamcontent.com", "steamserver.net", "steamcdn-a.akamaihd.net"),
    "Microsoft/Windows Update": ("windowsupdate.com", "update.microsoft.com", "delivery.mp.microsoft.com", "download.windowsupdate.com"),
    "Microsoft": ("microsoft.com", "microsoftonline.com", "live.com", "office.com", "office.net", "msftconnecttest.com", "msftncsi.com", "bing.com", "outlook.com"),
    "Xbox": ("xbox.com", "xboxlive.com"),
    "Netflix": ("netflix.com", "nflxvideo.net", "nflximg.net", "nflximg.com", "nflxso.net", "nflxext.com"),
    "Spotify": ("spotify.com", "scdn.co", "spotifycdn.com", "spotifycdn.net"),
    "Twitch": ("twitch.tv", "ttvnw.net", "jtvnw.net", "twitchcdn.net"),
    "Facebook": ("facebook.com", "facebook.net", "fbcdn.net", "fb.com"),
    "Instagram": ("instagram.com", "cdninstagram.com"),
    "WhatsApp": ("whatsapp.com", "whatsapp.net"),
    "TikTok": ("tiktok.com", "tiktokcdn.com", "tiktokv.com", "muscdn.com", "byteoversea.com"),
    "Reddit": ("reddit.com", "redd.it", "redditstatic.com", "redditmedia.com"),
    "X/Twitter": ("twitter.com", "x.com", "twimg.com", "t.co"),
    "Amazon": ("amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazonvideo.com", "primevideo.com", "media-amazon.com", "ssl-images-amazon.com"),
    "Apple": ("apple.com", "icloud.com", "icloud-content.com", "mzstatic.com"),
    "Zoom": ("zoom.us", "zoom.com"),
    "Telegram": ("telegram.org", "t.me"),
    "Signal": ("signal.org",),
    "GitHub": ("github.com", "githubusercontent.com", "githubassets.com"),
    "Dropbox": ("dropbox.com", "dropboxapi.com", "dropboxusercontent.com"),
    "Epic Games": ("epicgames.com", "unrealengine.com"),
    "Roblox": ("roblox.com", "rbxcdn.com"),
    "Infatica (bandwidth sharing)": ("infatica.io",),
}
_SUFFIXES = {suffix: service for service, suffixes in SERVICE_DOMAINS.items() for suffix in suffixes}


def service_name(host: str) -> str:
    """Longest whole-label suffix wins; unknown domains retain their original spelling."""
    suffix = host.lower().rstrip(".")
    while suffix:
        if suffix in _SUFFIXES:
            return _SUFFIXES[suffix]
        suffix = suffix.partition(".")[2]
    return host


def background_app(exe: str, name: str, windows: bool = False) -> str:
    """Only label known agents/system traffic; don't guess from lack of a window.

    Infatica is a proxyware/bandwidth-sharing SDK bundled by some free apps.
    Its presence doesn't establish which app installed it, or associate it with a video.
    """
    infatica = "infatica" in name.lower() or "infatica" in exe.lower()
    if infatica:
        return f"Background: {name} (bandwidth sharing)"
    return f"Background: {name}" if windows else name
