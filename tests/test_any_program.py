"""Block any program file, not only an .exe: a Java game (javaw.exe -jar game.jar), a script (python, cmd, PowerShell,
Windows Script Host) and a shortcut to one. The process is the interpreter, which other programs use too, so the item
("game.jar") is the interpreter process whose command line names that file - other Java / Python programs are not
touched. Closed, minimised, counted and limited like any app; never cut off the internet (that would be javaw.exe's
for every Java program)."""
import threading

import pytest

import alerts
import service
from blocker import apps
from monitor import applist
from monitor import usage as usage_mod
from test_app_limit_enforcement import FRIDAY, World, at, block_during, world  # noqa: F401

JAVAW = r"C:\Program Files\Java\jre-1.8\bin\javaw.exe"
PYTHON = r"C:\Python312\python.exe"
CMD = r"C:\WINDOWS\system32\cmd.exe"


# ---------------------------------------------------------------- reading a command line

@pytest.mark.parametrize("cmd, args", [
    (r'"C:\Program Files\Java\bin\javaw.exe" -jar "C:\My Games\game.jar"',
     [r"C:\Program Files\Java\bin\javaw.exe", "-jar", r"C:\My Games\game.jar"]),
    (r"java.exe -Xmx2G -jar game.jar nogui", ["java.exe", "-Xmx2G", "-jar", "game.jar", "nogui"]),
    (r'x.exe "a ""quoted"" word" b', ["x.exe", 'a "quoted" word', "b"]),
    (r'x.exe a\\\"b c\\"d e"', ["x.exe", 'a\\"b', "c\\d e"]),            # 2n+1 / 2n backslashes before a quote
    (r'x.exe C:\dir\ "C:\with space\\"', ["x.exe", "C:\\dir\\", "C:\\with space\\"]),
    ("   x.exe    a\tb  ", ["x.exe", "a", "b"]),
    ("", []),
])
def test_split_command_line(cmd, args):
    assert apps.split_command_line(cmd) == args


@pytest.mark.parametrize("name, cmd, found", [
    # Java: the file after -jar, whatever options come before; -cp with a main class is not a file
    ("javaw.exe", rf'"{JAVAW}" -jar "C:\Games\My Game\game.jar"', r"C:\Games\My Game\game.jar"),
    ("java.exe", "java -Xmx2G -Dx=1 -jar server.jar nogui", "server.jar"),
    ("javaw.exe", "javaw -JAR C:/Games/game.jar", r"C:\Games\game.jar"),
    ("javaw.exe", r"javaw -cp lib\a.jar;game.jar com.game.Main", None),
    ("javaw.exe", "javaw -jar", None),
    ("javaw.exe", "javaw -cp lib Main -jar game.jar", None),       # after the main class: the program's own argument
    ("javaw.exe", r"javaw -cp lib\a.jar -Xmx1G -jar game.jar", "game.jar"),
    # Python: the first argument that isn't an option; none for -c / -m
    ("python.exe", rf'"{PYTHON}" "C:\Tools\my tool.py" --fast', r"C:\Tools\my tool.py"),
    ("pythonw.exe", r"pythonw.exe -u -X utf8 -W ignore C:\x\game.pyw", r"C:\x\game.pyw"),
    ("py.exe", r"py -3.12 tool.py", "tool.py"),
    ("python3.12.exe", r"python3.12.exe -B tool.py", "tool.py"),
    ("python.exe", r'python -c "import game"', None),
    ("python.exe", r"python -m http.server", None),
    ("python.exe", r'python -uc "game.py"', None),                 # -u and -c together: a command, not a file
    ("python.exe", r"python -X frozen_modules=off tool.py", "tool.py"),
    ("python.exe", "python -u x.py", "x.py"),
    ("python.exe", "python -X dev x.py", "x.py"),
    ("python.exe", "python -mhttp.server 8000", None),            # -m / -c take the rest of the cluster as the value
    ("python.exe", "python -cprint(1) a.py", None),
    # cmd: the first word after /c or /k - Explorer's doubled quotes too
    ("cmd.exe", rf'{CMD} /c ""C:\Users\me\Desktop\start game.bat" "', r"C:\Users\me\Desktop\start game.bat"),
    ("cmd.exe", r'cmd.exe /d /s /c "C:\x\run.cmd" arg', r"C:\x\run.cmd"),
    ("cmd.exe", r"cmd /K start.bat", "start.bat"),
    ("cmd.exe", r'cmd /c call "C:\x y\start.bat"', r"C:\x y\start.bat"),
    ("cmd.exe", r'cmd /c "C:\My Games\call of duty.bat"', r"C:\My Games\call of duty.bat"),   # only a leading call goes
    ("cmd.exe", r"cmd /c C:\recall x.bat", r"C:\recall"),
    ("cmd.exe", r"cmd /c CALL  start.bat", "start.bat"),
    ("cmd.exe", r"cmd.exe", None),
    # PowerShell: the first .ps1 - -File, -Command "& '...'" (Run with PowerShell), a bare path (pwsh)
    ("powershell.exe", r'powershell.exe -ExecutionPolicy Bypass -File "C:\My Scripts\x.ps1" -n 1', r"C:\My Scripts\x.ps1"),
    ("powershell.exe", r"""powershell.exe "-Command" "if((Get-ExecutionPolicy ) -ne 'AllSigned') """
                       r"""{ Set-ExecutionPolicy -Scope Process Bypass }; & 'C:\Users\me\x.ps1'" """, r"C:\Users\me\x.ps1"),
    ("pwsh.exe", r"pwsh C:\s\tool.PS1", r"C:\s\tool.PS1"),
    ("powershell.exe", "powershell -EncodedCommand ZQBjAGgAbwA=", None),
    # Windows Script Host: the first argument that isn't a // option
    ("wscript.exe", r'wscript.exe //B //Nologo "C:\x\a b.vbs" /arg', r"C:\x\a b.vbs"),
    ("cscript.exe", r"cscript tool.js", "tool.js"),
    # not an interpreter: nothing is read from it
    ("notepad.exe", r"notepad.exe C:\x\tool.py", None),
])
def test_hosted_file(name, cmd, found):
    assert apps.hosted_file(name, cmd) == found


def test_kinds_and_interpreters():
    assert apps.hosted_kind(r"C:\Games\Game.JAR") == "java" and apps.hosted_kind("start.bat") == "cmd"
    assert apps.hosted_kind("game.exe") is None and apps.hosted_kind("notes.txt") is None and not apps.is_hosted("")
    assert {apps.interpreter(n) for n in ("java.exe", "javaw.exe")} == {"java"}
    assert {apps.interpreter(n) for n in ("python.exe", "pythonw.exe", "py.exe", "pyw.exe", "python3.exe",
                                          "pythonw3.11.exe", "Python.EXE")} == {"python"}
    assert apps.interpreter("powershell.exe") == apps.interpreter("pwsh.exe") == "powershell"
    assert apps.interpreter("cmd.exe") == "cmd" and apps.interpreter("wscript.exe") == "wsh"
    assert apps.interpreter("notepad.exe") is None and apps.interpreter("pythonista.exe") is None


def test_a_program_file_is_closed_or_minimised_only():
    assert apps.hosted_way("close,internet") == "close"
    assert apps.hosted_way("close,background") == "close"
    assert apps.hosted_way("minimize,internet") == "minimize"
    assert apps.hosted_way(None) == "close"
    assert apps.hosted_way("internet") is None          # (a group that only cuts its members' internet: nothing)
    # no "background processes" folder for a script - everything in its folder would have been closed
    assert apps.app_folder({"app_path": r"C:\Tools\Mine\tool.py", "block_type": "close,background"}) is None
    assert apps.app_folder({"app_path": r"C:\Tools\Mine\tool.exe", "block_type": "close,background"}) == r"c:\tools\mine"
    # a program file in a game library still has the game's folder
    assert apps.app_folder({"app_path": r"D:\steamapps\common\Mine\game.jar", "block_type": "close"}) \
        == r"d:\steamapps\common\mine"


# ---------------------------------------------------------------- shortcuts and the file picker

def test_shortcut_program():
    # a Java game's shortcut: javaw.exe -jar game.jar, from its working folder
    assert apps.shortcut_program(JAVAW, "-jar game.jar", r"C:\Games\Mine") == r"C:\Games\Mine\game.jar"
    assert apps.shortcut_program(JAVAW, r'-jar "D:\x\game.jar"', r"C:\Games") == r"D:\x\game.jar"
    assert apps.shortcut_program(CMD, r'/c "C:\x\start.bat"', "") == r"C:\x\start.bat"
    assert apps.shortcut_program(r"C:\x\start.bat", "", "") == r"C:\x\start.bat"
    assert apps.shortcut_program(r"C:\Games\Game.exe", "-fullscreen", "") == r"C:\Games\Game.exe"
    assert apps.shortcut_program(JAVAW, "-version", "") == JAVAW       # no file: the interpreter itself (asked first)
    assert apps.shortcut_program(r"C:\x\notes.txt", "", "") is None
    assert apps.shortcut_program("", "", "") is None                    # a web address / a shell folder


def test_program_file_resolves_shortcuts():
    links = {r"C:\Users\me\Desktop\Mine.lnk": (JAVAW, "-jar mine.jar", r"C:\Games\Mine"),
             r"C:\Users\me\Desktop\Notes.lnk": (r"C:\x\notes.txt", "", "")}
    read = lambda path: links[path]
    assert applist.program_file(r"C:\Users\me\Desktop\Mine.lnk", read) == r"C:\Games\Mine\mine.jar"
    assert applist.program_file(r"C:\Games\game.jar", read) == r"C:\Games\game.jar"
    assert applist.program_file(r"C:\Games\Game.EXE", read) == r"C:\Games\Game.EXE"
    with pytest.raises(ValueError, match="doesn't start a program"):
        applist.program_file(r"C:\Users\me\Desktop\Notes.lnk", read)
    with pytest.raises(ValueError, match="Couldn't read"):
        applist.program_file(r"C:\Users\me\Desktop\Broken.lnk", read)
    with pytest.raises(ValueError, match="not notes.txt"):
        applist.program_file(r"C:\x\notes.txt", read)


def test_typed_program_names():
    from gui.target_picker import typed_program
    assert typed_program("game.jar") and typed_program("start.bat") and typed_program("Game.exe")
    assert typed_program(r"C:\Tools\tool.py") and not typed_program("tienda.com.py")   # .py is Paraguay too
    assert not typed_program("reddit.com") and not typed_program("")


def test_a_program_files_block_events_are_app_events():
    assert alerts.is_app_event({"hostname": "discord.exe", "reason": "schedule"})
    assert alerts.is_app_event({"hostname": "game.jar", "reason": "limit"}, {"item_type": "app"})
    assert alerts.is_app_event({"hostname": "tool.py", "reason": "mode"})                 # (a mode's own item)
    assert not alerts.is_app_event({"hostname": "tienda.com.py", "reason": "schedule"}, {"item_type": "site"})
    assert not alerts.is_app_event({"hostname": "bad.com.py", "reason": "protection:malware"})
    assert not alerts.is_app_event({"hostname": "reddit.com", "reason": "schedule"})


# ---------------------------------------------------------------- the service: closing the right process

def make_enforcer(monkeypatch, procs, cmds, target="game.jar", block_type="close", paths=None):
    """An enforcer with one blocked item; procs: [(pid, name)], cmds: {pid: command line}."""
    e = object.__new__(service.Enforcer)   # skip __init__ (trusted clock, database)
    e.visit_lock = threading.Lock()
    e.last_visit, e.app_first_seen, e.block_since, e.helpers, e.path_cache = {}, {}, {}, {}, {}
    e.events, e.killed, e.reads = [], [], []
    e.record_event = lambda key, block: e.events.append(key)
    item = {"id": 1, "display_name": "Game", "target": target, "block_type": block_type,
            "app_path": rf"C:\Games\{target}"}
    e.app_blocks = {target: {"item": item, "reason": "schedule", "until": None}}
    e.app_items = {target: item}
    monkeypatch.setattr(apps, "list_processes_full", lambda: [(pid, 4, name) for pid, name in procs])
    monkeypatch.setattr(apps, "command_line", lambda pid: e.reads.append(pid) or cmds.get(pid))
    monkeypatch.setattr(apps, "process_path", lambda pid: (paths or {}).get(pid))
    monkeypatch.setattr(apps, "terminate", lambda pid: e.killed.append(pid) or True)
    monkeypatch.setattr(apps, "start_time", lambda pid: 10 ** 10)      # launched while blocked: closed at once
    return e


def test_only_the_blocked_java_game_is_closed(monkeypatch):
    procs = [(10, "javaw.exe"), (11, "javaw.exe"), (12, "java.exe"), (13, "notepad.exe")]
    cmds = {10: rf'"{JAVAW}" -jar "C:\Games\Game.jar"', 11: rf'"{JAVAW}" -jar C:\Games\other.jar',
            12: r"java -jar D:\Copy\game.jar nogui", 13: r"notepad.exe C:\Games\game.jar"}
    e = make_enforcer(monkeypatch, procs, cmds)
    e.enforce_apps()
    assert sorted(e.killed) == [10, 12]          # by file name, any case, wherever the copy is; not other.jar
    assert e.events == ["game.jar", "game.jar"]
    assert 13 not in e.reads                     # only interpreters' command lines are read


def test_a_blocked_script_closes_its_python_but_never_another(monkeypatch):
    """python.exe is protected (Lockdown runs on it from source) - but one running a script you listed is closed."""
    procs = [(20, "python.exe"), (21, "pythonw.exe"), (22, "python.exe")]
    cmds = {20: rf'"{PYTHON}" C:\Tools\farm.py', 21: r"pythonw.exe C:\Tools\other.py", 22: "python -m farm"}
    e = make_enforcer(monkeypatch, procs, cmds, target="farm.py")
    e.enforce_apps()
    assert e.killed == [20, 22]                  # (python -m farm runs farm too)


def test_a_bat_started_from_explorer_is_closed(monkeypatch):
    procs = [(30, "cmd.exe"), (31, "cmd.exe")]
    cmds = {30: rf'{CMD} /c ""C:\Users\me\Desktop\Start Game.bat" "', 31: rf"{CMD} /k"}
    e = make_enforcer(monkeypatch, procs, cmds, target="start game.bat")
    e.enforce_apps()
    assert e.killed == [30]


def test_command_lines_are_read_once_per_process(monkeypatch):
    """The app check runs 4x a second: each process's command line is read once, not on every pass."""
    procs = [(10, "javaw.exe"), (11, "javaw.exe")]
    cmds = {10: r"javaw -jar other.jar", 11: r"javaw -jar other2.jar"}
    e = make_enforcer(monkeypatch, procs, cmds)
    for _ in range(5):
        e.enforce_apps()
    assert sorted(e.reads) == [10, 11] and e.killed == []


def test_an_unreadable_command_line_is_tried_again(monkeypatch):
    procs = [(10, "javaw.exe")]
    cmds = {}
    e = make_enforcer(monkeypatch, procs, cmds)
    e.enforce_apps()
    assert e.killed == []
    cmds[10] = r"javaw -jar game.jar"          # (readable now)
    e.enforce_apps()
    assert e.killed == [10]


def test_an_empty_command_line_is_not_remembered(monkeypatch):
    procs = [(10, "javaw.exe")]
    cmds = {10: ""}                             # (not filled in yet)
    e = make_enforcer(monkeypatch, procs, cmds)
    e.enforce_apps()
    cmds[10] = r"javaw -jar game.jar"
    e.enforce_apps()
    assert e.killed == [10]


def test_nothing_is_read_without_a_program_file_on_the_list(monkeypatch):
    procs = [(10, "javaw.exe"), (11, "game.exe")]
    e = make_enforcer(monkeypatch, procs, {10: "javaw -jar game.jar"}, target="game.exe")
    e.enforce_apps()
    assert e.reads == [] and e.killed == [11]


def test_lockdowns_own_scripts_are_never_closed(monkeypatch):
    """Run from source, Lockdown is pythonw running its own main.py: a "main.py" on the list never closes it."""
    own = str(service.APP_DIR).replace("/", "\\")
    procs = [(40, "pythonw.exe"), (41, "pythonw.exe"), (42, "python.exe")]
    cmds = {40: rf'pythonw.exe "{own}\src\main.py"', 41: r"pythonw.exe main.py", 42: r"python C:\Games\main.py"}
    e = make_enforcer(monkeypatch, procs, cmds, target="main.py", paths={41: own + r"\.venv\Scripts\pythonw.exe"})
    monkeypatch.setattr(service, "APP_DIR", own)   # (a Windows path on any test machine)
    e.enforce_apps()
    assert e.killed == [42]


def test_minimised_only_program_files_are_left_to_the_tray(monkeypatch):
    e = make_enforcer(monkeypatch, [(10, "javaw.exe")], {10: "javaw -jar game.jar"}, block_type="minimize")
    e.enforce_apps()
    assert e.killed == [] and e.members == {10: ["game.jar"]}    # (published: the tray minimises its windows)


# ---------------------------------------------------------------- end to end: blocked, counted, limited

def add_jar(w: World, rule_list, block_type="close") -> int:
    return w.gui.add_item("Block Game", ["game.jar"], "app", rules=rule_list, block_type=block_type,
                          app_path=r"C:\Games\Block Game\game.jar")


def java(w: World, jar: str) -> int:
    pid = w.launch("javaw.exe", JAVAW)
    w.cmds[pid] = rf'"{JAVAW}" -jar "{jar}"'
    return pid


@pytest.fixture
def jworld(world, monkeypatch):  # noqa: F811
    def make(start):
        w = world(start)
        w.cmds = {}
        monkeypatch.setattr(apps, "command_line", lambda pid: w.cmds.get(pid))
        return w
    return make


def test_java_game_closed_in_its_blocked_hours_other_java_apps_run_on(jworld):
    w = jworld(at(FRIDAY, "21:59"))
    add_jar(w, [block_during("22:00", "23:00")])
    game, other = java(w, r"C:\Games\Block Game\game.jar"), java(w, r"C:\Tools\ide.jar")
    w.run(1)
    assert w.alive(game) and w.alive(other)
    w.run(1)                                     # 22:00: blocked - asked to close, forced after 10 s
    w.run(0.5)
    assert not w.alive(game) and w.alive(other)


def test_java_game_time_is_counted_and_its_daily_limit_closes_it(jworld):
    w = jworld(at(FRIDAY, "19:00"))
    gid = add_jar(w, [{"rule_type": "time_limit", "daily_limit_min": 10}])
    game, other = java(w, r"C:\Games\Block Game\game.jar"), java(w, r"C:\Tools\ide.jar")
    w.play(other)                                # another Java program in front: not the game's time
    w.run(5)
    assert w.used(gid) == 0
    w.play(game)                                 # the game in front: javaw.exe, counted as game.jar
    w.run(9)
    assert 8 * 60 <= w.used(gid) <= 9 * 60 + 20 and w.alive(game)
    w.run(2)
    assert w.reason(gid) == "limit" and not w.alive(game) and w.alive(other)


def test_screen_time_names_the_game_not_javaw(jworld):
    w = jworld(at(FRIDAY, "19:00"))
    add_jar(w, [{"rule_type": "time_limit", "daily_limit_min": 60}])
    game = java(w, r"C:\Games\Block Game\game.jar")
    w.play(game)
    w.run(2)
    w.tracker.flush(w.tray_db)
    exes = {r[0] for r in w.gui.conn.execute("SELECT exe FROM activity WHERE active_seconds > 0")}
    assert "game.jar" in exes


def test_each_launch_of_the_java_game_counts_for_its_opening_limit(jworld):
    w = jworld(at(FRIDAY, "10:00"))
    gid = add_jar(w, [{"rule_type": "switch_limit", "daily_switch_limit": 1, "switch_mode": "visit"}])
    w.step()
    first = java(w, r"C:\Games\Block Game\game.jar")
    w.run(1)
    assert w.alive(first) and w.reason(gid) is None
    w.procs = [p for p in w.procs if p[0] != first]
    w.run(1)
    second = java(w, r"C:\Games\Block Game\game.jar")
    w.run(0.5)
    assert w.reason(gid) == "switches" and not w.alive(second)


def test_internet_is_never_cut_for_a_program_file(jworld, monkeypatch):
    added = []
    w = jworld(at(FRIDAY, "22:30"))
    monkeypatch.setattr(service.firewall, "add", lambda exe, path: added.append(exe) or True)
    add_jar(w, [block_during("22:00", "23:00")], block_type="close,internet")
    game = java(w, r"C:\Games\Block Game\game.jar")
    w.run(0.5)
    assert added == [] and not w.alive(game)        # closed - and javaw.exe keeps its internet


