"""Tests for photag.py: a taken port never stops photag from starting, and a second start joins the running one.
Runs `python photag.py` headless (PHOTAG_NO_WINDOW) with a throw-away profile on test ports.

    py -3.12 tools/test_port_instance.py
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = 8780
tmp = Path(tempfile.mkdtemp(prefix="photag_port_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_WINDOW": "1", "PHOTAG_BASE_PORT": str(BASE), "PYTHONIOENCODING": "utf-8"}
env.pop("PHOTAG_PORT", None)
for d in ("appdata", "local", "home"):
    (tmp / d).mkdir()
res = []
procs = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def start():
    p = subprocess.Popen([sys.executable, str(ROOT / "photag.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
    procs.append(p)
    return p


def answers(port, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as r:
                return "library_root" in json.loads(r.read())
        except Exception:
            time.sleep(0.5)
    return False


def log_text():
    f = tmp / "appdata" / "photag" / "startup.log"
    return f.read_text("utf-8") if f.exists() else ""


try:
    # ---- 1. another program (not photag) holds the usual port
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", BASE)); blocker.listen(5)
    p1 = start()
    check("with the usual port taken by another program, photag starts on the next free port", answers(BASE + 1), f"exit={p1.poll()}")
    check("... and says so in its start-up log", f"port {BASE} is used by another program, using {BASE + 1}" in log_text())
    p1.terminate(); p1.wait(timeout=15)
    blocker.close()
    time.sleep(1)
    (tmp / "appdata" / "photag" / "startup.log").unlink(missing_ok=True)

    # ---- 2. the usual port is free: normal start
    p2 = start()
    check("with the usual port free, photag uses it", answers(BASE), f"exit={p2.poll()}")
    # ---- 3. a second start joins the running one instead of starting another server
    t = time.time()
    p3 = start()
    try:
        p3.wait(timeout=30)
        gone = True
    except subprocess.TimeoutExpired:
        gone = False
    check("a second start does not start another server (it exits, headless)", gone and p3.returncode == 0, f"{time.time() - t:.1f}s rc={p3.poll()}")
    check("... and the first one keeps serving", answers(BASE, 5) and p2.poll() is None)
    check("... the log says photag was already running", f"photag is already running on port {BASE}" in log_text())
    check("nothing was started on the next port", not answers(BASE + 1, 3))
    p2.terminate(); p2.wait(timeout=15)

    # ---- 4. a forced port is honoured
    env["PHOTAG_PORT"] = str(BASE + 5)
    p4 = start()
    check("PHOTAG_PORT forces a port", answers(BASE + 5), f"exit={p4.poll()}")
    p4.terminate(); p4.wait(timeout=15)
finally:
    for p in procs:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
    shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{sum(res)}/{len(res)} passed")
sys.exit(0 if all(res) else 1)
