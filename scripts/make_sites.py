"""Builds the bundled site data (PC and phone get identical bytes):
  assets/sites/sites.json + assets/sites/icons/<main domain>.png
  android/app/src/main/assets/sites/sites.json + .../icons/

The list below is the only source of truth (order = popularity, 1 = most popular). Run it by hand when the list
or the icons should change - it needs the network only for the icons and never runs inside the apps:

    python scripts/make_sites.py                 # keeps the icons already in assets/sites/icons, fetches missing ones
    python scripts/make_sites.py --refresh       # fetches every icon again
    python scripts/make_sites.py --no-fetch      # only rewrites the JSON and re-syncs the phone copy

Icons: 64x64 PNG (256-colour palette + alpha) for the first ICON_COUNT sites whose icon could be fetched; the
site's own icon first (home page <link rel=icon / apple-touch-icon>, then /favicon.ico), else Google's favicon
service. Blank images and Google's generic globe are skipped (reported at the end).

Line format of the list: Category|Name|main-domain other-host other-host ...
"""
import argparse
import hashlib
import io
import json
import pathlib
import re
import shutil
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin

ROOT = pathlib.Path(__file__).resolve().parent.parent
PC_DIR = ROOT / "assets" / "sites"
PHONE_DIR = ROOT / "android" / "app" / "src" / "main" / "assets" / "sites"

CATEGORIES = ["Social", "Video & streaming", "Gaming", "News", "Shopping", "Messaging", "Music & audio", "Sports",
              "Betting & gambling", "Dating", "Forums & communities", "AI & tools", "Work & productivity",
              "Education & reference", "Email", "Search", "Finance", "Travel & maps", "Tech", "Other"]
ICON_COUNT = 150          # icons to bundle (the top sites that have a usable one)
ICON_CANDIDATES = 200     # look this far down the list to fill ICON_COUNT
ICON_PX = 64
ICON_BUDGET = 500 * 1024  # per app

