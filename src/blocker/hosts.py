"""Hosts file manipulation.

Lockdown only ever touches the lines between START_MARKER and END_MARKER;
everything else in the hosts file is preserved as-is.
"""
import re
import shutil
import subprocess
from pathlib import Path

from paths import HOSTS_BACKUP_PATH, HOSTS_PATH

START_MARKER = "# >>> Lockdown START (managed automatically - do not edit)"
END_MARKER = "# <<< Lockdown END"
REDIRECT_IP = "127.0.0.1"

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_host(text: str) -> str:
    """Turn user input like 'https://www.Reddit.com/r/x' into 'reddit.com'. Raises ValueError."""
    host = text.strip().lower()
    host = re.sub(r"^[a-z]+://", "", host)
    host = re.split(r"[/?#:]", host, maxsplit=1)[0]
    host = host.removeprefix("www.")
    if not _HOST_RE.match(host):
        raise ValueError(f"Not a valid domain: {text!r}")
    return host


def expand(hostnames: list[str]) -> list[str]:
    """Add the www. variant of every hostname (hosts file has no wildcards)."""
    out = set()
    for h in hostnames:
        out.add(h)
        if not h.startswith("www."):
            out.add("www." + h)
    return sorted(out)


def build_lines(current_lines: list[str], hostnames: list[str]) -> list[str]:
    """Return the hosts file lines with the Lockdown section replaced (or removed if empty)."""
    kept, inside, found = [], False, False
    for line in current_lines:
        if line.strip() == START_MARKER:
            inside = found = True
        elif line.strip() == END_MARKER and inside:
            inside = False
        elif not inside:
            kept.append(line)
    if found or hostnames:
        # drop the blank separator line we add in front of the section
        while kept and not kept[-1].strip():
            kept.pop()
    if not hostnames:
        return kept
    section = [START_MARKER] + [f"{REDIRECT_IP} {h}" for h in expand(hostnames)] + [END_MARKER]
    return kept + [""] + section


def apply(hostnames: list[str], hosts_path: Path = HOSTS_PATH, backup_path: Path = HOSTS_BACKUP_PATH) -> bool:
    """Make the hosts file's Lockdown section match `hostnames`. Returns True if the file was rewritten."""
    text = hosts_path.read_text(encoding="utf-8", errors="surrogateescape")
    lines = text.splitlines()
    new_lines = build_lines(lines, hostnames)
    if new_lines == lines:
        return False
    if not backup_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(hosts_path, backup_path)
    # Write in place (not rename) so the file keeps its ACLs.
    with open(hosts_path, "w", encoding="utf-8", errors="surrogateescape", newline="\r\n") as f:
        f.write("\n".join(new_lines) + "\n")
    return True


def flush_dns():
    subprocess.run(["ipconfig", "/flushdns"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
