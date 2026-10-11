"""R16: the Network Log page (js/pages/netlog.js) in Chrome against the REAL Python Api (webapi/network.py) on a temporary
database (see harness.py).

What is checked: the table (columns, summary line, Show more), the filters (status, app, Windows' own, Local network),
the search (one rpc, 120 ms after the last key), Table / Graph (60 bars, top 8), the row menu (block site / block app open
Blocking > Add, copy, show only), Export CSV, live rows (merged in place, the scroll position stays), 10 000 rows scroll
smoothly with few row nodes, and that nothing is asked while the page is away.

What can only be proven on Windows: the page inside WebView2 (this is Chrome), the native Save dialog, real icons, and
the NetLogWorker thread started by the agent."""
import csv
import os
from datetime import timedelta

from harness import NOW

LIVE = "#nl-list > .vl-row:not([hidden])"
BY_PLACE = "els => els.sort((a, b) => a._i - b._i)"
SUMMARY = '[data-page="netlog"] [data-k="summary"]'
MORE = '[data-k="more"]'


def minute_of(back):
    return (NOW.replace(second=0) - timedelta(minutes=back)).strftime("%Y-%m-%d %H:%M")


def fill(harness, n=300, offset=0):
    """n connection rows in the last hour: 4 apps (svchost.exe is Windows' own), 5 sites (one with no name), and every
    50th one on the local network."""
    apps = ["chrome.exe", "discord.exe", "steam.exe", "svchost.exe"]
    sites = ["youtube.com", "rr3.googlevideo.com", "github.com", "reddit.com", ""]
    harness.db.add_network([
        {"minute": minute_of(i % 60), "exe": apps[i % 4], "ip": f"10.{offset}.{i // 250}.{i % 250}", "port": 443,
         "domain": sites[i % 5], "count": 1 + i % 4, "windows": int(i % 4 == 3), "local": int(i % 50 == 49)}
        for i in range(n)])


def block_event(harness, host, reason="permanent", seconds_ago=10):
    harness.db.add_block_event(host, 1, host, reason, None, NOW - timedelta(seconds=seconds_ago))


def open_log(shell, page):
    shell(route="netlog")
    page.wait_for_selector(LIVE, timeout=8000)


def column(page, field):
    """What the rows in view say in one column, top to bottom."""
    return page.eval_on_selector_all(LIVE, f"els => ({BY_PLACE})(els).map(e => e.querySelector('[data-r={field}]').textContent)")


def settle(page, ms=400):
    page.wait_for_timeout(ms)


def app_options(page):
    return page.locator('[data-k="app"] option').all_inner_texts()


# ---------------------------------------------------------------- the table

def test_the_table_shows_the_newest_rows_and_the_summary(harness, shell, page):
    fill(harness, 300)
    block_event(harness, "reddit.com")
    open_log(shell, page)
    assert [t.upper() for t in page.locator(".nl-head .cap").all_inner_texts()] == ["APP", "TIME", "SITE", "RULE", "PORT", "STATUS"]
    shown = page.eval_on_selector_all(LIVE, f"els => ({BY_PLACE})(els).map(e => e.innerText)")
    assert "✓ Allowed" in shown[0] and "443" in shown[0] and "–" in shown[0] and "Chrome" in shown[0]
    blocked = [t for t in shown if "Blocked visit" in t]
    assert len(blocked) == 1 and "reddit.com" in blocked[0] and "Permanently blocked" in blocked[0] and "✗ Blocked" in blocked[0]
    text = page.inner_text(SUMMARY)
    assert text.startswith("Last hour: ") and " connections · 1 blocked · " in text and " sites · 3 apps · top: " in text
    assert page.locator(MORE).inner_text().startswith("Show more (")
    assert page.locator(f"{LIVE}.blk").count() == 1                       # the blocked visit is tinted
    assert page.locator(LIVE).count() <= 40                              # a virtual list: only what is in view


