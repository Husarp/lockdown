import socket
import struct
import threading

import keywords as kw
from blocker import dnsfilter
from monitor.word_guard import WordGuard

CFG = {**kw.DEFAULTS}


def test_find_words_in_address_and_title():
    assert kw.find("https://www.google.com/search?q=free+porn+videos", "", CFG) == "porn*"
    assert kw.find("https://www.pornhub.com/", "", CFG) == "porn*"                      # prefix word
    assert kw.find("https://example.com/", "Hot MILFs near you", CFG) == "milf*"
    assert kw.find("https://example.com/x", "Darmowy seks - filmy", CFG) == "seks"
    assert kw.find("https://pl.example.com/", "Ruchać za darmo", CFG) == "ruchac"         # accents ignored
    assert kw.find("https://example.com/watch?v=1", "SEX TAPES leaked", CFG) == "sex tape*"   # phrase
    assert kw.find("https://www.google.com/search?q=%70orn", "", CFG) == "porn*"          # %-encoded


def test_no_false_alarms():
    for url, title in [("https://en.wikipedia.org/wiki/Data_analysis", "Data analysis - Wikipedia"),
                       ("https://www.google.com/search?q=essex+county+cricket", "essex county - Google"),
                       ("https://www.google.com/search?q=sexton+family", ""),
                       ("https://pl.wikipedia.org/wiki/Edukacja_seksualna", "Edukacja seksualna"),
                       ("https://www.youtube.com/watch?v=abc", "Ruch drogowy - poradnik"),
                       ("https://example.com/", "Sex"),                                  # just "sex" isn't listed
                       ("https://www.google.com/search?q=cocktail+recipes", "")]:
        assert kw.find(url, title, CFG) is None, url


def test_typing_in_the_address_bar_isnt_checked():
    assert kw.find("free porn", "New Tab", CFG) is None          # search words being typed (spaces, no dot)
    assert kw.find("porn", "New Tab", CFG) is None
    assert kw.find("porn.com", "New Tab", CFG) == "porn*"         # an address


def test_own_words_and_exceptions():
    cfg = {**CFG, "words": ["gambling night"], "exceptions": ["xxx", "example.org"]}
    assert kw.find("https://a.com/", "Gambling Night special", cfg) == "gambling night"
    assert kw.find("https://a.com/", "XXX movie trailer", cfg) is None                # word exception
    assert kw.find("https://news.example.org/porn-study", "Study", cfg) is None       # site exception
    assert kw.find("https://a.com/", "gambling at night", cfg) is None               # phrase = those words in order


def test_ready_lists_and_words_turned_off():
    assert kw.find("https://a.com/", "Darmowy seks", {**CFG, "lists": {"adult_en": True, "adult_pl": False}}) is None
    assert kw.find("https://a.com/", "Hot MILFs", {**CFG, "off": ["milf*"]}) is None
    assert "milf*" not in kw.active_words({**CFG, "off": ["milf*"]})
    assert kw.active_words({**CFG, "lists": {}, "words": ["abc"]}) == ["abc"]


def test_what_weakens_the_check():
    old = {**CFG, "words": ["abc", "def"]}
    assert kw.looser(old, {**old, "words": ["abc", "def", "ghi"], "action": "back"}) == []   # more words: fine
    assert kw.looser(old, {**old, "lists": {**CFG["lists"], "adult_pl": False}}) == \
        ["Turn the Adult words - Polish list off"]
    assert kw.looser(old, {**old, "off": ["milf*"], "words": ["abc"]}) == ["Stop blocking 2 words: def, milf*"]
    assert kw.looser(old, {**old, "exceptions": ["reddit.com"]}) == ["New exceptions: reddit.com"]
    assert kw.looser(old, {**old, "enabled": False, "safesearch": False}) == \
        ["Turn the blocked-words check off", "Turn forced SafeSearch off"]
    assert kw.looser({**old, "off": ["milf*"]}, old) == []                                   # turned back on


def test_your_words_and_exceptions_switches():
    # "your words" off: your custom words stop matching, ready lists still do
    on = {**CFG, "words": ["gambling"]}
    assert "gambling" in kw.active_words(on) and kw.find("https://a.com/x", "gambling site", on) == "gambling"
    off = {**on, "words_on": False}
    assert "gambling" not in kw.active_words(off) and kw.find("https://a.com/x", "gambling site", off) is None
    assert "porn*" in kw.active_words(off)                                   # ready list unaffected
    # exceptions off: the exception stops applying (stricter)
    exc = {**CFG, "exceptions": ["xxx"]}
    assert kw.find("https://a.com/", "XXX movie", exc) is None               # exception allows it
    assert kw.find("https://a.com/", "XXX movie", {**exc, "exceptions_on": False}) == "xxx"
    # turning your words off is loosening (challenge); turning exceptions off is stricter (instant)
    assert kw.looser(on, off) == ["Turn your blocked words off"]
    assert kw.looser(exc, {**exc, "exceptions_on": False}) == []
    assert kw.looser({**exc, "exceptions_on": False}, exc) == ["Turn exceptions back on"]


