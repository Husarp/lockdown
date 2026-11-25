"""Trusted-time quota accounting shared by the tray and the service's app backstop."""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from time import monotonic

import emergency
import modes
from blocker import apps
from rules import counted_rules, effective_rules, usage_targets, visit_targets

COUNTED_KEY = "usage.counted_until"
MAX_GAP_SEC = 60
BACKSTOP_STALE_SEC = 10
BACKSTOP_INITIAL_SEC = 2
log = logging.getLogger("lockdown.service")
_logged = {}


def log_accounting(writer, event, target, start, end, details, *args):
    key = writer, event
    now = monotonic()
    if now - _logged.get(key, float("-inf")) >= 60:
        _logged[key] = now
        log.info("Quota writer=%s event=%s target=%s interval=[%s,%s) " + details,
                 writer, event, target, start, end, *args)


@dataclass(frozen=True)
class Contributor:
    item: dict
    source: int | None

    def scopes(self, groups):
        # A member unlock cannot lift a permanent category. Disabled rules still collect ordinary usage.
        if any(rule["rule_type"] == "permanent" for rule in effective_rules(self.item, groups)):
            return ()
        return self.item["id"], self.source


def category_contributors(items, sources):
    """Expand category rules without losing the member that is on screen or running."""
    for item in items:
        if item["item_type"] == "category":
            categories = modes.category_keys({item["target"]})
            for source in {source for cat, source in sources if cat in categories}:
                yield Contributor(item, source)


def payable(start, end, excluded):
    """Subtract possibly overlapping half-open intervals."""
    if not excluded:
        if start < end:
            yield start, end
        return
    for lo, hi in sorted(excluded):
        if hi <= start:
            continue
        if lo >= end:
            break
        if lo > start:
            yield start, lo
        start = max(start, hi)
    if start < end:
        yield start, end


class Exemptions:
    def __init__(self, db, start, end):
        self.pauses = tuple(db.quota_pauses(start, end))
        self.by_item = {}
        for use in db.unlocks_since(start):
            lo, hi = use["started"], use["until"]
            if lo <= end and timedelta(0) < hi - lo <= timedelta(minutes=emergency.MAX_MINUTES):
                for item in use["item_ids"]:
                    self.by_item.setdefault(item, []).append((lo, hi))

    def for_scopes(self, scopes):
        return self.pauses + tuple(span for item in set(scopes) for span in self.by_item.get(item, ()))

    def allows_charge(self, scopes, at):
        return not any(lo <= at < hi for lo, hi in self.for_scopes(scopes))


def segments(start, end, clock):
    """All bucket/schedule transitions are minute-aligned, except explicit reset holds."""
    holds = [clock.carry_start, clock.carry_until]
    for _key, until, since in clock.holds.values():
        holds.extend((since, until))
    edges = {start, end, *(point for point in holds if point and start < point < end)}
    minute = start.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while minute < end:
        edges.add(minute)
        minute += timedelta(minutes=1)
    ordered = sorted(edges)
    return zip(ordered, ordered[1:])


def time_spans(contributors, groups, exemptions, start, end, clock, writer):
    """Union the paid portions per counter even when several contributors feed a shared limit."""
    targets = {}
    for contributor in contributors:
        item = contributor.item
        excluded = exemptions.for_scopes(contributor.scopes(groups))
        paid = list(payable(start, end, excluded))
        if not paid or sum((hi - lo for lo, hi in paid), timedelta()) < end - start:
            paid_sec = sum((hi - lo).total_seconds() for lo, hi in paid)
            log_accounting(writer, "excluded", f"item:{item['id']}", start, end,
                           "paid_seconds=%.6f exempt_seconds=%.6f", paid_sec,
                           (end - start).total_seconds() - paid_sec)
        rules = counted_rules(item, groups)
        for lo, hi in paid:
            for a, b in segments(lo, hi, clock):
                for target in usage_targets(rules, item["id"], a, clock):
                    targets.setdefault(target, []).append((a, b))
    out = []
    for target, spans in targets.items():
        ordered = sorted(spans)
        lo, hi = ordered[0]
        for a, b in ordered[1:]:
            if a > hi:
                out.append((target, lo, hi))
                lo, hi = a, b
            else:
                lo, hi = min(lo, a), max(hi, b)
        out.append((target, lo, hi))
    if start < end:
        period_keys = [(clock.period(period, start)[0], clock.period(period, end)[0])
                       for period in ("day", "week", "month")]
        if changed := [(old, new) for old, new in period_keys if old != new]:
            log_accounting(writer, "reset", next(iter(targets), None), start, end, "limit_keys=%s", changed)
    return out