def test_windows_and_local_rows_are_hidden_until_their_switches_are_on(harness, shell, page):
    fill(harness, 300)
    open_log(shell, page)
    assert "Svchost" not in app_options(page) and "Chrome" in app_options(page)
    page.locator('[data-k="windows"]').check(force=True)
    page.wait_for_function("[...document.querySelectorAll('[data-k=\"app\"] option')].some(o => o.textContent === 'Svchost')")
    page.locator('[data-k="windows"]').uncheck(force=True)
    page.wait_for_function("![...document.querySelectorAll('[data-k=\"app\"] option')].some(o => o.textContent === 'Svchost')")
    before = page.inner_text(SUMMARY)
    page.locator('[data-k="local"]').check(force=True)
    page.wait_for_function("t => document.querySelector('[data-page=\"netlog\"] [data-k=\"summary\"]').textContent !== t", arg=before)


def test_status_filter_and_show_more(harness, shell, page):
    fill(harness, 300)
    block_event(harness, "reddit.com")
    block_event(harness, "x.com", "schedule", 20)
    open_log(shell, page)
    page.click('[data-seg="nlstatus"] [data-v="blocked"]')
    page.wait_for_function("document.querySelectorAll('#nl-list > .vl-row:not([hidden])').length === 2")
    assert page.locator(f"{LIVE}.blk").count() == 2 and page.locator(MORE).is_hidden()
    assert sorted(column(page, "rule")) == ["Outside its allowed hours", "Permanently blocked"]
    page.click('[data-seg="nlstatus"] [data-v="allowed"]')
    page.wait_for_function("!document.querySelector('#nl-list .vl-row:not([hidden]).blk')")
    assert "Blocked visit" not in column(page, "app")
    older = int(page.locator(MORE).inner_text().split("(")[1].split(" ")[0])
    page.click(MORE)                                                      # (the list doubles: 100 -> 200)
    page.wait_for_function("t => !document.querySelector('[data-k=\"more\"]').textContent.includes('(' + t + ' older')", arg=str(older))
    page.click('[data-seg="nlstatus"] [data-v="all"]')                   # a filter change starts from 100 rows again
    settle(page)
    assert harness.calls[-1][1] == "network_get" and harness.calls[-1][2][1] == 100


def test_an_empty_log_says_so(harness, shell, page):
    shell(route="netlog")
    page.wait_for_selector('[data-k="empty"]:not([hidden])', timeout=8000)
    assert "No connections in the last hour yet." in page.inner_text('[data-k="hint"]')
    assert "No connections yet" in page.inner_text('[data-k="empty"]')


# ---------------------------------------------------------------- the search

def test_typing_in_search_makes_one_rpc_after_120_ms(harness, shell, page):
    fill(harness, 300)
    open_log(shell, page)
    start = harness.rpc_count("network_get")
    page.click('[data-k="search"]')
    page.keyboard.type("gith", delay=20)
    assert harness.rpc_count("network_get") == start                    # (the last key was under 120 ms ago)
    settle(page, 500)
    assert harness.rpc_count("network_get") == start + 1
    assert set(column(page, "site")) <= {"github.com"} | {s for s in column(page, "site") if s.startswith("github.com")}
    assert column(page, "site") and all(s.startswith("github.com") for s in column(page, "site"))


def test_the_search_forgives_typos_and_finds_the_app_name(harness, shell, page):
    fill(harness, 300)
    open_log(shell, page)
    page.fill('[data-k="search"]', "yotube")                           # a typo
    settle(page, 500)
    assert column(page, "site") and all(s.startswith("youtube.com") for s in column(page, "site"))
    page.fill('[data-k="search"]', "discord")                          # the app's name
    settle(page, 500)
    assert set(column(page, "app")) == {"Discord"}
    page.fill('[data-k="search"]', "zzzzzzqq")
    settle(page, 500)
    assert page.locator(LIVE).count() == 0 and "Nothing matches" in page.inner_text('[data-k="empty"]')


# ---------------------------------------------------------------- Table / Graph

