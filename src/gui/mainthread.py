"""Getting work onto the Tk thread, and garbage collection that stays on it. No Tk import here (unit-tested).

Tk may only be touched from the thread running mainloop. A worker thread calling `widget.after(...)` reaches into
Tcl from the wrong thread, which can raise "main thread is not in main loop", a TclError, or hang / crash Tk
(design/rebuild/inventory-perf.md #8: the About page's update check and download, the hourly GitHub check).
Workers put callables on a CallQueue instead; the window drains it from an after() loop on its own thread.
"""
import gc
import queue
import threading

DRAIN_MS = 150           # how often the window runs what workers handed it (fast enough for a progress bar)
MAX_PER_DRAIN = 200      # (a flood can't hold the window up for long; the rest waits for the next round)

# Garbage collection. Automatic collection is OFF in the window process and stays off: it runs in whichever
# thread happens to allocate, and freeing a Tk image / font from a worker thread hangs Tk (CHANGELOG 0.4.0: "garbage
# collection runs only on the Tk thread"). What changed is how the Tk thread collects. It used to run a FULL
# gc.collect() every 2 s - a stall that grew with every widget, image and stats dict alive. Now everything alive
# once startup is done is frozen (gc.freeze(): never scanned again), and the Tk thread collects the way automatic
# collection would: the young generation when enough new objects have piled up, a middle one now and then, a full
# collection rarely.
GC_MS = 1000
MIDDLE_EVERY = 10        # ticks between generation-1 collections (if there is anything to collect)
FULL_EVERY = 300         # ticks between full collections (5 minutes)


class CallQueue:
    """Thread-safe hand-off to the Tk thread. post() from any thread; drain() only on the Tk thread."""

    def __init__(self):
        self._calls: queue.SimpleQueue = queue.SimpleQueue()
        self._latest: dict = {}              # key -> (fn, args): only the newest is run (progress bars)
        self._lock = threading.Lock()

    def post(self, fn, *args):
        self._calls.put((fn, args))

    def post_latest(self, key, fn, *args):
        """Like post, but if several arrive for the same key before the next drain only the last one runs - a
        download reports progress per chunk, the bar only needs the newest value."""
        with self._lock:
            fresh = key not in self._latest
            self._latest[key] = (fn, args)
        if fresh:
            self._calls.put((self._run_latest, (key,)))

    def _run_latest(self, key):
        with self._lock:
            fn, args = self._latest.pop(key)
        fn(*args)

    def drain(self, limit: int = MAX_PER_DRAIN, on_error=None) -> int:
        """Run what is waiting (at most `limit`). One failing call doesn't stop the others."""
        done = 0
        while done < limit:
            try:
                fn, args = self._calls.get_nowait()
            except queue.Empty:
                break
            done += 1
            try:
                fn(*args)
            except Exception as error:
                if on_error is None:
                    raise
                on_error(error)
        return done


def collection_due(tick: int, count0: int, threshold0: int) -> int | None:
    """Which generation the Tk thread should collect on this tick, or None. tick counts from 1."""
    if tick % FULL_EVERY == 0:
        return 2
    if count0 <= threshold0:
        return None
    return 1 if tick % MIDDLE_EVERY == 0 else 0


def collect(tick: int) -> int | None:
    generation = collection_due(tick, gc.get_count()[0], gc.get_threshold()[0] or 700)
    if generation == 2 and gc.get_freeze_count():
        # The full sweep also looks at what was frozen: a startup object that has become cyclic garbage since (a
        # widget built at prebuild and destroyed later) is freed instead of leaking; what is alive is frozen again.
        gc.unfreeze()
        gc.collect()
        gc.freeze()
    elif generation is not None:
        gc.collect(generation)
    return generation


def freeze_startup():
    """Once the window is built: collect what startup left behind, then move everything still alive into the
    permanent generation, so no later collection walks the thousands of widgets that live as long as the app."""
    gc.collect()
    gc.freeze()