def mark_value(raw):
    try:
        mark = float(raw or 0)
        return mark if isfinite(mark) else 0.0
    except ValueError:
        return 0.0


def count_unwatched_apps(enforcer, now):
    """Charge running apps and categories only when the tray's counted-through mark is stale."""
    from monitor.usage import UsageTracker

    db = enforcer.db
    expected = db.get_setting(COUNTED_KEY) or ""
    mark = mark_value(expected)
    gap = now.timestamp() - mark
    owns_mark = enforcer.counting and mark == enforcer.count_mark
    if mark and (gap <= 0 or (not owns_mark and gap <= BACKSTOP_STALE_SEC)):
        if enforcer.counting and not owns_mark:
            log.info("The tray app is counting usage again")
            enforcer.counting = False
            enforcer.count_running = None
        return
    start = now - timedelta(seconds=min(gap, MAX_GAP_SEC) if mark else BACKSTOP_INITIAL_SEC)
    procs = apps.list_processes_full()
    alive = {pid for pid, _ppid, _name in procs}
    enforcer.count_paths = {pid: value for pid, value in enforcer.count_paths.items() if pid in alive}
    running = {name for _pid, _ppid, name in procs}
    running |= {file for pid, file in enforcer.hosted.items() if pid in alive}
    launched = running - enforcer.count_running if owns_mark and enforcer.count_running is not None else set()
    members = {exe for pid, exes in enforcer.members.items() if pid in alive for exe in exes}
    groups, clock, categories = db.list_groups(), db.limit_clock(), db.categories()
    items = db.list_items()
    exemptions = Exemptions(db, start, now)
    used, opening_targets, category_sources = [], [], []
    matched = set()
    for item in items:
        if item["item_type"] != "app":
            continue
        contributor = Contributor(item, item["id"])
        names = apps.names_of(item["target"])
        if names & launched and exemptions.allows_charge(contributor.scopes(groups), now):
            targets = visit_targets(counted_rules(item, groups), item, now, True, None, clock)
            opening_targets.extend(UsageTracker.unless_closed(db, item, groups, targets, now, clock))
        if apps.exe_name(item["target"]) in members or enforcer._running(item, running, procs):
            used.append(contributor)
            cat = modes.item_category(item, categories)
            category_sources.append((cat, item["id"]))
            main_exe = apps.exe_name(item["target"])
            matched_names = names & running
            matched_names |= {name for pid, _ppid, name in procs
                              if main_exe in enforcer.members.get(pid, ())}
            if folder := apps.app_folder(item):
                matched_names |= {name for name, path in enforcer.count_paths.values()
                                  if name not in apps.PROTECTED and apps.in_folder(path, folder)}
            for name in matched_names:
                if assigned := categories.get(("app", name)):
                    category_sources.append((assigned, item["id"]))
            matched |= matched_names
    for (kind, name), cat in categories.items():
        if kind == "app" and name in running and name not in matched:
            category_sources.append((cat, None))
    used.extend(category_contributors(items, category_sources))
    spans = time_spans(used, groups, exemptions, start, now, clock, "service")
    openings = [(*target, now.date(), 1) for target in opening_targets]
    if not db.commit_quota(COUNTED_KEY, now, spans, openings, service_mark=expected):
        log_accounting("service", "handoff", None, start, now, "commit=retry")
        return
    if not enforcer.counting:
        log.warning("The tray app isn't counting usage - the service counts running blocked apps instead")
    enforcer.counting = True
    enforcer.count_running = running
    enforcer.count_mark = now.timestamp()
