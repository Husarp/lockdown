"""The bundled site list (assets/sites + the phone's copy) and its loader in importer.popular."""
import io
import json
import pathlib

from PIL import Image

from blocker.hosts import normalize_host
from importer import popular
from importer.popular import POPULAR_SITES, site_of

ROOT = pathlib.Path(__file__).resolve().parent.parent
PC = ROOT / "assets" / "sites"
PHONE = ROOT / "android" / "app" / "src" / "main" / "assets" / "sites"
CATEGORIES = {"Social", "Video & streaming", "Gaming", "News", "Shopping", "Messaging", "Music & audio", "Sports",
              "Betting & gambling", "Dating", "Forums & communities", "AI & tools", "Work & productivity",
              "Education & reference", "Email", "Search", "Finance", "Travel & maps", "Tech", "Other"}


def _data():
    return json.loads((PC / "sites.json").read_text(encoding="utf-8"))


def test_json_valid_and_about_500():
    data = _data()
    assert data["version"] == 1
    assert set(data["categories"]) == CATEGORIES
    assert 480 <= len(data["sites"]) <= 520


def test_names_and_domains_unique_and_valid():
    sites = _data()["sites"]
    names = [s["name"] for s in sites]
    assert len(set(names)) == len(names)
    seen = {}
    for s in sites:
        assert s["category"] in CATEGORIES, s
        assert s["domains"], s
        for d in s["domains"]:
            assert normalize_host(d) == d, (s["name"], d)
            assert "." in d and not d.startswith("www."), (s["name"], d)
            assert d not in seen, (d, s["name"], seen.get(d))
            seen[d] = s["name"]


def test_quick_list_sites_present_with_identical_hosts():
    by_name = {s["name"]: s for s in _data()["sites"]}
    for sites in POPULAR_SITES.values():
        for name, hosts in sites.items():
            assert name in by_name, name
            assert by_name[name]["domains"] == hosts, name


def test_polish_favourites_present():
    mains = {s["domains"][0] for s in _data()["sites"]}
    for d in ("allegro.pl", "olx.pl", "onet.pl", "wp.pl", "interia.pl", "gazeta.pl", "wykop.pl", "pudelek.pl",
              "pepper.pl", "filmweb.pl", "player.pl", "cda.pl", "vinted.pl", "ceneo.pl"):
        assert d in mains, d


def test_phone_copy_is_byte_identical():
    assert (PC / "sites.json").read_bytes() == (PHONE / "sites.json").read_bytes()
    pc = {f.name: f.read_bytes() for f in (PC / "icons").iterdir()}
    phone = {f.name: f.read_bytes() for f in (PHONE / "icons").iterdir()}
    assert pc == phone


def test_icons_decode_small_and_belong_to_a_site():
    sites = _data()["sites"]
    mains = {s["domains"][0] for s in sites}
    files = sorted((PC / "icons").iterdir())
    assert 120 <= len(files) <= 180
    total = 0
    for f in files:
        assert f.suffix == ".png" and f.stem in mains, f.name
        total += f.stat().st_size
        im = Image.open(io.BytesIO(f.read_bytes()))
        im.load()
        assert max(im.size) <= 64, (f.name, im.size)
    assert total <= 500 * 1024, total
    # the "icon" field of a site is exactly the files that exist
    flagged = {s["icon"] for s in sites if "icon" in s}
    assert flagged == {f.name for f in files}
    for s in sites:
        if "icon" in s:
            assert s["icon"] == s["domains"][0] + ".png"


def test_site_of():
    assert site_of("m.youtube.com")["name"] == "YouTube"
    assert site_of("www.reddit.com")["name"] == "Reddit"
    assert site_of("i.redd.it")["name"] == "Reddit"
    assert site_of("EN.Wikipedia.org.")["name"] == "Wikipedia"
    assert site_of("mail.google.com")["name"] == "Gmail"       # the most specific listed domain wins
    assert site_of("news.google.com")["name"] == "Google"
    assert site_of("unknown.example") is None
    assert site_of("") is None
    assert site_of("notyoutube.com") is None                    # a parent domain, not a text suffix


def test_loader_ranks_and_icons():
    sites = popular.SITES
    assert sites is popular.load_sites()                         # cached
    assert [s["rank"] for s in sites] == list(range(1, len(sites) + 1))
    yt = site_of("youtube.com")
    assert yt["rank"] == 1 and yt["domains"][0] == "youtube.com"
    path = popular.site_icon_path(yt)
    assert path is not None and path.is_file()
    nameless = next(s for s in sites if "icon" not in s)
    assert popular.site_icon_path(nameless) is None
