"""Getting a site's real icon from the net - on a background thread of resident/icons.py, never on the page's time.

The chain, stopping at the first icon that decodes:
  a) the site itself: https://<site>/favicon.ico, then the home page's first 200 KB read for <link rel="icon" |
     "shortcut icon" | "apple-touch-icon"> (the best size from 32 px up, at most MAX_CANDIDATES tried);
  b) only when a) gives nothing: the favicon service (Google s2) - it is told which site is asked about, that is why the
     Settings page has a switch for it.

Lockdown must never cause its own blocked-visit alert. Its hosts file (and its DNS filter, for the protection lists) send
a blocked site to 127.0.0.1, where its listener logs a visit and shows an alert. So the site itself is not contacted when
it is blocked right now (its name, or a parent domain, is in Lockdown's part of the hosts file) - and no request of any
kind goes to an address that is not public: every name is looked up first, and only when EVERY address is a public one
(no loopback, private, link-local, unspecified, reserved, multicast) the connection is made - to the address that was
checked, so a second lookup can't give another answer. Redirects are followed by hand (at most MAX_REDIRECTS) and checked
the same way at every step; only http(s) on ports 80 / 443; names without a dot, IP addresses and local names (.local,
.lan, .internal ...) are not asked about at all (not even the service). That also keeps LAN hosts out.

Limits: 5 s per network step, REQUEST_SEC for one whole request (a server that drips bytes can't hold a thread), 200 KB
per response, PIL only for the formats of FORMATS and never for a picture over MAX_PIXELS. Output: a PNG of at most 64 px
with its transparency."""
import http.client
import io
import ipaddress
import socket
import ssl
import threading
import time
import warnings
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

from resident.icons import is_public_host

FAVICON_URL = "https://www.google.com/s2/favicons?domain={domain}&sz=64"
MAX_BYTES = 200_000            # per response
TIMEOUT = 5                    # seconds per network step (connect, each read)
REQUEST_SEC = 12               # seconds for one request, everything in
MAX_REDIRECTS = 3
MAX_CANDIDATES = 3             # <link> icons tried from a home page
MAX_LINKS = 30                 # <link> icons looked at in a home page
MAX_PIXELS = 1024 * 1024       # a picture declaring more is never decoded
OUT_SIZE = 64                  # the longest side of what is kept
BEST_FROM = 32                 # smaller icons are the last choice
FORMATS = ["PNG", "ICO", "JPEG", "GIF", "WEBP", "BMP"]   # (no EPS: PIL would start Ghostscript for it)
PORTS = {"http": 80, "https": 443}
REDIRECTS = (301, 302, 303, 307, 308)
USER_AGENT = "Mozilla/5.0 (compatible; Lockdown)"
ICON_RELS = {"icon", "apple-touch-icon", "apple-touch-icon-precomposed"}


# ---------------------------------------------------------------- which addresses may be contacted

def is_public_ip(ip: str) -> bool:
    """A global unicast address. False for loopback, private, link-local, unspecified, reserved, multicast, shared
    (100.64/10) and for anything that does not parse - and an IPv4 address wrapped in IPv6 is judged as the IPv4 one."""
    try:
        addr = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return addr.is_global and not addr.is_multicast


def resolve_public(host: str, getaddrinfo=socket.getaddrinfo) -> list[tuple[int, str]] | None:
    """[(family, address)] of the host (IPv4 first) - or None unless it resolves and EVERY address is public."""
    try:
        infos = getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return None
    found: list[tuple[int, str]] = []
    for family, _kind, _proto, _name, sockaddr in infos:
        ip = sockaddr[0]
        if not is_public_ip(ip):
            return None
        if (family, ip) not in found:
            found.append((family, ip))
    found.sort(key=lambda a: a[0] != socket.AF_INET)
    return found or None


def hosts_blocked(host: str, path=None) -> bool:
    """Is this site blocked right now - its name, or a parent domain of it, in Lockdown's part of the hosts file (the
    service writes exactly the sites that are blocked at the moment)? False when the file can't be read."""
    names = _blocked_names(path)
    parts = host.lower().rstrip(".").removeprefix("www.").split(".")
    return any(".".join(parts[i:]) in names for i in range(len(parts) - 1))