def test_the_graph_has_60_bars_and_the_top_lists(harness, shell, page):
    fill(harness, 300)
    block_event(harness, "reddit.com", seconds_ago=400)
    open_log(shell, page)
    page.click('[data-seg="nlview"] [data-v="graph"]')
    page.wait_for_selector(".nl-graph svg path.ch-bar", state="attached", timeout=8000)
    assert page.locator(".nl-graph svg path.ch-bar").count() == 60
    assert page.locator(".nl-graph svg path.ch-bar.block").count() == 1   # the minute with a blocked visit is red
    labels = [t for t in page.eval_on_selector_all(".nl-graph svg text", "els => els.map(e => e.textContent)") if t]
    assert labels and all(t.endswith("0") for t in labels)               # a label every 10 minutes
    assert page.locator('[data-k="topapps"] .mini-row:not([hidden])').count() == 3
    assert page.locator('[data-k="topsites"] .mini-row:not([hidden])').count() == 4
    assert page.locator('[data-k="tabtable"]').is_hidden()
    page.click('#nav [data-nav="dashboard"]')                            # away and back: the Table again
    page.wait_for_selector('[data-page="dashboard"]:not([hidden])')
    page.click('#nav [data-nav="netlog"]')
    page.wait_for_selector('[data-k="tabtable"]:not([hidden])')
    assert page.locator('[data-seg="nlview"] [data-v="table"]').get_attribute("aria-pressed") == "true"


def test_the_bar_of_this_minute_stays_the_accent_colour_even_with_a_blocked_visit(harness, shell, page):
    fill(harness, 60)
    block_event(harness, "reddit.com", seconds_ago=10)                   # (this very minute)
    block_event(harness, "youtube.com", seconds_ago=600)                 # (ten minutes ago)
    open_log(shell, page)
    page.click('[data-seg="nlview"] [data-v="graph"]')
    page.wait_for_selector(".nl-graph svg path.ch-bar", state="attached", timeout=8000)
    bars = page.locator(".nl-graph svg path.ch-bar")
    assert bars.count() == 60 and "now" in bars.nth(59).get_attribute("class").split()
    assert "block" not in bars.nth(59).get_attribute("class").split()    # today wins over the red
    assert page.locator(".nl-graph svg path.ch-bar.block").count() == 1 and page.locator(".nl-graph svg path.ch-bar.now").count() == 1


# ---------------------------------------------------------------- the row menu

def click_row(page, text, right=False):
    """Click the topmost row that says `text` (from the top of the list: a click that has to scroll first would have its menu
    closed by the scroll event that follows)."""
    page.eval_on_selector("#nl-list", "e => { e.scrollTop = 0; }")
    settle(page, 200)
    row = page.evaluate_handle(f"""(() => [...document.querySelectorAll({LIVE!r})]
        .filter((e) => e.innerText.includes({text!r})).sort((a, b) => a._i - b._i)[0])()""").as_element()
    row.click(button="right" if right else "left")
    page.wait_for_selector("#menu:not([hidden]) button")


def menu_items(page):
    return page.locator("#menu button").all_inner_texts()


def test_the_row_menu_has_the_old_items(harness, shell, page):
    fill(harness, 300)
    open_log(shell, page)
    click_row(page, "rr3.googlevideo.com")
    items = menu_items(page)
    assert "Block site googlevideo.com…" in items and "Block app Discord…" in items
    assert "Copy site" in items and "Copy app name" in items and "Show only Discord" in items
    page.keyboard.press("Escape")
    page.wait_for_selector("#menu", state="hidden")
    page.locator('[data-k="windows"]').check(force=True)
    page.wait_for_function("[...document.querySelectorAll('[data-k=\"app\"] option')].some(o => o.textContent === 'Svchost')")
    page.fill('[data-k="search"]', "svchost youtube")                     # (rows that have a site name: some have only an address)
    page.wait_for_function(f"""(() => {{ const rows = [...document.querySelectorAll({LIVE!r})];
        return rows.length > 0 && rows.every((r) => r.innerText.includes('Svchost') && r.innerText.includes('youtube.com')); }})()""")
    settle(page, 300)                                                    # (the list's own scroll events - they close a menu - have passed)
    click_row(page, "Svchost")
    assert not any(i.startswith("Block app") for i in menu_items(page))   # Windows' own can't be blocked from here
    assert any(i.startswith("Block site") for i in menu_items(page))


