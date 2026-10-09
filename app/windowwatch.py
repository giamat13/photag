"""A window of photag that outlives the program it was showing.

Opening photag while it is already running opens one more window, in a process of its own that only shows the running server's page
(photag.py, ALREADY_RUNNING). When the program is updated, the process that owns the server ends and the new program starts; this other
window was left open on the old page. It now watches the server: when it is replaced by another run (a different version answers) or it
has gone while an update is restarting the program, the window closes itself.
"""
import json
import time
import urllib.request


def server_version(port: int, host: str = "127.0.0.1", timeout: float = 3.0):
    """The version photag answers with on this port, or None when nothing (or something else) answers."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=timeout) as r:
            d = json.loads(r.read())
        return d.get("version") if "library_root" in d else None
    except Exception:
        return None


def should_close(first, now, down_polls: int, restarting: bool, down_needed: int = 3) -> bool:
    if now is not None:
        return first is not None and now != first            # the new program answers: this window shows the old one
    return down_polls >= down_needed and restarting           # gone, and an update is the reason (a crash must not close windows)


def watch(port: int, first, restarting, close, interval: float = 2.0, sleep=time.sleep, probe=server_version, max_polls=None) -> bool:
    """Poll the server every `interval` seconds until `should_close` says so, then call close(). True when it closed the window."""
    down = polls = 0
    while max_polls is None or polls < max_polls:
        sleep(interval)
        polls += 1
        now = probe(port)
        down = down + 1 if now is None else 0
        if should_close(first, now, down, bool(restarting())):
            close()
            return True
    return False
