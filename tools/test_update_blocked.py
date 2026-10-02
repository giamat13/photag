"""Test: when Windows (Smart App Control) refuses to run the downloaded installer, the updater says so, changes
nothing and keeps the app running (instead of reporting a missing file).

    py -3.12 tools/test_update_blocked.py
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
tmp = Path(tempfile.mkdtemp(prefix="photag_update_blocked_"))
os.environ["PHOTAG_UPDATE_STATE_DIR"] = str(tmp / "state")
from app import updater  # noqa: E402

res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


dl = updater.download_dir()
dl.mkdir(parents=True, exist_ok=True)
exe = dl / "photagSetup-9.9.9.exe"
exe.write_bytes(b"MZ not a real installer")

updater.prepare_rollback = lambda p, v: "journal"            # copying the whole program folder is not what is tested here
cleared = []
updater._clear_pending = lambda d: cleared.append(1)
quit_called = []
updater.os._exit = lambda code: quit_called.append(code)
sys.frozen = True


def blocked(*a, **k):
    e = OSError(13, "An Application Control policy has blocked this file")
    e.winerror = 4551
    raise e


updater.subprocess.Popen = blocked
try:
    updater.launch(str(exe)); got = None
except updater.BlockedError as e:
    got = e
except updater.UpdateError as e:
    got = e
check("a blocked installer raises BlockedError (not 'file not found')", isinstance(got, updater.BlockedError), type(got).__name__)
check("the half-started update was cleared (nothing to roll back)", cleared == [1])
check("the app was NOT quit", not quit_called)


def other(*a, **k):
    raise FileNotFoundError(2, "no such file")


updater.subprocess.Popen = other
try:
    updater.launch(str(exe)); got = None
except updater.BlockedError as e:
    got = "blocked"
except updater.UpdateError as e:
    got = "update-error"
check("any other launch failure is still a plain UpdateError", got == "update-error", got)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