def test_safe_targets():
    assert kw.safe_target("www.google.com") == kw.safe_target("google.pl") == "forcesafesearch.google.com"
    assert kw.safe_target("www.google.co.uk") == "forcesafesearch.google.com"
    assert kw.safe_target("mail.google.com") is None and kw.safe_target("googleapis.com") is None
    assert kw.safe_target("www.bing.com") == "strict.bing.com"
    assert kw.safe_target("duckduckgo.com") == "safe.duckduckgo.com"
    assert kw.safe_target("www.youtube.com") == kw.safe_target("youtubei.googleapis.com") == \
        "restrictmoderate.youtube.com"


def test_safesearch_and_youtube_are_separate_switches():
    # YouTube Restricted Mode off but SafeSearch on: search engines still safe, YouTube left alone (comments show)
    assert kw.safe_target("www.google.com", safesearch=True, youtube=False) == "forcesafesearch.google.com"
    assert kw.safe_target("www.youtube.com", safesearch=True, youtube=False) is None
    # the other way round
    assert kw.safe_target("www.google.com", safesearch=False, youtube=True) is None
    assert kw.safe_target("www.youtube.com", safesearch=False, youtube=True) == "restrictmoderate.youtube.com"
    assert kw.safe_target("www.google.com", safesearch=False, youtube=False) is None
    old = {**CFG}
    assert kw.looser(old, {**old, "youtube": False}) == ["Turn YouTube Restricted Mode off"]
    assert kw.looser({**old, "youtube": False}, old) == []                                    # turning it on: fine


def _query(name, qtype=1):
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split("."))
    return struct.pack(">HHHHHH", 0x4242, 0x0100, 1, 0, 0, 0) + labels + b"\0" + struct.pack(">HH", qtype, 1)


def test_dns_answers_search_engines_with_the_safe_address():
    upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    upstream.bind(("127.0.0.1", 0))
    asked = []

    def serve():   # answers every A query with 216.239.38.120 (as for forcesafesearch.google.com)
        while True:
            msg, addr = upstream.recvfrom(512)
            name, qtype, end = dnsfilter.question(msg)
            asked.append(name)
            answer = struct.pack(">HHHIH", 0xC00C, 1, 1, 300, 4) + socket.inet_aton("216.239.38.120")
            upstream.sendto(msg[:2] + struct.pack(">HHHHH", 0x8180, 1, 1, 0, 0) + msg[12:end] + answer, addr)
    threading.Thread(target=serve, daemon=True).start()
    server = dnsfilter.Server(lambda n: False, listen=[], port=0, safe=kw.safe_target)
    server.upstreams, server.upstream_port = ["127.0.0.1"], upstream.getsockname()[1]
    reply = server.answer(_query("www.google.com"))
    assert asked[-1] == "forcesafesearch.google.com"
    assert dnsfilter.question(reply)[0] == "www.google.com"                 # answered under the asked name
    assert dnsfilter.records(reply, 1) == [(300, socket.inet_aton("216.239.38.120"))]
    reply = server.answer(_query("www.google.com", qtype=65))             # HTTPS record: empty answer
    assert struct.unpack(">H", reply[6:8])[0] == 0 and asked[-1] == "forcesafesearch.google.com"


def test_word_guard_closes_or_goes_back():
    events, pressed = [], []
    guard = WordGuard(lambda word, action: events.append((word, action)))
    bad = (7, "https://www.google.com/search?q=porn", "porn - Google Search")
    do = lambda hwnd, action: pressed.append(action) or True
    guard.tick({**CFG, "action": "back"}, lambda: bad, do, 100.0)
    assert pressed == ["back"] and events == [("porn*", "back")]
    guard.tick({**CFG, "action": "back"}, lambda: bad, do, 100.5)          # give the browser a moment
    assert pressed == ["back"]
    guard.tick({**CFG, "action": "back"}, lambda: bad, do, 103.0)          # still there: nothing to go back to
    assert pressed == ["back", "close"]
    guard.tick(CFG, lambda: (7, "https://example.com/", "Example"), do, 104.0)
    guard.tick({**CFG, "enabled": False}, lambda: bad, do, 110.0)          # off
    assert pressed == ["back", "close"]