def test_block_site_opens_blocking_add(harness, shell, page):
    fill(harness, 300)
    open_log(shell, page)
    click_row(page, "rr3.googlevideo.com")
    page.get_by_role("menuitem", name="Block site googlevideo.com…").click()
    page.wait_for_selector('[data-page="blocking"]:not([hidden])', timeout=8000)
    assert page.evaluate("location.hash") == "#blocking"
    call = [c for c in harness.calls if c[1] == "network_block_target"]
    assert len(call) == 1 and call[0][2] == ["site", "rr3.googlevideo.com"]


def test_block_app_asks_python_for_the_target(harness, shell, page):
    fill(harness, 300)
    open_log(shell, page)
    click_row(page, "Discord", right=True)
    page.get_by_role("menuitem", name="Block app Discord…").click()
    page.wait_for_selector('[data-page="blocking"]:not([hidden])', timeout=8000)
    assert [c[2] for c in harness.calls if c[1] == "network_block_target"] == [["app", "discord.exe"]]


def test_copy_site_and_show_only_the_app(harness, shell, page):
    fill(harness, 300)
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    open_log(shell, page)
    click_row(page, "github.com")
    page.get_by_role("menuitem", name="Copy site").click()
    assert page.evaluate("navigator.clipboard.readText()") == "github.com"
    click_row(page, "github.com")
    page.get_by_role("menuitem", name="Copy app name").click()
    assert page.evaluate("navigator.clipboard.readText()") == "Steam"
    click_row(page, "github.com")
    page.get_by_role("menuitem", name="Show only Steam").click()
    settle(page)
    assert set(column(page, "app")) == {"Steam"}
    assert page.locator('[data-k="app"]').evaluate("e => e.options[e.selectedIndex].textContent") == "Steam"
    click_row(page, "github.com")
    assert "Show all apps" in menu_items(page)
    page.get_by_role("menuitem", name="Show all apps").click()
    settle(page)
    assert len(set(column(page, "app"))) > 1


# ---------------------------------------------------------------- Export CSV

def test_export_csv_writes_the_filtered_rows(harness, shell, page, tmp_path, monkeypatch):
    from webapi import network
    fill(harness, 300)
    block_event(harness, "reddit.com")
    target = tmp_path / "log.csv"
    monkeypatch.setattr(network, "_ask_save_path", lambda ctx: str(target))
    monkeypatch.setattr(network, "_spawn", lambda fn: fn())
    sent = []
    harness.ctx.publish = lambda event, payload=None, key=None: sent.append((event, payload))
    open_log(shell, page)
    page.click('[data-seg="nlstatus"] [data-v="blocked"]')
    settle(page, 300)
    page.click('[data-k="export"]')
    page.wait_for_function("[...document.querySelectorAll('body *')].some(e => e.children.length === 0 && e.textContent.startsWith('Saving 1 rows'))")
    rows = list(csv.reader(open(target, encoding="utf-8", newline="")))
    assert rows[0] == ["time", "app", "exe", "site", "ip", "port", "connections", "status"]
    assert len(rows) == 2 and rows[1][1:] == ["Browser", "", "reddit.com", "", "", "1", "blocked"]
    assert [e for e, _ in sent] == ["net_export"] and sent[0][1]["ok"] and sent[0][1]["rows"] == 1


# ---------------------------------------------------------------- live rows

def collect_pushes(harness):
    sent = []
    harness.ctx.publish = lambda event, payload=None, key=None: sent.append((event, payload))
    return sent