_names_cache: dict = {}


def _blocked_names(path=None) -> frozenset:
    from blocker.hosts import END_MARKER, START_MARKER
    if path is None:
        from paths import HOSTS_PATH as path
    try:
        info = path.stat()
        key = (str(path), info.st_mtime_ns, info.st_size)
        if _names_cache.get("key") == key:
            return _names_cache["names"]
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return frozenset()
    names, inside = set(), False
    for line in text.splitlines():
        line = line.strip()
        if line == START_MARKER:
            inside = True
        elif line == END_MARKER:
            inside = False
        elif inside and line and not line.startswith("#"):
            names.update(n.lower().removeprefix("www.") for n in line.split()[1:])
    _names_cache.update(key=key, names=frozenset(names))
    return _names_cache["names"]


# ---------------------------------------------------------------- one safe request

class _Pinned:
    """Connects to an address that was already checked (not to the name again), whatever name the request is for."""
    def connect(self):
        error = OSError("no address to connect to")
        for _family, ip in self._addresses:
            try:
                sock = socket.create_connection((ip, self.port), self.timeout)
                break
            except OSError as e:
                error = e
        else:
            raise error
        self.sock = self._wrap(sock)


class _Http(_Pinned, http.client.HTTPConnection):
    def _wrap(self, sock):
        return sock


class _Https(_Pinned, http.client.HTTPSConnection):
    def _wrap(self, sock):
        return self._context.wrap_socket(sock, server_hostname=self.host)   # (the certificate is checked for the NAME)


_tls: list = []
_tls_lock = threading.Lock()


def _context():
    """The TLS settings (certificates of the system, host name checked), made once - loading the Windows store is slow."""
    with _tls_lock:
        if not _tls:
            _tls.append(ssl.create_default_context())
        return _tls[0]


def connect(scheme: str, host: str, addresses: list, port: int, timeout: float):
    """A not-yet-connected connection for `host` that will connect to `addresses` (see _Pinned)."""
    if scheme == "https":
        conn = _Https(host, port, timeout=timeout, context=_context())
    else:
        conn = _Http(host, port, timeout=timeout)
    conn._addresses = addresses
    return conn


