"""UI test of issue #25: a preview in the grid must never be stretched. The box of a thumbnail came from the stored width / height
(4:3 when there were none: videos, GIFs; or the shape of the file before its EXIF turn), and the picture was forced into it. Now the
box follows the thumbnail's own shape. Throw-away profile.

    py -3.12 tools/test_thumb_shape.py
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
PORT = 8790
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_shape_test_"))
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


from PIL import Image  # noqa: E402

src = tmp / "src"
# 1. a wide GIF (3:1): stored without width / height (it is handled like a video)
frames = [Image.new("RGB", (300, 100), c) for c in ((200, 40, 40), (40, 200, 60))]
frames[0].save(src / "wide.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
# 2. a JPEG that is stored wide (200x100) and turned to portrait by its EXIF orientation (6): shown 100x200
im = Image.new("RGB", (200, 100), (60, 120, 200))
ex = Image.Exif()
ex[0x0112] = 6
im.save(src / "turned.jpg", exif=ex)
# 3. an ordinary landscape picture and a square one
Image.new("RGB", (240, 160), (90, 90, 90)).save(src / "plain.jpg")
Image.new("RGB", (150, 150), (200, 200, 60)).save(src / "square.jpg")

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
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted(src.iterdir())], "keywords": [], "album": None})
    while call("GET", "/api/job/import")["state"] not in ("done", "error"):
        time.sleep(0.3)

    with sync_playwright() as pw:
        b = (pw.chromium.launch(executable_path=os.environ["PHOTAG_TEST_BROWSER"], headless=True, args=["--no-sandbox"]) if os.environ.get("PHOTAG_TEST_BROWSER") else pw.chromium.launch(channel="msedge", headless=True))
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell .ph img", timeout=20000)
        pg.wait_for_timeout(1500)
        rows = pg.evaluate("""() => [...document.querySelectorAll('.cell')].map(c => {
            const i = c.querySelector('.ph img');
            if(!i) return null;
            const r = i.getBoundingClientRect();
            return {name: c.querySelector('.f').textContent, natural: i.naturalWidth / i.naturalHeight, shown: r.width / r.height, nw: i.naturalWidth, nh: i.naturalHeight};
        }).filter(Boolean)""")
        check("all four pictures are shown in the grid", len(rows) == 4, [r["name"] for r in rows])
        for r in rows:
            off = abs(r["shown"] / r["natural"] - 1)
            check(f"{r['name']}: shown in its own shape ({r['natural']:.2f}), not stretched ({r['shown']:.2f})", off < 0.03, f"off by {off * 100:.1f}%")
        names = {r["name"]: r for r in rows}
        check("the EXIF-turned photo is shown as a portrait", names["turned.jpg"]["shown"] < 1, round(names["turned.jpg"]["shown"], 2))
        check("the wide GIF is shown wide", names["wide.gif"]["shown"] > 2, round(names["wide.gif"]["shown"], 2))
        check("no script errors", not errs, errs[:2])
        b.close()
finally:
    srv.terminate()
sys.exit(0 if all(res) else 1)
