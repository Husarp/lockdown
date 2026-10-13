"""App icons that came out as letter tiles in 0.91.0 (Firefox, Brave, Javaw, LockApp): every way to get a program's
icon is tried in turn and a blank picture counts as none (resident/icons.icon_chain), Store / system apps get their
package's logo (AppxManifest.xml), programs the catalog doesn't know are looked for in SystemApps / App Paths / PATH,
app icons never wait behind the slow site downloads, 0.91.0's icons are made again once, and the reason for every icon
that can't be made (or needed a fallback) is in the log once per run.

Nothing here needs Windows: the ways are fakes, the package folders are made in tmp_path. What only a Windows machine
proves: SHDefExtractIcon / ExtractIconEx / SHGetFileInfo / DrawIconEx on real programs, and real package manifests."""
import io

from PIL import Image

from resident import icons

PNG = b"\x89PNG\r\n\x1a\nfake-icon"


def picture(alpha=255, size=32):
    return Image.new("RGBA", (size, size), (200, 40, 40, alpha))


def png_bytes(size):
    out = io.BytesIO()
    Image.new("RGBA", (size, size), (10, 120, 200, 255)).save(out, "PNG")
    return out.getvalue()


# ================================================================ the chain of ways

def test_the_chain_goes_on_when_a_way_gives_nothing_or_a_blank_picture_or_fails():
    good = picture()
    notes = []

    def boom():
        raise OSError("access denied")

    got = icons.icon_chain([("big extract", lambda: picture(alpha=0)), ("ExtractIconEx", lambda: None),
                            ("app package logo", boom), ("SHGetFileInfo", lambda: good)], notes)
    assert got is good
    assert notes == ["big extract gave no pixels", "ExtractIconEx gave nothing",
                     "app package logo failed (OSError: access denied)", "SHGetFileInfo ok"]


def test_the_chain_stops_at_the_first_way_that_works():
    called = []
    notes = []
    got = icons.icon_chain([("big extract", lambda: called.append(1) or picture()),
                            ("ExtractIconEx", lambda: called.append(2) or picture())], notes)
    assert got is not None and called == [1] and notes == ["big extract ok"]


def test_the_chain_gives_none_when_no_way_works():
    notes = []
    assert icons.icon_chain([("a", lambda: None), ("b", lambda: picture(alpha=0))], notes) is None
    assert notes == ["a gave nothing", "b gave no pixels"]


def test_a_picture_drawn_on_black_and_white_keeps_its_transparency():
    black = Image.new("RGB", (2, 1), (0, 0, 0))
    white = Image.new("RGB", (2, 1), (255, 255, 255))
    black.putpixel((1, 0), (100, 0, 0))     # pixel 1: red at half alpha, premultiplied on black
    white.putpixel((1, 0), (228, 127, 127))
    black.putpixel((0, 0), (0, 0, 0))       # pixel 0: fully transparent (black on black, white on white)
    image = icons.from_black_and_white(black, white)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0
    r, g, b, a = image.getpixel((1, 0))
    assert 125 <= a <= 130 and r > 190 and g < 5 and b < 5
    opaque = icons.from_black_and_white(Image.new("RGB", (1, 1), (10, 20, 30)), Image.new("RGB", (1, 1), (10, 20, 30)))
    assert opaque.getpixel((0, 0)) == (10, 20, 30, 255)


def test_an_exe_off_windows_says_why_and_an_icon_file_is_read(tmp_path):
    notes = []
    assert icons.extract_exe_icon("C:\\Program Files\\Mozilla Firefox\\firefox.exe", notes) is None
    assert notes == ["not Windows"]
    ico = tmp_path / "a.png"
    ico.write_bytes(png_bytes(48))
    notes = []
    assert icons.extract_exe_icon(str(ico), notes) and notes == ["icon file ok"]
    notes = []
    assert icons.extract_exe_icon(str(tmp_path / "gone.ico"), notes) is None
    assert notes == ["icon file missing or unreadable"]


# ================================================================ app packages (LockApp and Store apps)

MANIFEST = b"""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
         xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10">
  <Properties><DisplayName>LockApp</DisplayName><Logo>Assets\\StoreLogo.png</Logo></Properties>
  <Applications>
    <Application Id="Other" Executable="Other.exe">
      <uap:VisualElements Square44x44Logo="Assets\\Other.png" Square150x150Logo="Assets\\Other150.png"/>
    </Application>
    <Application Id="WindowsDefaultLockScreen" Executable="LockApp.exe">
      <uap:VisualElements DisplayName="LockApp" Square44x44Logo="Assets\\LockScreenLogo.png"
                          Square150x150Logo="Assets\\Square150.png"/>
    </Application>
  </Applications>
</Package>"""


def test_the_manifest_names_the_logo_of_the_right_application():
    assert icons.manifest_logos(MANIFEST, "LockApp.exe") == ["Assets\\LockScreenLogo.png", "Assets\\Square150.png",
                                                             "Assets\\StoreLogo.png"]
    assert icons.manifest_logos(MANIFEST, "lockapp.exe")[0] == "Assets\\LockScreenLogo.png"   # (any case)
    assert icons.manifest_logos(MANIFEST, "Unknown.exe")[0] == "Assets\\Other.png"             # (else the first app)


def test_a_store_app_in_a_subfolder_is_matched_by_its_path():
    manifest = b"""<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"><Applications>
      <Application Executable="tools\\helper.exe"><VisualElements Square44x44Logo="h.png"/></Application>
      <Application Executable="app\\Spotify.exe"><VisualElements Square44x44Logo="s.png"/></Application>
    </Applications></Package>"""
    assert icons.manifest_logos(manifest, "app/Spotify.exe")[0] == "s.png"


def make_package(tmp_path, files, manifest=MANIFEST):
    package = tmp_path / "SystemApps" / "Microsoft.LockApp_cw5n1h2txyewy"
    (package / "Assets").mkdir(parents=True)
    (package / "AppxManifest.xml").write_bytes(manifest)
    (package / "LockApp.exe").write_bytes(b"MZ")
    for name, size in files.items():
        (package / "Assets" / name).write_bytes(png_bytes(size))
    return package


def test_the_biggest_scaled_logo_is_taken(tmp_path):
    package = make_package(tmp_path, {"LockScreenLogo.scale-100.png": 44, "LockScreenLogo.scale-200.png": 88,
                                      "LockScreenLogo.targetsize-48.png": 48,
                                      "LockScreenLogo.targetsize-256_altform-unplated.png": 256,
                                      "LockScreenLogo.targetsize-256_contrast-black.png": 256,
                                      "Square150.png": 150})
    logo = icons.package_logo(str(package / "LockApp.exe"))
    assert logo == str(package / "Assets" / "LockScreenLogo.targetsize-256_altform-unplated.png")


def test_an_unqualified_logo_file_is_taken_as_it_is_and_a_missing_one_falls_to_the_next(tmp_path):
    package = make_package(tmp_path, {"Square150.png": 150})       # (no LockScreenLogo at all)
    assert icons.package_logo(str(package / "LockApp.exe")) == str(package / "Assets" / "Square150.png")


def test_the_logo_becomes_the_icon(tmp_path):
    package = make_package(tmp_path, {"LockScreenLogo.scale-200.png": 88})
    image = icons._logo_image(str(package / "LockApp.exe"))
    assert image.size == (88, 88) and icons.visible(image)
    assert icons.image_file_icon(icons.package_logo(str(package / "LockApp.exe")))


def test_a_program_outside_a_package_has_no_logo_and_bad_manifests_never_escape(tmp_path):
    (tmp_path / "plain").mkdir()
    assert icons.package_logo(str(tmp_path / "plain" / "app.exe")) is None
    assert icons.resolve_asset(tmp_path, "..\\..\\secret.png") is None
    package = make_package(tmp_path, {}, manifest=b"<Package><Applications/></Package>")
    assert icons.package_logo(str(package / "LockApp.exe")) is None


# ================================================================ where a program is

def test_lockapp_is_found_in_systemapps(tmp_path):
    package = make_package(tmp_path, {})
    assert icons.system_app_path("LockApp.exe", root=tmp_path) == str(package / "LockApp.exe")
    assert icons.system_app_path("nothing.exe", root=tmp_path) is None
    assert icons.system_app_path("LockApp.exe", root=tmp_path / "no-windows") is None


def test_app_paths_and_path_find_browsers_and_java(tmp_path):
    firefox = tmp_path / "Mozilla Firefox" / "firefox.exe"
    firefox.parent.mkdir()
    firefox.write_bytes(b"MZ")
    assert icons.registered_path("firefox.exe", read=lambda n: ["", f'"{firefox}"']) == str(firefox)
    assert icons.registered_path("firefox.exe", read=lambda n: [str(tmp_path / "gone.exe")]) is None
    javapath = tmp_path / "javapath"
    javapath.mkdir()
    (javapath / "javaw.exe").write_bytes(b"MZ")
    import os
    env = os.pathsep.join(["", "relative\\bin", str(tmp_path / "missing"), str(javapath)])
    assert icons.path_on("javaw.exe", env) == str(javapath / "javaw.exe")
    assert icons.path_on("brave.exe", env) is None


def test_find_exe_tries_systemapps_after_the_windows_folders(tmp_path, monkeypatch):
    assert icons.find_exe("firefox.exe") is None                        # (off Windows: no Windows folders)
    package = make_package(tmp_path, {})
    monkeypatch.setattr(icons, "_system_root", lambda: tmp_path)
    assert icons.find_exe("LockApp.exe") == str(package / "LockApp.exe")
    assert icons.find_exe("LockApp") is None                             # (only .exe names)


# ================================================================ the service: log, queue, cache version

def service(tmp_path, places=None, works=(), **kw):
    lines = []

    def extract(place):
        return PNG if place in works else None

    s = icons.IconService(tmp_path / "icons", path_of=lambda e: (places or {}).get(e, [None])[-1],
                          sources_of=lambda e: (places or {}).get(e, [])[:-1], extract=extract,
                          old_dir=tmp_path / "none", start=False, log=lines.append, **kw)
    return s, lines


def test_an_icon_that_cant_be_made_is_logged_once_per_run_with_its_reason(tmp_path):
    clock = [0.0]
    s, lines = service(tmp_path, clock=lambda: clock[0])
    s.handle("app/javaw.exe.png", {})
    s.run_pending()
    assert len(lines) == 1 and lines[0].startswith("icon javaw.exe: no icon - no path known")
    clock[0] += icons.RETRY_SEC + 1
    s.handle("app/javaw.exe.png", {})
    s.run_pending()
    assert len(lines) == 1                                              # (once per run)


def test_the_log_says_what_each_place_gave(tmp_path):
    s, lines = service(tmp_path, places={"firefox.exe": ["C:\\ff.ico", "C:\\ff.exe"]})
    s.handle("app/firefox.exe.png", {})
    s.run_pending()
    assert lines == ["icon firefox.exe: no icon - C:\\ff.ico: nothing | C:\\ff.exe: nothing"]


def test_a_fallback_that_worked_is_logged_and_a_first_try_that_worked_is_not(tmp_path):
    s, lines = service(tmp_path, places={"brave.exe": ["C:\\b.ico", "C:\\brave.exe"], "claude.exe": ["C:\\c.exe"]},
                       works={"C:\\brave.exe", "C:\\c.exe"})
    s.handle("app/brave.exe.png", {})
    s.handle("app/claude.exe.png", {})
    s.run_pending()
    assert lines == ["icon brave.exe: C:\\b.ico: nothing | C:\\brave.exe: ok"]
    assert s.handle("app/brave.exe.png", {}).content_type == "image/png"


def test_the_real_extractors_reasons_reach_the_log(tmp_path, monkeypatch):
    lines = []

    def real(place, notes):
        notes += ["big extract gave no pixels", "ExtractIconEx ok"]
        return PNG

    monkeypatch.setattr(icons, "extract_exe_icon", real)
    s = icons.IconService(tmp_path / "icons", path_of=lambda e: "C:\\firefox.exe", old_dir=tmp_path / "none",
                          start=False, log=lines.append)
    s.extract = real                       # (as the default: it is given the notes list)
    s.handle("app/firefox.exe.png", {})
    s.run_pending()
    assert lines == ["icon firefox.exe: C:\\firefox.exe: big extract gave no pixels; ExtractIconEx ok"]


def test_an_extractor_that_raises_doesnt_stop_the_next_place(tmp_path):
    def extract(place):
        if place == "C:\\bad.ico":
            raise OSError("broken")
        return PNG

    lines = []
    s = icons.IconService(tmp_path / "icons", path_of=lambda e: "C:\\a.exe", sources_of=lambda e: ["C:\\bad.ico"],
                          extract=extract, old_dir=tmp_path / "none", start=False, log=lines.append)
    s.handle("app/a.exe.png", {})
    s.run_pending()
    assert s.handle("app/a.exe.png", {}).content_type == "image/png"
    assert lines and "C:\\bad.ico: failed (OSError('broken'))" in lines[0]


def test_app_icons_dont_wait_behind_site_downloads(tmp_path):
    s, _ = service(tmp_path, places={"firefox.exe": ["C:\\ff.exe"]}, works={"C:\\ff.exe"},
                   fetch=lambda host: None)
    for i in range(icons.MAX_QUEUED):                   # the page asked for many sites: the site queue is full
        s.handle(f"site/site{i}.example.com.png", {})
    assert s.queued == icons.MAX_QUEUED
    s.handle("app/firefox.exe.png", {})
    assert s.queued == icons.MAX_QUEUED + 1              # (the app is queued all the same)


def test_apps_over_the_cap_wait_and_are_made_later(tmp_path):
    names = [f"app{i}.exe" for i in range(icons.MAX_QUEUED + 5)]
    ready = []
    s, _ = service(tmp_path, places={n: [f"C:\\{n}"] for n in names}, works={f"C:\\{n}" for n in names},
                   on_ready=ready.append)
    for n in names:
        s.handle(f"app/{n}.png", {})
    assert s.queued == icons.MAX_QUEUED
    s.run_pending()
    assert sorted(ready) == sorted(f"app:{n}" for n in names)                # (none was dropped)


def test_0_91_icons_are_made_again_once(tmp_path):
    folder = tmp_path / "icons"
    folder.mkdir()
    (folder / icons.APP_MARK).write_text("2", encoding="utf-8")
    (folder / "app_firefox.exe.png").write_bytes(b"blank from 0.91.0")
    s, _ = service(tmp_path, places={"firefox.exe": ["C:\\ff.exe"]}, works={"C:\\ff.exe"})
    assert icons.APP_CACHE_VERSION == "3"
    assert s.handle("app/firefox.exe.png", {}).content_type == "image/svg+xml"
    s.run_pending()
    assert s.handle("app/firefox.exe.png", {}).body == PNG
    assert (folder / icons.APP_MARK).read_text() == "3"