def _read(resp, limit: int, deadline: float, clock) -> bytes | None:
    """At most `limit` bytes of the body; None when it takes longer than the deadline."""
    chunks, size = [], 0
    while size < limit:
        if clock() > deadline:
            return None
        chunk = resp.read(min(8192, limit - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def http_get(url: str, limit: int = MAX_BYTES, timeout: float = TIMEOUT, resolve=resolve_public, opener=connect,
             clock=time.monotonic) -> tuple[bytes, str] | None:
    """(the first `limit` bytes of the answer, the address it came from after redirects) - or None: not http(s) on
    port 80 / 443, a name that is not a public host or does not resolve to public addresses only (also after every
    redirect), any answer but 200, too slow, or any network error. Never raises."""
    deadline = clock() + REQUEST_SEC
    try:
        for _ in range(MAX_REDIRECTS + 1):
            parts = urlsplit(url)
            host = (parts.hostname or "").lower().rstrip(".")
            if parts.scheme not in PORTS or parts.username or parts.password or not is_public_host(host):
                return None
            port = parts.port or PORTS[parts.scheme]
            if port != PORTS[parts.scheme]:
                return None
            addresses = resolve(host)
            if not addresses:
                return None
            path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=~-._") + (f"?{parts.query}" if parts.query else "")
            conn = opener(parts.scheme, host, addresses, port, timeout)
            try:
                conn.request("GET", path, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                                                   "Accept": "image/*,text/html;q=0.8,*/*;q=0.5"})
                resp = conn.getresponse()
                if resp.status in REDIRECTS:
                    where = resp.getheader("Location")
                    if not where:
                        return None
                    url = urljoin(url, where.strip())
                    continue
                if resp.status != 200:
                    return None
                body = _read(resp, limit, deadline, clock)
            finally:
                conn.close()
            return None if body is None else (body, url)
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return None


# ---------------------------------------------------------------- reading what came back

def _rank(size: int) -> tuple:
    """Sort key of an icon's side in pixels (0 = not said): the smallest from 64 px up first (it shrinks well), then the
    largest between 32 and 63, then the ones that don't say, then the small ones (largest first)."""
    if size >= OUT_SIZE:
        return 0, size
    if size >= BEST_FROM:
        return 1, -size
    if size == 0:
        return 2, 0
    return 3, -size


def _side(sizes: str | None) -> int:
    """The biggest side in a `sizes` attribute ("16x16 32x32"); 0 when it says nothing usable ("any", missing)."""
    best = 0
    for token in (sizes or "").lower().split():
        w, _, h = token.partition("x")
        if w.isdigit() and h.isdigit():
            best = max(best, min(int(w), int(h)))
    return best


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.found: list[tuple[str, int]] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link" or len(self.found) >= MAX_LINKS:
            return
        a = {k: (v or "") for k, v in attrs}
        rels = set(a.get("rel", "").lower().split())
        href = a.get("href", "").strip()
        if not href or not rels & ICON_RELS or "svg" in a.get("type", "").lower():
            return
        size = _side(a.get("sizes")) or (180 if rels & {"apple-touch-icon", "apple-touch-icon-precomposed"} else 0)
        self.found.append((href, size))


def icon_links(html: str, base: str) -> list[str]:
    """The icon addresses a home page names, best first (see _rank), as absolute http(s) addresses, no repeats, svg left
    out (PIL can't read it)."""
    parser = _Links()
    try:
        parser.feed(html)
    except Exception:
        pass
    ranked = []
    for href, size in parser.found:
        url = urljoin(base, href)
        parts = urlsplit(url)
        if parts.scheme in PORTS and not parts.path.lower().endswith(".svg") and all(url != u for u, _ in ranked):
            ranked.append((url, size))
    ranked.sort(key=lambda c: _rank(c[1]))   # (stable: the page's own order breaks ties)
    return [url for url, _ in ranked]


_pil_lock = threading.Lock()


def to_png(data: bytes) -> bytes | None:
    """A PNG of at most OUT_SIZE px (transparency kept) from picture bytes - or None: not a picture of one of FORMATS, one
    that declares over MAX_PIXELS (it is not decoded at all), fully transparent, or damaged. An .ico with several sizes
    gives its best one (see _rank)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    with _pil_lock:   # (the library's own cap is lowered for the time of this one conversion; its warning is an error)
        cap = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                return _convert(Image, data)
        except Exception:
            return None
        finally:
            Image.MAX_IMAGE_PIXELS = cap


def _convert(Image, data: bytes) -> bytes | None:
    with Image.open(io.BytesIO(data), formats=FORMATS) as im:
        if im.size[0] < 1 or im.size[1] < 1 or im.size[0] * im.size[1] > MAX_PIXELS:
            return None
        if im.format == "ICO":
            sizes = sorted(im.ico.sizes(), key=lambda s: _rank(min(s)))
            if sizes:
                im.size = sizes[0]
        im.load()
        if im.size[0] * im.size[1] > MAX_PIXELS:
            return None
        image = im.convert("RGBA")
    if image.getextrema()[3][1] == 0:
        return None
    if max(image.size) > OUT_SIZE:
        image.thumbnail((OUT_SIZE, OUT_SIZE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------- the chain

def fetch_site_icon(host: str, get=http_get, blocked=hosts_blocked, convert=to_png) -> bytes | None:
    """The icon of a site as PNG bytes, or None (offline, no icon, not a public site). See the top of this file."""
    host = host.lower().rstrip(".")
    if not is_public_host(host):
        return None
    if not blocked(host):                                         # a) the site itself
        base = f"https://{host}/favicon.ico"
        png = _icon_at(base, get, convert)
        if png:
            return png
        page = get(f"https://{host}/")
        if page:
            html = page[0].decode("utf-8", "replace")
            for url in [u for u in icon_links(html, page[1]) if u != base][:MAX_CANDIDATES]:
                png = _icon_at(url, get, convert)
                if png:
                    return png
    return _icon_at(FAVICON_URL.format(domain=host), get, convert)   # b) the service


def _icon_at(url: str, get, convert) -> bytes | None:
    got = get(url)
    return convert(got[0]) if got else None
