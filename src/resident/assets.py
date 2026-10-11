"""The local web server that gives the new window its files: plain HTTP on 127.0.0.1 (never file://), only files
under src/ui/, plus routes other parts add (the app/site icons, /icon/...).

The page is untrusted: no directory listing, no path that leaves src/ui/, and only requests addressed to the loopback
name (so a web page on the internet can't read the files through a rebinding trick)."""
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

# Fixed on purpose: the platform's registry can map .js to text/plain, and module scripts need a JavaScript type.
MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf",
    ".png": "image/png", ".ico": "image/x-icon", ".jpg": "image/jpeg", ".webp": "image/webp",
}
NO_CACHE = "no-cache"
LONG_CACHE = "public, max-age=31536000"


def ui_dir() -> Path:
    """src/ui/ - from the source checkout, or from the PyInstaller bundle."""
    meipass = getattr(sys, "_MEIPASS", "")
    return (Path(meipass) / "ui") if meipass else Path(__file__).resolve().parents[1] / "ui"


class Response:
    """What a route returns: status, content type, body bytes, optional Cache-Control."""
    def __init__(self, body: bytes, content_type: str, status: int = 200, cache: str = NO_CACHE):
        self.body, self.content_type, self.status, self.cache = body, content_type, status, cache


class AssetServer:
    def __init__(self, root: Path | None = None):
        self.root = (root or ui_dir()).resolve()
        self.routes: dict[str, object] = {}   # "/icon/" -> fn(rest: str, query: dict) -> Response | None
        self.httpd: ThreadingHTTPServer | None = None
        self.base_url = ""

    def add_route(self, prefix: str, handler):
        """Serve everything under `prefix` (like "/icon/") with handler(rest, query) -> Response, or None for 404."""
        self.routes[prefix] = handler

    def start(self) -> str:
        """Start on a free loopback port in a background thread; returns the base URL ("http://127.0.0.1:PORT")."""
        server = self
        class Handler(_Handler):
            owner = server
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, name="lockdown-assets", daemon=True).start()
        return self.base_url

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def find(self, url_path: str) -> Path | None:
        """The file for a request path, or None when it isn't a plain file inside the root."""
        text = unquote(url_path)   # (%2e%2e becomes ".." here and is refused below)
        if "\\" in text or "\0" in text or not text.startswith("/"):
            return None
        parts = text.split("/")[1:]
        if parts == [""]:
            parts = ["index.html"]
        if any(p in ("", ".", "..") or ":" in p for p in parts):
            return None
        path = self.root.joinpath(*parts).resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            return None
        return path

    def respond(self, url_path: str, query: dict) -> Response | None:
        for prefix, handler in self.routes.items():
            if url_path.startswith(prefix):
                return handler(unquote(url_path[len(prefix):]), query)
        path = self.find(url_path)
        if path is None:
            return None
        ctype = MIME.get(path.suffix.lower())
        if ctype is None:
            return None
        cache = NO_CACHE if path.suffix.lower() == ".html" else LONG_CACHE
        return Response(path.read_bytes(), ctype, 200, cache)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    owner: AssetServer

    def log_message(self, *args):   # (no console, no log spam)
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        return host in ("127.0.0.1", "localhost")

    def _send(self, response: Response, head: bool):
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("Cache-Control", response.cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(response.body)

    def _serve(self, head: bool):
        if not self._host_ok():
            return self._send(Response(b"forbidden", "text/plain", HTTPStatus.FORBIDDEN), head)
        parsed = urlsplit(self.path)
        try:
            response = self.owner.respond(parsed.path, parse_qs(parsed.query))
        except Exception:   # a broken route must not take the server down
            response = Response(b"error", "text/plain", HTTPStatus.INTERNAL_SERVER_ERROR)
        self._send(response or Response(b"not found", "text/plain", HTTPStatus.NOT_FOUND), head)

    def do_GET(self):
        self._serve(False)

    def do_HEAD(self):
        self._serve(True)

    def _refuse(self):
        self._send(Response(b"", "text/plain", HTTPStatus.METHOD_NOT_ALLOWED), True)

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse
