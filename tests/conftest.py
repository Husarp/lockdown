"""Make the test suite importable on a non-Windows machine (Linux CI / dev box).

Lockdown is a Windows app: several modules touch the Windows API the moment they are imported
(`ctypes.WinDLL("kernel32")`, `import winreg`, pywin32, COM/UI Automation, pystray's win32 backend,
customtkinter). On Windows this file does NOTHING - the real modules are used and production behaviour
is untouched. Everywhere else it installs inert fakes *before* any test imports `src/`, so the logic
tests (rules, anti-bypass, DB, reminders, ...) collect and run. Any fake Windows call just returns 0 /
raises OSError ("not found"), the same as a Windows box where the thing is missing.

Tests that genuinely need the real Win32 API (process list, TCP table, DNS adapter calls) carry an explicit
`skipif(sys.platform != "win32")` with the reason, in their own file - never a blanket skip here.
"""
import ctypes
import importlib.util
import sys
import types


IS_WINDOWS = sys.platform == "win32"


def _has_module(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


HAS_TK = _has_module("tkinter") and _has_module("customtkinter")



# --------------------------------------------------------------------------- generic inert fakes
class _AnyMeta(type):
    """Class-level attribute access on a fake class returns another fake (ctk.CTkFrame, tk.END, ...)."""

    def __getattr__(cls, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Any

    def __or__(cls, other):        # flags combined with |
        return 0

    __ror__ = __or__


class _Any(metaclass=_AnyMeta):
    """Subclassable, callable, attribute-tolerant stand-in for anything in a missing GUI/COM module."""

    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Any()

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Any()

    def __bool__(self):
        return False

    def __iter__(self):
        return iter(())

    def __int__(self):
        return 0


class _FakeModule(types.ModuleType):
    """A module where every missing attribute is a fake; submodules resolve too (pystray._util.win32)."""

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return _Any


def _install(name, **attrs):
    if name in sys.modules:
        return sys.modules[name]
    mod = _FakeModule(name)
    mod.__path__ = []          # behave like a package so "import a.b" works
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(_install(parent), child, mod)
    return mod


# --------------------------------------------------------------------------- ctypes.WinDLL / windll
class _FakeFunc:
    """A Win32 export: accepts argtypes/restype/errcheck, returns 0 (failure / nothing found)."""

    def __init__(self, name):
        self.__name__ = name
        self.argtypes = None
        self.restype = None
        self.errcheck = None

    def __call__(self, *a, **k):
        return 0


class _FakeDLL:
    def __init__(self, name="", *a, **k):
        self._name = name

    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        fn = _FakeFunc(name)
        setattr(self, name, fn)    # same object next time, like a real DLL
        return fn

    __getitem__ = __getattr__


class _FakeLoader:
    def __getattr__(self, name):
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        dll = _FakeDLL(name)
        setattr(self, name, dll)
        return dll


def _install_windows_fakes():
    ctypes.WinDLL = _FakeDLL
    ctypes.OleDLL = _FakeDLL
    ctypes.windll = _FakeLoader()
    ctypes.oledll = _FakeLoader()
    if not hasattr(ctypes, "WINFUNCTYPE"):
        ctypes.WINFUNCTYPE = ctypes.CFUNCTYPE
    if not hasattr(ctypes, "get_last_error"):
        ctypes.get_last_error = lambda: 0
        ctypes.set_last_error = lambda value: 0
    if not hasattr(ctypes, "WinError"):
        ctypes.WinError = lambda code=None, descr=None: OSError(code or 0, descr or "Windows error (fake)")
    if not hasattr(ctypes, "FormatError"):
        ctypes.FormatError = lambda code=None: "Windows error (fake)"

    # winreg: an EMPTY registry - keys open but have no subkeys and no values (a value read raises
    # FileNotFoundError, exactly what Windows does for a missing value); writes are swallowed.
    def _missing(*a, **k):
        raise FileNotFoundError(2, "registry value not found (non-Windows test run)")

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def Close(self):
            pass

    def _open(*a, **k):
        return _Key()

    _install("winreg",
             HKEY_CURRENT_USER=0x80000001, HKEY_LOCAL_MACHINE=0x80000002, HKEY_CLASSES_ROOT=0x80000000,
             KEY_READ=0x20019, KEY_WRITE=0x20006, KEY_SET_VALUE=0x0002, KEY_ALL_ACCESS=0xF003F,
             KEY_WOW64_64KEY=0x0100, KEY_WOW64_32KEY=0x0200,
             REG_SZ=1, REG_EXPAND_SZ=2, REG_BINARY=3, REG_DWORD=4, REG_MULTI_SZ=7, REG_QWORD=11,
             OpenKey=_open, OpenKeyEx=_open, CreateKey=_open, CreateKeyEx=_open,
             QueryInfoKey=lambda *a, **k: (0, 0, 0),
             QueryValueEx=_missing, EnumKey=_missing, EnumValue=_missing,
             DeleteValue=_missing, DeleteKey=_missing,
             SetValueEx=lambda *a, **k: None, CloseKey=lambda *a, **k: None)

    # pywin32, COM, UI Automation, tray icon: import-time only, never driven by a logic test
    for name in ("win32service", "win32serviceutil", "win32event", "win32api", "win32con", "win32gui",
                 "win32process", "servicemanager", "pywintypes", "pythoncom",
                 "comtypes", "comtypes.client", "uiautomation",
                 "pystray", "pystray._util", "pystray._util.win32", "webview"):
        if not _has_module(name.split(".")[0]) or name.split(".")[0] in ("pystray",):
            _install(name)


def _install_tk_fakes():
    """No Tk here: let GUI modules *import* (their pure helpers/constants are tested) - building a widget
    does nothing, so a test that needs a real widget must skip itself when tkinter is missing."""
    for name in ("tkinter", "tkinter.font", "tkinter.ttk", "tkinter.filedialog", "tkinter.colorchooser",
                 "tkinter.messagebox", "customtkinter",
                 "customtkinter.windows", "customtkinter.windows.widgets",
                 "customtkinter.windows.widgets.scaling", "customtkinter.windows.widgets.scaling.scaling_tracker",
                 "PIL.ImageTk"):
        top = name.split(".")[0]
        if name == "PIL.ImageTk":
            if not _has_module("tkinter"):
                _install(name)
                import PIL
                PIL.ImageTk = sys.modules[name]
        elif top == "tkinter" and not _has_module("tkinter"):
            _install(name)
        elif top == "customtkinter" and not _has_module("customtkinter"):
            _install(name)


if not IS_WINDOWS:
    _install_windows_fakes()
    if not HAS_TK:
        _install_tk_fakes()