DATA = """\
Video & streaming|YouTube|youtube.com m.youtube.com youtu.be googlevideo.com
Social|Facebook|facebook.com m.facebook.com fb.com fbcdn.net
Social|Instagram|instagram.com cdninstagram.com
Search|Google|google.com google.pl
Social|Twitter/X|x.com twitter.com mobile.twitter.com
Social|TikTok|tiktok.com tiktokcdn.com tiktokv.com
Social|Reddit|reddit.com old.reddit.com new.reddit.com np.reddit.com redd.it
Messaging|WhatsApp|whatsapp.com web.whatsapp.com wa.me whatsapp.net
Education & reference|Wikipedia|wikipedia.org en.wikipedia.org pl.wikipedia.org
Shopping|Amazon|amazon.com smile.amazon.com
Video & streaming|Netflix|netflix.com nflxvideo.net
Messaging|Discord|discord.com discord.gg discordapp.com discordapp.net
Video & streaming|Twitch|twitch.tv m.twitch.tv ttvnw.net
Social|Pinterest|pinterest.com
Social|LinkedIn|linkedin.com
Social|Snapchat|snapchat.com web.snapchat.com
Messaging|Telegram|telegram.org web.telegram.org t.me telegram.me
Music & audio|Spotify|spotify.com open.spotify.com scdn.co
AI & tools|ChatGPT|chatgpt.com chat.openai.com openai.com
Email|Gmail|mail.google.com gmail.com googlemail.com
Search|Bing|bing.com
Shopping|Allegro|allegro.pl
Shopping|OLX|olx.pl
News|Onet|onet.pl
News|WP|wp.pl
News|Interia|interia.pl
News|Gazeta.pl|gazeta.pl
Forums & communities|Wykop|wykop.pl
Video & streaming|Disney+|disneyplus.com
Video & streaming|Hulu|hulu.com
Video & streaming|HBO Max|max.com hbomax.com play.max.com
Video & streaming|Prime Video|primevideo.com
Video & streaming|Crunchyroll|crunchyroll.com
Social|Tumblr|tumblr.com
Social|Threads|threads.net threads.com
Social|Bluesky|bsky.app bsky.social
Social|Imgur|imgur.com
Social|9GAG|9gag.com
Gaming|Steam Community|steamcommunity.com store.steampowered.com
Gaming|Epic Games|epicgames.com store.epicgames.com
Gaming|Roblox|roblox.com web.roblox.com
Gaming|IGN|ign.com
News|CNN|cnn.com edition.cnn.com
News|BBC|bbc.com bbc.co.uk
News|Fox News|foxnews.com
News|Hacker News|news.ycombinator.com
News|BuzzFeed|buzzfeed.com
Shopping|eBay|ebay.com
Shopping|AliExpress|aliexpress.com
Shopping|Temu|temu.com
Shopping|Shein|shein.com
Shopping|Vinted|vinted.pl vinted.com
Shopping|Ceneo|ceneo.pl
Shopping|Pepper.pl|pepper.pl
Video & streaming|Filmweb|filmweb.pl
Video & streaming|Player.pl|player.pl
Video & streaming|CDA|cda.pl
News|Pudelek|pudelek.pl
Social|Quora|quora.com
Forums & communities|Stack Overflow|stackoverflow.com stackexchange.com
Video & streaming|Vimeo|vimeo.com vimeocdn.com
Video & streaming|Dailymotion|dailymotion.com dai.ly
Video & streaming|Paramount+|paramountplus.com
Video & streaming|Peacock|peacocktv.com
Video & streaming|Apple TV|tv.apple.com
Video & streaming|Rumble|rumble.com
Video & streaming|Kick|kick.com
Video & streaming|Bilibili|bilibili.com
Video & streaming|IMDb|imdb.com
Video & streaming|Tubi|tubitv.com
Video & streaming|Pluto TV|pluto.tv
Video & streaming|Plex|plex.tv
Video & streaming|Canal+|canalplus.com
Video & streaming|TVP VOD|vod.tvp.pl
Video & streaming|Odysee|odysee.com
Video & streaming|Rotten Tomatoes|rottentomatoes.com
Video & streaming|Ipla|ipla.tv
Messaging|Messenger|messenger.com
Messaging|Signal|signal.org
Messaging|Viber|viber.com
Messaging|Skype|skype.com
Messaging|WeChat|wechat.com
Messaging|LINE|line.me
Social|Mastodon|mastodon.social
Social|Medium|medium.com
Social|Flickr|flickr.com
Social|VK|vk.com vk.ru
Social|Goodreads|goodreads.com
Social|DeviantArt|deviantart.com
Social|Giphy|giphy.com
Social|Substack|substack.com
Gaming|Xbox|xbox.com
News|The New York Times|nytimes.com
Forums & communities|Hejto|hejto.pl
Shopping|Walmart|walmart.com
Music & audio|SoundCloud|soundcloud.com sndcdn.com
Sports|ESPN|espn.com
Betting & gambling|Bet365|bet365.com
Dating|Tinder|tinder.com
AI & tools|Claude|claude.ai anthropic.com
Work & productivity|Slack|slack.com
Education & reference|Khan Academy|khanacademy.org
Email|Outlook|outlook.com outlook.live.com hotmail.com
Search|DuckDuckGo|duckduckgo.com
Finance|PayPal|paypal.com
Travel & maps|Google Maps|maps.google.com
Tech|GitHub|github.com githubusercontent.com
Other|AccuWeather|accuweather.com
Gaming|PlayStation|playstation.com
News|The Washington Post|washingtonpost.com
Forums & communities|Elektroda|elektroda.pl
Shopping|Target|target.com
Music & audio|Apple Music|music.apple.com
Sports|Sky Sports|skysports.com
Betting & gambling|Betfair|betfair.com
Dating|Bumble|bumble.com
AI & tools|Gemini|gemini.google.com
Work & productivity|Zoom|zoom.us
Education & reference|Coursera|coursera.org
Email|Yahoo Mail|mail.yahoo.com ymail.com
Search|Yahoo|yahoo.com
Finance|Binance|binance.com
Travel & maps|Booking.com|booking.com
Tech|GitLab|gitlab.com
Other|Weather.com|weather.com
Gaming|Nintendo|nintendo.com
News|The Guardian|theguardian.com
Forums & communities|Wizaz|wizaz.pl
Shopping|Etsy|etsy.com
Music & audio|Deezer|deezer.com
Sports|Bleacher Report|bleacherreport.com
Betting & gambling|William Hill|williamhill.com
Dating|Hinge|hinge.co
AI & tools|Microsoft Copilot|copilot.microsoft.com
Work & productivity|Microsoft Teams|teams.microsoft.com
Education & reference|Udemy|udemy.com
Email|Proton Mail|proton.me protonmail.com
Search|Yandex|yandex.com yandex.ru
Finance|Coinbase|coinbase.com
Travel & maps|Airbnb|airbnb.com
Tech|Apple|apple.com
Other|Twoja Pogoda|twojapogoda.pl
Gaming|Battle.net|battle.net blizzard.com
News|Reuters|reuters.com
Forums & communities|Kafeteria|kafeteria.pl
Shopping|Best Buy|bestbuy.com
Music & audio|Tidal|tidal.com
Sports|NBA|nba.com
Betting & gambling|Ladbrokes|ladbrokes.com
Dating|Badoo|badoo.com
AI & tools|Perplexity|perplexity.ai
Work & productivity|Notion|notion.so notion.com
Education & reference|Duolingo|duolingo.com
Email|iCloud|icloud.com
Search|Baidu|baidu.com
Finance|Revolut|revolut.com
Travel & maps|Tripadvisor|tripadvisor.com
Tech|Microsoft|microsoft.com
Other|Poczta Polska|poczta-polska.pl
Gaming|EA|ea.com
News|Associated Press|apnews.com
Forums & communities|Chomikuj|chomikuj.pl
Shopping|Costco|costco.com
Music & audio|Pandora|pandora.com
Sports|NFL|nfl.com
Betting & gambling|Paddy Power|paddypower.com
Dating|OkCupid|okcupid.com
AI & tools|Character.AI|character.ai
Work & productivity|Trello|trello.com
Education & reference|edX|edx.org
Email|GMX|gmx.com gmx.net
Search|Brave Search|search.brave.com
Finance|Wise|wise.com
Travel & maps|Expedia|expedia.com
Tech|Google Play|play.google.com
Other|InPost|inpost.pl
Gaming|Ubisoft|ubisoft.com
News|NBC News|nbcnews.com
Forums & communities|Kwejk|kwejk.pl
Shopping|IKEA|ikea.com
Music & audio|Bandcamp|bandcamp.com
Sports|UEFA|uefa.com
Betting & gambling|888|888.com
Dating|Match|match.com
AI & tools|DeepSeek|deepseek.com chat.deepseek.com
Work & productivity|Asana|asana.com
Education & reference|Quizlet|quizlet.com
Email|AOL|aol.com
Search|Ecosia|ecosia.org
Finance|Robinhood|robinhood.com
Travel & maps|Skyscanner|skyscanner.net
Tech|Cloudflare|cloudflare.com
Other|DHL|dhl.com
Gaming|Riot Games|riotgames.com
News|CBS News|cbsnews.com
Forums & communities|Demotywatory|demotywatory.pl
Shopping|Zalando|zalando.com zalando.pl
Music & audio|Last.fm|last.fm
Sports|FIFA|fifa.com
Betting & gambling|Unibet|unibet.com
Dating|eHarmony|eharmony.com
AI & tools|Grok|grok.com
Work & productivity|Atlassian|atlassian.com atlassian.net
Education & reference|Brainly|brainly.com brainly.pl
Search|Startpage|startpage.com
Finance|Yahoo Finance|finance.yahoo.com
Travel & maps|Uber|uber.com
Tech|Mozilla|mozilla.org
Other|Patreon|patreon.com
Gaming|League of Legends|leagueoflegends.com
News|ABC News|abcnews.go.com
Forums & communities|JoeMonster|joemonster.org
Shopping|H&M|hm.com
Music & audio|Audible|audible.com
Sports|Transfermarkt|transfermarkt.com transfermarkt.pl
Betting & gambling|Betway|betway.com
Dating|Plenty of Fish|pof.com
AI & tools|Midjourney|midjourney.com
Work & productivity|Google Drive|drive.google.com
Education & reference|Google Scholar|scholar.google.com
Search|Qwant|qwant.com
Finance|TradingView|tradingview.com
Travel & maps|Bolt|bolt.eu
Tech|The Verge|theverge.com
Other|Kickstarter|kickstarter.com
Gaming|Valorant|playvalorant.com
News|NPR|npr.org
Forums & communities|JBZD|jbzd.com.pl
Shopping|Zara|zara.com
Music & audio|Genius|genius.com
Sports|Flashscore|flashscore.com flashscore.pl
Betting & gambling|DraftKings|draftkings.com
Dating|Grindr|grindr.com
AI & tools|Hugging Face|huggingface.co
Work & productivity|Google Docs|docs.google.com
Education & reference|Britannica|britannica.com
Finance|CoinMarketCap|coinmarketcap.com
Travel & maps|Waze|waze.com
Tech|TechCrunch|techcrunch.com
Other|GoFundMe|gofundme.com
Gaming|Fortnite|fortnite.com
News|Forbes|forbes.com
Forums & communities|4chan|4chan.org 4channel.org
Shopping|Nike|nike.com
Music & audio|Shazam|shazam.com
Sports|SofaScore|sofascore.com
Betting & gambling|FanDuel|fanduel.com
Dating|Happn|happn.com
AI & tools|Poe|poe.com
Work & productivity|Google Calendar|calendar.google.com
Education & reference|Merriam-Webster|merriam-webster.com
Finance|CoinGecko|coingecko.com
Travel & maps|OpenStreetMap|openstreetmap.org
Tech|WIRED|wired.com
Other|Zrzutka.pl|zrzutka.pl
Gaming|Minecraft|minecraft.net
News|Daily Mail|dailymail.co.uk
Forums & communities|Product Hunt|producthunt.com
Shopping|Adidas|adidas.com
Music & audio|TuneIn|tunein.com
Sports|LiveScore|livescore.com
Betting & gambling|PokerStars|pokerstars.com
Dating|Meetic|meetic.com
AI & tools|Canva|canva.com
Work & productivity|Google Meet|meet.google.com
Education & reference|Dictionary.com|dictionary.com
Finance|Investing.com|investing.com
Travel & maps|Ryanair|ryanair.com
Tech|Ars Technica|arstechnica.com
Other|Pastebin|pastebin.com
Gaming|GOG|gog.com
News|The Sun|thesun.co.uk
Forums & communities|Slashdot|slashdot.org
Shopping|Allegro Lokalnie|allegrolokalnie.pl
Music & audio|Radio ZET|radiozet.pl
Sports|Sport.pl|sport.pl
Betting & gambling|1xBet|1xbet.com
AI & tools|Grammarly|grammarly.com
Work & productivity|Dropbox|dropbox.com
Education & reference|Internet Archive|archive.org
Finance|Bloomberg|bloomberg.com
Travel & maps|Wizz Air|wizzair.com
Tech|Engadget|engadget.com
Other|Unsplash|unsplash.com
Gaming|itch.io|itch.io
News|Mirror|mirror.co.uk
Forums & communities|Know Your Meme|knowyourmeme.com
Shopping|Empik|empik.com
Music & audio|Eska|eska.pl
Sports|Meczyki|meczyki.pl
Betting & gambling|Stake|stake.com
AI & tools|DeepL|deepl.com
Work & productivity|OneDrive|onedrive.live.com
Education & reference|TED|ted.com
Finance|MarketWatch|marketwatch.com
Travel & maps|LOT Polish Airlines|lot.com
Tech|CNET|cnet.com
Gaming|Chess.com|chess.com
News|The Independent|independent.co.uk
Shopping|Media Expert|mediaexpert.pl
Music & audio|Antyradio|antyradio.pl
Sports|Goal.pl|goal.pl
Betting & gambling|STS|sts.pl
AI & tools|Google Translate|translate.google.com
Work & productivity|Microsoft Office|office.com
Education & reference|Codecademy|codecademy.com
Finance|CNBC|cnbc.com
Travel & maps|easyJet|easyjet.com
Tech|Gizmodo|gizmodo.com
Gaming|Lichess|lichess.org
News|The Telegraph|telegraph.co.uk
Shopping|RTV Euro AGD|euro.com.pl
Music & audio|Open FM|open.fm
Sports|Eurosport|eurosport.com
Betting & gambling|Fortuna|efortuna.pl
AI & tools|Mistral AI|mistral.ai chat.mistral.ai
Work & productivity|Figma|figma.com
Education & reference|freeCodeCamp|freecodecamp.org
Finance|eToro|etoro.com
Travel & maps|Lufthansa|lufthansa.com
Tech|Android Authority|androidauthority.com
Gaming|Poki|poki.com
News|Sky News|news.sky.com
Shopping|MediaMarkt|mediamarkt.pl
Music & audio|Tekstowo|tekstowo.pl
Sports|Strava|strava.com
Betting & gambling|Superbet|superbet.pl
AI & tools|Cursor|cursor.com
Work & productivity|Miro|miro.com
Education & reference|W3Schools|w3schools.com
Finance|XTB|xtb.com
Travel & maps|PKP Intercity|intercity.pl
Tech|Tom's Hardware|tomshardware.com
Gaming|Miniclip|miniclip.com
News|Al Jazeera|aljazeera.com
Shopping|x-kom|x-kom.pl
Sports|WWE|wwe.com
Betting & gambling|Betclic|betclic.pl
AI & tools|Wolfram Alpha|wolframalpha.com
Work & productivity|Airtable|airtable.com
Education & reference|MDN Web Docs|developer.mozilla.org
Finance|Kraken|kraken.com
Travel & maps|Jakdojade|jakdojade.pl
Tech|ZDNet|zdnet.com
Gaming|CrazyGames|crazygames.com
News|HuffPost|huffpost.com
Shopping|Morele|morele.net
Sports|UFC|ufc.com
Betting & gambling|Lotto|lotto.pl
AI & tools|Speedtest|speedtest.net
Work & productivity|monday.com|monday.com
Education & reference|LeetCode|leetcode.com
Finance|Bybit|bybit.com
Travel & maps|FlixBus|flixbus.com
Tech|GSMArena|gsmarena.com
Gaming|osu!|osu.ppy.sh ppy.sh
News|Business Insider|businessinsider.com
Shopping|Answear|answear.com
Sports|Formula 1|formula1.com
Betting & gambling|Betano|betano.com
Work & productivity|ClickUp|clickup.com
Education & reference|Kaggle|kaggle.com
Finance|OKX|okx.com
Travel & maps|Trivago|trivago.com
Tech|DEV Community|dev.to
Gaming|GameSpot|gamespot.com
News|Vox|vox.com
Shopping|Reserved|reserved.com
Sports|Premier League|premierleague.com
Betting & gambling|Casumo|casumo.com
Work & productivity|Evernote|evernote.com
Education & reference|Babbel|babbel.com
Finance|KuCoin|kucoin.com
Travel & maps|Agoda|agoda.com
Tech|npm|npmjs.com
Gaming|Polygon|polygon.com
News|Politico|politico.com
Shopping|Sinsay|sinsay.com
Sports|Weszlo|weszlo.com
Betting & gambling|LeoVegas|leovegas.com
Work & productivity|Todoist|todoist.com
Education & reference|Google Classroom|classroom.google.com
Finance|Stripe|stripe.com
Travel & maps|Wakacje.pl|wakacje.pl
Tech|PyPI|pypi.org
Gaming|Kotaku|kotaku.com
News|USA Today|usatoday.com
Shopping|Decathlon|decathlon.pl decathlon.com
Work & productivity|Zendesk|zendesk.com
Education & reference|Kahoot|kahoot.com kahoot.it
Finance|Klarna|klarna.com
Tech|Docker|docker.com
Gaming|PC Gamer|pcgamer.com
News|MSN|msn.com
Shopping|Leroy Merlin|leroymerlin.pl
Work & productivity|Salesforce|salesforce.com
Education & reference|Librus|librus.pl
Finance|PKO Bank Polski|pkobp.pl ipko.pl
Tech|Vercel|vercel.com
Gaming|Eurogamer|eurogamer.net
News|Newsweek|newsweek.com
Shopping|Castorama|castorama.pl
Work & productivity|HubSpot|hubspot.com
Education & reference|Zadane.pl|zadane.pl
Finance|mBank|mbank.pl
Tech|Netlify|netlify.com
Gaming|Metacritic|metacritic.com
News|Breitbart|breitbart.com
Shopping|Biedronka|biedronka.pl
Work & productivity|Calendly|calendly.com
Finance|ING Bank Slaski|ing.pl
Tech|DigitalOcean|digitalocean.com
Gaming|Fandom|fandom.com
News|The Atlantic|theatlantic.com
Shopping|Lidl|lidl.pl
Work & productivity|Webex|webex.com
Finance|Santander Bank Polska|santander.pl
Tech|Samsung|samsung.com
Gaming|GRY-Online|gry-online.pl
News|The Wall Street Journal|wsj.com
Shopping|Zabka|zabka.pl
Work & productivity|Box|box.com
Finance|Bank Millennium|bankmillennium.pl
Tech|NVIDIA|nvidia.com
Gaming|Rockstar Games|rockstargames.com
News|Financial Times|ft.com
Shopping|Rossmann|rossmann.pl
Work & productivity|WeTransfer|wetransfer.com
Finance|Bank Pekao|pekao.com.pl
Tech|Ubuntu|ubuntu.com
News|The Economist|economist.com
Shopping|Hebe|hebe.pl
Work & productivity|Mailchimp|mailchimp.com
Finance|BLIK|blik.com
Tech|Antyweb|antyweb.pl
News|TIME|time.com
Shopping|Douglas|douglas.pl
Work & productivity|Zapier|zapier.com
Finance|Bankier.pl|bankier.pl
Tech|Spider's Web|spidersweb.pl
News|Fakt|fakt.pl
Shopping|Sephora|sephora.pl
Work & productivity|DocuSign|docusign.com
Finance|Money.pl|money.pl
Tech|Komputer Swiat|komputerswiat.pl
News|Super Express|se.pl
Shopping|Notino|notino.pl
Work & productivity|Adobe|adobe.com
Finance|Seeking Alpha|seekingalpha.com
Tech|CHIP|chip.pl
News|Wyborcza|wyborcza.pl
Shopping|Pyszne.pl|pyszne.pl
Work & productivity|Wix|wix.com
Finance|Nasdaq|nasdaq.com
Tech|Benchmark.pl|benchmark.pl
News|TVN24|tvn24.pl
Shopping|Glovo|glovoapp.com
Work & productivity|WordPress|wordpress.com wordpress.org
Finance|Stooq|stooq.pl stooq.com
Tech|PurePC|purepc.pl
News|Polsat News|polsatnews.pl
Shopping|Uber Eats|ubereats.com
Work & productivity|Squarespace|squarespace.com
Finance|Investopedia|investopedia.com
Tech|Dobreprogramy|dobreprogramy.pl
News|TVP Info|tvp.info
Shopping|Wolt|wolt.com
Work & productivity|Shopify|shopify.com
Tech|Niebezpiecznik|niebezpiecznik.pl
News|TVP|tvp.pl
Shopping|Craigslist|craigslist.org
Work & productivity|Upwork|upwork.com
Tech|Sekurak|sekurak.pl
News|Plotek|plotek.pl
Shopping|Wayfair|wayfair.com
Work & productivity|Fiverr|fiverr.com
News|O2|o2.pl
Shopping|Home Depot|homedepot.com
Work & productivity|Indeed|indeed.com
News|Rzeczpospolita|rp.pl
Shopping|Lowe's|lowes.com
Work & productivity|Glassdoor|glassdoor.com
News|Wprost|wprost.pl
Shopping|Newegg|newegg.com
Work & productivity|Pracuj.pl|pracuj.pl
News|Polityka|polityka.pl
Shopping|Rakuten|rakuten.com
Work & productivity|GoWork|gowork.pl
News|Business Insider Polska|businessinsider.com.pl
Shopping|Flipkart|flipkart.com
Work & productivity|No Fluff Jobs|nofluffjobs.com
News|Natemat|natemat.pl
Shopping|Alibaba|alibaba.com
Work & productivity|Just Join IT|justjoin.it
News|RMF24|rmf24.pl
Shopping|Kaufland|kaufland.pl
Work & productivity|SurveyMonkey|surveymonkey.com
Shopping|Carrefour|carrefour.pl
Shopping|G2A|g2a.com
Shopping|Kinguin|kinguin.net
Shopping|Humble Bundle|humblebundle.com
Shopping|Wish|wish.com
"""