def test_without_the_tray_app_the_service_counts_the_java_game(jworld):
    w = jworld(at(FRIDAY, "19:00"))
    gid = add_jar(w, [{"rule_type": "time_limit", "daily_limit_min": 60}])
    java(w, r"C:\Games\Block Game\game.jar")
    java(w, r"C:\Tools\ide.jar")
    w.tray_running = False
    w.run(3)
    assert 2 * 60 <= w.used(gid) <= 3 * 60 + 10


def test_the_tray_learns_which_processes_are_the_file():
    published = {"10": ["game.jar"], "11": ["game.jar", "hollowgame.exe"], "12": ["tool.py"], "13": "junk"}

    class DB:
        def get_setting(self, key, default=None):
            import json
            return json.dumps(published)
    assert usage_mod.pids_of(DB(), "game.jar") == {10, 11}
    assert usage_mod.pids_of(DB(), "hollowgame.exe") == set()       # an .exe's windows go by its name
    assert usage_mod.hosted_running(published) == {"game.jar", "tool.py"}


def test_a_java_game_open_when_its_block_begins_is_asked_to_close_and_says_so(jworld, monkeypatch):
    """The tray asks the game's windows to close (they are javaw.exe's: by the processes the service publishes) and
    shows the notice - a block event for game.jar used to be taken for a site's."""
    from gui import app as appmod
    from test_visit_notices import Tray
    w = jworld(at(FRIDAY, "18:58"))
    add_jar(w, [block_during("19:00", "20:00")])
    game, other = java(w, r"C:\Games\Block Game\game.jar"), java(w, r"C:\Tools\ide.jar")
    w.run(1)
    tray, asked = Tray(w, monkeypatch), []
    monkeypatch.setattr(appmod.win, "close_windows", lambda pids: asked.append(set(pids)))
    while not w.gui.block_events_after(0):      # 19:00: blocked - the tray hears of it within a second
        w.step()
    tray.poll()
    assert asked == [{game}] and tray.closed == []
    assert tray.shown == ["Block Game is blocked until 20:00."]
    w.run(0.5)
    assert not w.alive(game) and w.alive(other)


@pytest.mark.parametrize("name, cmd, found", [
    ("javaw.exe", r"javaw -cp lib\a.jar;game.jar com.game.Main", [r"lib\a.jar", "game.jar", "com.game.Main.class", r"-m:com\game\Main.class"]),
    ("java.exe", r"java -Xmx1G -classpath C:/g/game.jar;lib/* Main arg", [r"C:\g\game.jar", "Main.class"]),
    ("java.exe", "java -cp . Main", ["Main.class"]),
    ("java.exe", "java -jar game.jar", ["game.jar"]),
    ("python.exe", "python -m http.server 8000", [r"-m:http\server.py", r"-m:http\server\__main__.py"]),
    ("pythonw.exe", "pythonw -u -mgame", ["-m:game.py", r"-m:game\__main__.py"]),
    ("python.exe", "python -W ignore -m tool", ["-m:tool.py", r"-m:tool\__main__.py"]),
    ("python.exe", "python -c print(1)", []),
    ("python.exe", "python tool.py -m x", ["tool.py"]),
    ("cmd.exe", r'cmd /c "cd /d C:\Games\X && x.bat"', [r"cd /d C:\Games\X && x.bat", "x.bat"]),
    ("cmd.exe", r'cmd /c "cd /d "C:\My Games" && "C:\My Games\run it.cmd" arg"', [r"C:\My Games\run it.cmd", "it.cmd"]),   # (+ the last word: quote pairing is ambiguous)
    ("cmd.exe", r"cmd /c echo hi & call sub.BAT", ["echo", "sub.BAT"]),
    ("cmd.exe", r"cmd /c dir", ["dir"]),
    ("notepad.exe", r"notepad -m x.bat", []),
])
def test_hosted_files_other_launch_styles(name, cmd, found):
    got = apps.hosted_files(name, cmd)
    # (hosted_file's first-word guess can be a whole chain; compare the bare file names that are clean)
    names = [apps.exe_name(g) for g in got if " " not in apps.exe_name(g) and apps.is_hosted(g)]
    assert names == [apps.exe_name(f) for f in found if " " not in apps.exe_name(f) and apps.is_hosted(f)]


