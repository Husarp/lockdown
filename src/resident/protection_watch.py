"""Tells the Site protection tab how the list downloads are going (BLUEPRINT 3.3, "Live").

The service downloads the protection lists and writes its progress to the settings (`protection.progress`, and the
"info" of each list when it is done). This worker reads those every second - one tiny setting and one cached fingerprint,
nothing is parsed unless the saved text changed - and pushes `protection_progress {key, text, pct, rev}` when something
changed. The tab patches the one row that is downloading; when `rev` (the fingerprint of what the service wrote) is not
the one it loaded, it reads the lists again. While the window is hidden the worker does nothing at all: the tab is
marked stale and reads fresh when it is shown.
"""
from blocker import protection
from webapi import protection as page_api

from . import workers


class ProtectionWatcher(workers.Worker):
    label = "protection"
    interval = 1.0

    def __init__(self, hub, publish, visible=lambda: True, interval=None):
        super().__init__(hub, interval)
        self.publish, self.visible = publish, visible
        self.last = None

    def tick(self):
        if not self.visible():
            self.last = None             # (shown again: say where things stand once more)
            return
        db = self.db
        payload = page_api.progress_view(protection.download_progress(db), page_api.rev_of(db))
        if payload != self.last:
            self.last = payload
            self.publish("protection_progress", payload)


def attach(pool, publish, visible) -> ProtectionWatcher:
    """Add the watcher to the agent's workers (before pool.start()): it starts and stops with them."""
    watcher = ProtectionWatcher(pool.hub, publish, visible)
    pool.workers.append(watcher)
    return watcher