def tick(harness):
    """One round of the NetLogWorker's job, without waiting for its 3 seconds."""
    from webapi import network
    network._log(harness.ctx).fetched = 0.0
    return network._feed(harness.ctx).tick()


def test_live_rows_are_merged_at_the_top_without_a_reload(harness, shell, page, push):
    fill(harness, 120)
    sent = collect_pushes(harness)
    open_log(shell, page)
    top = column(page, "site")[:3]
    harness.db.add_network([{"minute": minute_of(0), "exe": "chrome.exe", "ip": "9.9.9.9", "port": 443, "domain": "live-news.org",
                             "count": 7, "windows": 0, "local": 0}])
    assert tick(harness)
    assert sent[-1][0] == "net_rows" and [r["site"] for r in sent[-1][1]["rows"]] == ["live-news.org"]
    asked = harness.rpc_count()
    push("net_rows", sent[-1][1])
    page.wait_for_function("[...document.querySelectorAll('#nl-list .vl-row:not([hidden]) [data-r=site]')].some(e => e.textContent === 'live-news.org')")
    assert column(page, "site")[0] == "live-news.org" and column(page, "site")[1:4] == top
    assert harness.rpc_count() == asked                                  # no reload, no question to Python


def test_a_live_push_of_50_rows_keeps_the_scroll_position(harness, shell, page, push):
    fill(harness, 600)
    sent = collect_pushes(harness)
    open_log(shell, page)
    page.click(MORE)                                                      # 200 rows loaded
    page.wait_for_function("document.querySelector('[data-k=\"more\"]').textContent.includes('(')  && document.querySelector('#nl-list').scrollHeight > 200 * 30")
    row_h = page.evaluate("document.querySelector('#nl-list .vl-row').offsetHeight")
    page.eval_on_selector("#nl-list", f"e => {{ e.scrollTop = {row_h * 40}; }}")
    settle(page, 250)
    in_view = f"els => ({BY_PLACE})(els).filter(e => e.getBoundingClientRect().top >= document.querySelector('#nl-list').getBoundingClientRect().top).slice(0, 3).map(e => e.innerText)"
    before = page.eval_on_selector_all(LIVE, in_view)
    scrolled = page.eval_on_selector("#nl-list", "e => e.scrollTop")
    harness.db.add_network([{"minute": minute_of(0), "exe": "chrome.exe", "ip": f"7.7.{i // 250}.{i % 250}", "port": 80,
                             "domain": f"fresh{i}.example", "count": 1, "windows": 0, "local": 0} for i in range(50)])
    assert tick(harness)
    payload = sent[-1][1]
    assert len(payload["rows"]) == 50
    push("net_rows", payload)
    page.wait_for_function("document.querySelector('#nl-list').scrollHeight > " + str((200 + 50) * row_h - 5))
    settle(page, 250)
    assert page.eval_on_selector_all(LIVE, in_view) == before            # the same rows are still in view
    assert page.eval_on_selector("#nl-list", "e => e.scrollTop") == scrolled + 50 * row_h
    page.eval_on_selector("#nl-list", f"e => {{ e.scrollTop = {12 * row_h}; }}")      # (10 busier rows of this minute come first)
    settle(page, 250)
    assert sum(s.startswith("fresh") for s in column(page, "site")) >= 20
    assert page.locator(LIVE).count() <= 80


def test_a_push_for_an_older_question_is_ignored(harness, shell, page, push):
    fill(harness, 120)
    open_log(shell, page)
    before = column(page, "site")
    row = {"k": "x", "m": minute_of(0), "t": "15:42", "exe": "a.exe", "app": "A", "host": "x.com", "site": "x.com", "n": 1,
           "rule": "–", "port": 1, "b": 0, "w": 0, "l": 0, "i": 0, "reg": "x.com"}
    push("net_rows", {"epoch": 999, "seq": 99, "rows": [row], "total": 999, "summary": {"text": "WRONG"}, "apps": [], "cutoff": ""})
    settle(page, 200)
    assert column(page, "site") == before and "WRONG" not in page.inner_text(SUMMARY)


