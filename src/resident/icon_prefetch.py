"""Real icons for the owner's own sites, got ahead of time (the Settings switch "Download icons for my sites" is the
gate: off = nothing is queued).

`my_site_hosts(db)` lists the sites the pages show an icon for on the owner's lists - the first host of every blocked
site (that is the one the page draws; groups hold the same items), the sites a mode adds by hand, the protection
"allowed anyway" sites and the Blocked-words exceptions that are sites. A `Prefetcher` queues them on the IconService
(which skips what has an icon already, what Lockdown brings with it, and what failed a moment ago) at agent start - a few
seconds after the window is up, like the app catalog - and again when the lists change (`blocks_changed`, `mode_changed`):
the publish call only sets a flag, ONE thread of its own then waits a moment (a burst of edits is one scan), reads the
lists and queues the sites. The downloading itself is the IconService's (two at a time, a pause between)."""
import threading
import time

START_DELAY = 8.0        # seconds after the window is up before the first scan (the page's own icons go first)
DEBOUNCE = 1.5           # seconds to wait after a change before reading the lists (edits come in bursts)
MAX_HOSTS = 400
WATCHED = frozenset({"blocks_changed", "mode_changed"})   # the publish events that mean "the lists changed"


def my_site_hosts(db, limit: int = MAX_HOSTS) -> list[str]:
    """The sites of the owner's lists, lower case, no repeats, at most `limit`."""
    import keywords
    import modes
    from blocker import protection
    found: dict[str, None] = {}

    def add(host):
        host = str(host).strip().lower().rstrip(".")
        if host and "." in host and len(found) < limit:
            found.setdefault(host)

    for item in db.list_items():
        if item["item_type"] == "site":
            hosts = item["target"].split()
            if hosts:
                add(hosts[0])
    for mode in modes.load(db):
        for extra in mode.get("extra", []):
            if extra.get("kind") == "site" and extra.get("targets"):
                add(extra["targets"][0])
    for host in protection.settings(db).get("allowed", []):
        add(host)
    for host in keywords.settings_shared(db).get("exceptions", []):
        add(host)
    return list(found)


class Prefetcher:
    """Reads the lists and queues their sites on `service`. `collect(db)` -> hosts can be replaced (tests); with
    `start=False` nothing runs by itself: call `scan()`."""

    def __init__(self, ctx, service, collect=my_site_hosts, delay: float = START_DELAY, debounce: float = DEBOUNCE,
                 sleep=None):
        self.ctx, self.service, self.collect = ctx, service, collect
        self.delay, self.debounce = delay, debounce
        self.sleep = sleep or time.sleep
        self._flag = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        """The first scan in `delay` seconds, then one after every poke. Once."""
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="lockdown-icon-prefetch", daemon=True)
            self._thread.start()

    def poke(self):
        """The lists changed (cheap: just a flag - this runs inside the caller's edit)."""
        self._flag.set()

    def setting_changed(self, on: bool):
        """The switch was flipped (or a backup restored): off stops new downloads at once, on scans again."""
        self.service.download = bool(on)
        if on:
            self.poke()

    def scan(self) -> int:
        """Read the setting and the lists; queue the sites. -> how many were queued."""
        try:
            from resident import icons
            db = self.ctx.db
            self.service.download = icons.download_on(db)
            return self.service.prefetch(self.collect(db)) if self.service.download else 0
        except Exception:   # (a damaged setting or list is not worth a crash: the page still asks for icons one by one)
            return 0

    def _run(self):
        self.sleep(self.delay)
        self.scan()
        while True:
            self._flag.wait()
            self.sleep(self.debounce)
            self._flag.clear()
            self.scan()


def attach(ctx, service, **kw) -> Prefetcher:
    """Give the Context what the Api needs (`ctx.icons`, `ctx.icon_prefetch`) and watch `ctx.publish` for list changes.
    Call it after ctx.publish is set; `.start()` it once the window is up."""
    prefetcher = Prefetcher(ctx, service, **kw)
    ctx.icons, ctx.icon_prefetch = service, prefetcher
    inner = getattr(ctx, "publish", None)
    if inner is not None:
        def publish(event, *args, **kwargs):
            try:
                return inner(event, *args, **kwargs)
            finally:
                if event in WATCHED:
                    prefetcher.poke()
        ctx.publish = publish
    return prefetcher
