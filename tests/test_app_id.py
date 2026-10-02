"""The Windows app identity. It is written down in two places - main.py registers it and sets it on the
process, gui/toast.py sends Lockdown's Windows notifications under it - and they have to be the same string,
or the notifications show no Lockdown name/icon and their buttons can't call back."""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
WANTED = "com.husarp.lockdown"


def _app_id(path: Path) -> str:
    found = re.search(r'^APP_ID = "([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    assert found, f"no APP_ID in {path.name}"
    return found.group(1)


def test_both_copies_agree():
    assert _app_id(SRC / "main.py") == _app_id(SRC / "gui" / "toast.py") == WANTED


def test_it_is_a_reverse_dns_name():
    """com.husarp.<name>: the same shape for every one of these projects."""
    assert re.fullmatch(r"com\.husarp\.[a-z0-9]+", WANTED)