def test_live_switch_and_leaving_the_page_stop_the_feed(harness, shell, page):
    from webapi import network
    fill(harness, 120)
    open_log(shell, page)
    assert network._feed(harness.ctx).on
    page.locator('[data-k="live"]').uncheck(force=True)
    settle(page, 300)
    assert not network._feed(harness.ctx).on
    page.locator('[data-k="live"]').check(force=True)
    settle(page, 300)
    assert network._feed(harness.ctx).on
    page.click('#nav [data-nav="dashboard"]')
    settle(page, 300)
    assert not network._feed(harness.ctx).on                             # (a page that is not shown asks for nothing)
    page.click('#nav [data-nav="netlog"]')
    settle(page, 400)
    assert network._feed(harness.ctx).on


# ---------------------------------------------------------------- the numbers

def test_10000_rows_scroll_without_slow_frames(harness, shell, page, perf):
    harness.db.conn.executemany(
        "INSERT OR IGNORE INTO network_log (minute, exe, ip, port, domain, count, windows, local) VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
        [(minute_of(i % 60), ("chrome.exe", "discord.exe", "steam.exe")[i % 3], f"10.{i // 65536}.{i // 256 % 256}.{i % 256}", 443,
          ("youtube.com", "github.com", "reddit.com", "x.com")[i % 4], 1 + i % 5) for i in range(10_000)])
    harness.db.conn.commit()
    open_log(shell, page)
    for _ in range(12):                                                  # each click doubles what is loaded
        if page.locator(MORE).is_hidden():
            break
        older = page.locator(MORE).inner_text()
        page.click(MORE)
        page.wait_for_function("t => document.querySelector('[data-k=\"more\"]').hidden || document.querySelector('[data-k=\"more\"]').textContent !== t",
                               arg=older, timeout=15000)
    assert page.locator(MORE).is_hidden()
    assert page.evaluate("document.querySelector('#nl-list').scrollHeight") > 10_000 * 20
    runs = []
    for _ in range(3):                                                   # (this box runs many jobs at once: the best of 3 runs counts)
        page.eval_on_selector("#nl-list", "e => { e.scrollTop = 0; }")
        runs.append(perf.scroll_frames("#nl-list", seconds=3.0, probe=LIVE))
    stats = min(runs, key=lambda r: r["p95_ms"])
    print("netlog 10 000 rows scroll:", stats)
    slack = 2 if os.getloadavg()[0] > (os.cpu_count() or 1) else 1       # (a machine busier than it has cores gets twice the time)
    assert stats["p95_ms"] <= 50 * slack and stats["max_ms"] <= 100 * slack, runs     # (the budget: no frame over 50 ms; one stall is allowed)
    assert max(r["live_max"] for r in runs) <= 80, runs
    assert page.evaluate("document.querySelectorAll('#nl-list .vl-row').length") <= 80


def test_nothing_is_asked_while_the_page_is_away_or_hidden(harness, shell, page, perf):
    fill(harness, 120)
    open_log(shell, page)
    page.click('#nav [data-nav="dashboard"]')
    page.wait_for_selector('[data-page="dashboard"]:not([hidden])')
    settle(page, 300)
    start = harness.rpc_count("network_get") + harness.rpc_count("network_graph")
    perf.set_hidden(True)
    page.wait_for_timeout(1500)
    assert harness.rpc_count("network_get") + harness.rpc_count("network_graph") == start


def test_scrolling_near_the_end_loads_the_next_batch(harness, shell, page):
    fill(harness, 600)
    open_log(shell, page)
    assert page.evaluate("document.querySelector('#nl-list').scrollHeight") < 150 * 40
    asked = harness.rpc_count("network_get")
    page.eval_on_selector("#nl-list", "e => { e.scrollTop = e.scrollHeight; }")
    page.wait_for_function("document.querySelector('#nl-list').scrollHeight > 190 * 30")
    assert harness.rpc_count("network_get") == asked + 1 and harness.calls[-1][2][1] == 200
