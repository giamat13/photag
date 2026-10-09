"""Runs the local tests one after the other and writes their progress to local-tests.json, which tools/release_status/status_page.py
shows live (what is running, what passed, how long is left). How long each test took last time is kept in local-tests-history.json
and is the estimate for the next run. Nothing here touches a real library: the tests use throw-away profiles.

    py -3.12 tools/release_status/run_local_tests.py            the usual set
    py -3.12 tools/release_status/run_local_tests.py test_pool ui_smoke      only these
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PROGRESS = HERE / "local-tests.json"
HISTORY = HERE / "local-tests-history.json"
DEFAULT = ["test_pool", "test_aitag", "test_place_search", "test_advanced_filters", "test_smart_features", "test_smart_ui",
           "test_features_ui", "ui_smoke", "test_release_notes", "test_version_scheme", "test_runtime_lock", "i18n check"]
GUESS = 45          # seconds, for a test that has never run


def load(p, default):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def command(name):
    if name == "i18n check":
        return [sys.executable, str(ROOT / "tools" / "i18n.py"), "check"]
    return [sys.executable, str(ROOT / "tools" / f"{name}.py")]


def summarize(text):
    """('12/12 passed' | '30 PASS' | ..., failed count) from what a test printed."""
    fails = len(re.findall(r"^FAIL", text, re.M))
    m = re.findall(r"(\d+)/(\d+) passed", text)
    if m:
        a, b = m[-1]
        return f"{a}/{b}", fails
    ok = len(re.findall(r"^PASS", text, re.M))
    if ok or fails:
        return f"{ok} עברו" + (f", {fails} נכשלו" if fails else ""), fails
    if "OK:" in text:
        return "תקין", fails
    return "", fails


def main():
    names = sys.argv[1:] or DEFAULT
    hist = load(HISTORY, {})
    state = {"started": time.time(), "finished": None,
             "tests": [{"name": n, "state": "todo", "seconds": None, "estimate": hist.get(n, GUESS), "summary": ""} for n in names]}
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for t in state["tests"]:
        t["state"], t["began"] = "run", time.time()
        PROGRESS.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
        r = subprocess.run(command(t["name"]), capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, cwd=str(ROOT))
        t["seconds"] = round(time.time() - t["began"], 1)
        t["summary"], fails = summarize(r.stdout)
        t["state"] = "done" if r.returncode == 0 and not fails else "fail"
        if t["state"] == "fail":
            t["detail"] = "\n".join(l for l in (r.stdout + r.stderr).splitlines() if l.startswith("FAIL") or "Error" in l)[-600:]
        else:
            hist[t["name"]] = t["seconds"]
        HISTORY.write_text(json.dumps(hist, ensure_ascii=False), "utf-8")
        PROGRESS.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    state["finished"] = time.time()
    PROGRESS.write_text(json.dumps(state, ensure_ascii=False), "utf-8")
    bad = [t["name"] for t in state["tests"] if t["state"] == "fail"]
    print("FAILED: " + ", ".join(bad) if bad else "all passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
