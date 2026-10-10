"""UI test of: an animated GIF in the library (issue #23). It is stored as a video, but a <video> cannot play a GIF (the window said it
was not supported and to open it in an external player); the big view now shows it as a picture that plays by itself, and the
grid marks it "GIF". Throw-away profile.

    py -3.12 tools/test_gif_loupe.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8788
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_gif_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "src"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=60).read() or b"{}")


# a small animated GIF of three frames, and one ordinary photo next to it
from PIL import Image  # noqa: E402
frames = [Image.new("RGB", (120, 80), c) for c in ((220, 40, 40), (40, 200, 60), (40, 60, 220))]
gif = tmp / "src" / "dance.gif"
frames[0].save(gif, save_all=True, append_images=frames[1:], duration=120, loop=0)
Image.new("RGB", (200, 130), (90, 90, 90)).save(tmp / "src" / "plain.jpg")

srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--app-dir", str(ROOT), "--port", str(PORT)], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(ROOT))
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(APP + "/api/status", timeout=2)
            break
        except Exception:
            time.sleep(0.5)
    call("POST", "/api/settings/library", {"path": str(tmp / "lib")})
    call("POST", "/api/import-folder", {"paths": [str(gif), str(tmp / "src" / "plain.jpg")], "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)
    photos = {p["filename"]: p for p in call("GET", "/api/photos?limit=99")}
    check("the GIF and the photo were imported", set(photos) == {"dance.gif", "plain.jpg"}, sorted(photos))
    gid = photos["dance.gif"]["id"]
    r = urllib.request.urlopen(f"{APP}/media/{gid}", timeout=20)
    check("the server hands the GIF over as a GIF picture", r.headers.get("content-type") == "image/gif" and r.read(6) in (b"GIF89a", b"GIF87a"), r.headers.get("content-type"))

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(600)
        badges = pg.evaluate("[...document.querySelectorAll('.cell .dur')].map(e => e.textContent.trim())")
        check("the grid marks the GIF as GIF (not 'Video')", "GIF" in badges and "Video" not in badges, badges)

        pg.evaluate(f"selectOnly({gid}); setView('loupe')")
        pg.wait_for_selector("#loupe-media img", timeout=10000)
        pg.wait_for_timeout(800)
        info = pg.evaluate("""() => { const m = document.querySelector('#loupe-media'), i = m.querySelector('img');
          return {img: !!i, w: i ? i.naturalWidth : 0, video: !!m.querySelector('video'), err: !!m.querySelector('.vp-err:not(.hidden)'), text: m.innerText} }""")
        check("the big view shows the GIF as a picture that loaded", info["img"] and info["w"] == 120, info)
        check("...with no video player and no 'can't be played / open in an external player' message", not info["video"] and not info["err"] and "external" not in info["text"].lower(), info["text"][:60])

        # a real video-like format would still use the player: only GIF is special
        src = open(ROOT / "app" / "ui" / "app.js", encoding="utf-8").read()
        check("other videos still use the player", "p.is_video && ext(p)!=='GIF'" in src)
        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