def test_chained_and_module_launches_are_closed(monkeypatch):
    procs = [(1, "cmd.exe"), (2, "python.exe"), (3, "javaw.exe"), (4, "javaw.exe"), (5, "cmd.exe"), (6, "pythonw.exe")]
    cmds = {1: r'cmd /c "cd /d C:\Games\X && farm.bat"', 2: "python -m farm", 3: r"javaw -cp lib\other.jar com.Other",
            4: r"javaw -cp lib com.Other", 5: r"cmd /c cd /d C:\X && other.bat", 6: "pythonw -mfarm"}
    e = make_enforcer(monkeypatch, procs, cmds, target="farm.bat")
    e.enforce_apps()
    assert e.killed == [1]
    e = make_enforcer(monkeypatch, procs, cmds, target="farm.py")
    e.enforce_apps()
    assert e.killed == [2, 6]
    e = make_enforcer(monkeypatch, procs, cmds, target="other.jar")
    e.enforce_apps()
    assert e.killed == [3]
    e = make_enforcer(monkeypatch, procs, cmds, target="com.other.class")
    e.enforce_apps()
    assert e.killed == [3, 4]


def test_a_module_in_lockdowns_own_folder_is_never_closed(monkeypatch):
    own = str(service.APP_DIR).replace("/", "\\")
    monkeypatch.setattr(service, "APP_DIR", own)
    e = make_enforcer(monkeypatch, [(1, "python.exe")], {1: "python -m main"}, target="main.py",
                      paths={1: own + r"\.venv\Scripts\python.exe"})
    e.enforce_apps()
    assert e.killed == []


def test_a_pathological_cmd_line_is_scanned_fast():
    import time
    for cmd in ("cmd /c " + "a." * 16000, 'cmd /c "' + "a.b " * 8000, "cmd /c " + "&" * 32000):
        start = time.perf_counter()
        apps.hosted_files("cmd.exe", cmd)
        assert time.perf_counter() - start < 0.5
    assert "late.bat" not in apps.hosted_files("cmd.exe", "cmd /c " + "x " * 20000 + "late.bat")   # (past the 8 K scanned)


def test_dotted_modules_match_by_path_not_last_segment(monkeypatch):
    procs = [(1, "python.exe"), (2, "python.exe"), (3, "python.exe")]
    cmds = {1: "python -m http.server 8000", 2: "python -m pkg.sub", 3: "python -m tool"}
    e = make_enforcer(monkeypatch, procs, cmds, target="server.py")
    e.enforce_apps()
    assert e.killed == []                         # python -m http.server is not a listed server.py
    e = make_enforcer(monkeypatch, procs, cmds, target="sub.py")
    e.app_items["sub.py"]["app_path"] = r"C:\Dev\pkg\sub.py"
    e.enforce_apps()
    assert e.killed == [2]                        # ... but is pkg\sub.py
    e = make_enforcer(monkeypatch, procs, cmds, target="sub.py")
    e.app_items["sub.py"]["app_path"] = r"C:\Dev\other\sub.py"
    e.enforce_apps()
    assert e.killed == []
    e = make_enforcer(monkeypatch, procs, cmds, target="__main__.py")
    e.app_items["__main__.py"]["app_path"] = r"C:\Dev\tool\__main__.py"
    e.enforce_apps()
    assert e.killed == [3]                        # a single module also finds its package's __main__.py
    e = make_enforcer(monkeypatch, procs, cmds, target="tool.py")
    e.enforce_apps()
    assert e.killed == [3]                        # ... and tool.py by name


def test_a_packaged_java_main_class_is_matched_by_its_path(monkeypatch):
    procs = [(1, "java.exe"), (2, "java.exe"), (3, "java.exe")]
    cmds = {1: r"java -cp C:\g com.game.Main", 2: r"java -cp C:\g com.other.Main", 3: "java -cp . Main"}
    e = make_enforcer(monkeypatch, procs, cmds, target="main.class")
    e.app_items["main.class"]["app_path"] = r"C:\g\com\game\Main.class"
    e.enforce_apps()
    assert e.killed == [1, 3]   # (com.other.Main is another Main; a plain "Main" has no package to compare)