def parse_sites() -> list[dict]:
    sites = []
    for line in DATA.strip().splitlines():
        cat, name, hosts = line.split("|")
        assert cat in CATEGORIES, line
        sites.append({"name": name, "domains": hosts.split(), "category": cat})
    return sites


# ---------------------------------------------------------------------------------------------- icons

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def get(url: str, limit: int = 400_000, timeout: float = 8) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(limit)
    except Exception:
        return None


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.icons: list[tuple[int, str]] = []   # (size hint, href)

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        rel = (a.get("rel") or "").lower().split()
        href = a.get("href")
        if not href or not ("icon" in rel or "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel):
            return
        if href.lower().split("?")[0].endswith(".svg"):
            return
        m = re.match(r"(\d+)x\d+", (a.get("sizes") or "").lower())
        size = int(m.group(1)) if m else (180 if "apple-touch-icon" in rel else 0)
        self.icons.append((size, href))


def site_candidates(domain: str) -> list[str]:
    """Icon URLs of the site itself, best (largest declared) first."""
    urls: list[tuple[int, str]] = []
    for base in (f"https://{domain}/", f"https://www.{domain}/"):
        html = get(base, 300_000)
        if not html:
            continue
        p = _Links()
        try:
            p.feed(html.decode("utf-8", "ignore"))
        except Exception:
            pass
        urls += [(s, urljoin(base, h)) for s, h in p.icons]
        urls.append((1, urljoin(base, "/favicon.ico")))
        break
    else:
        urls.append((1, f"https://{domain}/favicon.ico"))
    urls.sort(key=lambda t: -t[0])
    seen, out = set(), []
    for _, u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out[:5]


def decode(data: bytes | None):
    from PIL import Image
    if not data:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        if im.format == "ICO":   # biggest frame of the .ico
            best = max(im.ico.sizes(), key=lambda s: s[0])
            im.size = best
            im.load()
        im = im.convert("RGBA")
        return im
    except Exception:
        return None


def informative(im) -> bool:
    """False for a blank / single-colour image."""
    raw = im.resize((16, 16)).tobytes()
    cols = {(raw[i] // 16, raw[i + 1] // 16, raw[i + 2] // 16) for i in range(0, len(raw), 4) if raw[i + 3] > 40}
    return len(cols) >= 2


def finish(im) -> bytes:
    """64x64, palette PNG with alpha - small and decodes everywhere."""
    from PIL import Image
    w, h = im.size
    side = max(w, h)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(im, ((side - w) // 2, (side - h) // 2))
    sq = sq.resize((ICON_PX, ICON_PX), Image.LANCZOS)
    q = sq.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    q.save(buf, "PNG", optimize=True)
    return buf.getvalue()


_GLOBE: set[str] = set()


def google_icon(domain: str):
    data = get(f"https://www.google.com/s2/favicons?domain={domain}&sz=64", 100_000)
    if not data or hashlib.md5(data).hexdigest() in _GLOBE:
        return None
    return decode(data)


def fetch_icon(domain: str) -> tuple[bytes | None, str]:
    """(png bytes, source) or (None, reason)."""
    best = None
    for url in site_candidates(domain):
        im = decode(get(url, 600_000))
        if im and informative(im) and (best is None or im.size[0] > best.size[0]):
            best = im
            if im.size[0] >= 48:
                break
    src = "site"
    if best is None or best.size[0] < 32:
        g = google_icon(domain)
        if g is not None and informative(g) and (best is None or g.size[0] > best.size[0]):
            best, src = g, "google"
    if best is None:
        return None, "no usable icon (none found, blank, or Google's generic globe)"
    return finish(best), src


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="fetch every icon again")
    ap.add_argument("--no-fetch", action="store_true", help="no network: keep the icons that are there")
    args = ap.parse_args()

    sites = parse_sites()
    names = [s["name"] for s in sites]
    doms = [d for s in sites for d in s["domains"]]
    assert len(set(names)) == len(names), "duplicate name"
    assert len(set(doms)) == len(doms), [d for d in doms if doms.count(d) > 1]

    icon_dir = PC_DIR / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    report = []
    if not args.no_fetch:
        for u in ("https://www.google.com/s2/favicons?domain=nonexistent-zzq-lockdown.invalid&sz=64",
                  "https://www.google.com/s2/favicons?domain=nonexistent-zzq-lockdown.example&sz=64"):
            d = get(u, 100_000)
            if d:
                _GLOBE.add(hashlib.md5(d).hexdigest())
        todo = []
        have = 0
        for s in sites[:ICON_CANDIDATES]:
            f = icon_dir / f"{s['domains'][0]}.png"
            if f.exists() and not args.refresh:
                have += 1
            else:
                todo.append(s)
        want = ICON_COUNT - have
        # fetch in rank order, stop once enough succeeded
        with ThreadPoolExecutor(12) as ex:
            results = list(ex.map(lambda s: (s, fetch_icon(s["domains"][0])), todo))
        for s, (png, why) in results:
            if want <= 0:
                break
            if png is None:
                report.append((s["name"], s["domains"][0], why))
                continue
            (icon_dir / f"{s['domains'][0]}.png").write_bytes(png)
            want -= 1
    # keep only icons of listed main domains, and no more than ICON_COUNT, in rank order
    mains = [s["domains"][0] for s in sites]
    present = [d for d in mains if (icon_dir / f"{d}.png").exists()]
    keep = set(present[:ICON_COUNT])
    for f in icon_dir.glob("*.png"):
        if f.stem not in keep:
            f.unlink()
    for s in sites:
        if s["domains"][0] in keep:
            s["icon"] = f"{s['domains'][0]}.png"

    lines = [json.dumps(s, ensure_ascii=False, separators=(", ", ": ")) for s in sites]
    text = ('{"version": 1, "categories": ' + json.dumps(CATEGORIES) + ',\n"sites": [\n'
            + ",\n".join(lines) + "\n]}\n")
    (PC_DIR / "sites.json").write_text(text, encoding="utf-8", newline="\n")

    # phone copy: identical bytes
    if PHONE_DIR.exists():
        shutil.rmtree(PHONE_DIR)
    (PHONE_DIR / "icons").mkdir(parents=True)
    shutil.copy2(PC_DIR / "sites.json", PHONE_DIR / "sites.json")
    for f in sorted(icon_dir.glob("*.png")):
        shutil.copy2(f, PHONE_DIR / "icons" / f.name)

    total = sum(f.stat().st_size for f in icon_dir.glob("*.png"))
    print(f"{len(sites)} sites, {len(set(s['category'] for s in sites))} categories, {len(keep)} icons, "
          f"{total} icon bytes, sites.json {len((PC_DIR / 'sites.json').read_bytes())} bytes")
    if total > ICON_BUDGET:
        print("WARNING: icons over budget", file=sys.stderr)
    for name, dom, why in report:
        print(f"skipped {name} ({dom}): {why}")


if __name__ == "__main__":
    main()
