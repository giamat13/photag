"""Test: a window of photag left open by an update closes itself (app/windowwatch.py), and the program ends with hard_exit()
(TerminateProcess) so a stuck DLL exit cannot leave the old window on screen. No window, no real update: fake probes.

    py -3.12 tools/test_window_close.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PHOTAG_UPDATE_STATE_DIR"] = tempfile.mkdtemp(prefix="photag_winclose_test_")
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


from app import updater, windowwatch as ww  # noqa: E402


def run(answers, restarting, first="18.3.0"):
    """Feed the watcher these server answers (None = nothing answers); returns (closed, polls used)."""
    it, polls, closed = iter(answers), [0], []

    def probe(_port):
        polls[0] += 1
        return next(it)
    r = ww.watch(1, first, lambda: restarting, lambda: closed.append(1), interval=0, sleep=lambda s: None, probe=probe, max_polls=len(answers))
    return r, polls[0]


check("the same program keeps answering: the window stays", run(["18.3.0"] * 6, False)[0] is False)
check("a new version answers (the installer path): the window closes", run(["18.3.0", "18.3.0", "18.4.0"], False) == (True, 3))
check("the server is gone for a moment only: the window stays", run(["18.3.0", None, "18.3.0", None, "18.3.0"], True)[0] is False)
check("gone for 3 polls while an update restarts the program: the window closes", run(["18.3.0", None, None, None], True) == (True, 4))
check("gone without an update (a crash): the window is left alone", run([None] * 6, False)[0] is False)
check("the server came back with another version after a gap: closes", run([None, None, "18.4.0"], False)[0] is True)
check("a window that could not read the first version does not close on the first answer", run(["18.3.0", "18.3.0"], False, first=None)[0] is False)
check("should_close: down polls below the limit never close", not ww.should_close("1", None, 2, True) and ww.should_close("1", None, 3, True))

# the marker the updater writes before the program ends
check("no update marker at first", updater.restarting_recently() is False)
updater.mark_restarting()
check("the marker is written and recent", updater.restarting_recently() is True)
check("...and old markers are ignored", updater.restarting_recently(max_age=-1) is False)
check("hard_exit exists and is what the update timers call", callable(updater.hard_exit))
src = (ROOT / "app" / "updater.py").read_text("utf-8")
check("no update path ends with os._exit directly any more", "lambda: os._exit(0)" not in src and src.count("threading.Timer(1.0, hard_exit)") == 2 and "threading.Timer(1.5, hard_exit)" in src)
src_main = (ROOT / "photag.py").read_text("utf-8")
check("photag.py starts the watcher for windows of an already running program", "windowwatch.watch" in src_main and "ALREADY_RUNNING" in src_main)

sys.exit(0 if all(res) else 1)
