"""UI test of the place-and-shape Develop tools (issue #6): lens corrections, geometry and Upright, crop ratio and guides, local masks
(brush, gradients, ranges), spot removal and red eye. Throw-away profile.

    py -3.12 tools/test_develop_local_ui.py            (set PHOTAG_TEST_BROWSER to a Chromium executable to use it instead of Edge)
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8801
APP = f"http://127.0.0.1:{PORT}"
tmp = Path(tempfile.mkdtemp(prefix="photag_devloc_test_"))
env = {**os.environ, "APPDATA": str(tmp / "appdata"), "LOCALAPPDATA": str(tmp / "local"), "USERPROFILE": str(tmp / "home"), "HOME": str(tmp / "home"),
       "PHOTAG_NO_OPEN": "1", "PYTHONIOENCODING": "utf-8", "PHOTAG_BACKUP_START_DELAY": "9999", "PHOTAG_EXIF_DELAY": "9999", "PHOTAG_MIGRATE_DELAY": "9999"}
for d in ("appdata", "local", "home", "lib", "src"):
    (tmp / d).mkdir()
res = []


def check(name, ok, extra=""):
    res.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{extra}]" if extra else ""))


def call(m, p, b=None):
    r = urllib.request.Request(APP + p, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=120).read() or b"{}")


def job(name, secs=120):
    t0 = time.time()
    while time.time() - t0 < secs:
        if call("GET", f"/api/job/{name}")["state"] in ("done", "error"):
            return
        time.sleep(0.3)


def scene(w, h, k):
    x = np.linspace(0, 1, w)[None, :, None]
    y = np.linspace(0, 1, h)[:, None, None]
    return np.clip(255 * k * np.concatenate([0.15 + 0.7 * x + 0 * y, 0.2 + 0.6 * y + 0 * x, 0.8 - 0.6 * x + 0 * y], axis=2), 0, 255).astype("uint8")


src = tmp / "src"
ex = Image.Exif()
ex[0x010F], ex[0x0110] = "UiMake", "UiModel"
Image.fromarray(scene(300, 200, 0.2)).save(src / "a_dark.jpg", quality=92, exif=ex)           # dark, with EXIF
Image.fromarray(scene(300, 200, 0.9)).save(src / "b_normal.jpg", quality=92)

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
    call("POST", "/api/import-folder", {"paths": [str(p) for p in sorted(src.iterdir())]})
    job("import")
    ph = {p["filename"]: p for p in call("GET", "/api/photos?limit=999")}
    A = ph["a_dark.jpg"]["id"]
    dbfile = call("GET", "/api/status")["db_path"]

    with sync_playwright() as pw:
        exe = os.environ.get("PHOTAG_TEST_BROWSER")
        b = pw.chromium.launch(executable_path=exe, headless=True, args=["--no-sandbox"]) if exe else pw.chromium.launch(channel="msedge", headless=True)
        pg = b.new_page(viewport={"width": 1500, "height": 950})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(APP + "/")
        pg.wait_for_selector(".cell", timeout=20000)
        pg.wait_for_timeout(800)

        def shot(name):
            if os.environ.get("PHOTAG_TEST_SHOTS"):
                pg.wait_for_timeout(500)
                pg.screenshot(path=str(Path(os.environ["PHOTAG_TEST_SHOTS"]) / f"{name}.png"))

        def cell(name):
            pg.locator(f".cell[data-id='{ph[name]['id']}']").click()
            pg.wait_for_timeout(500)

        def setslider(k, v):
            pg.evaluate("""([k,v])=>{const e=document.querySelector('#d-'+k); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));}""", [k, v])
            pg.wait_for_timeout(250)

        def setn(path, v):
            pg.evaluate("""([id,v])=>{const e=document.getElementById(id); e.value=v; e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true}));}""", [path, v])
            pg.wait_for_timeout(250)

        B = ph["b_normal.jpg"]["id"]

        def saved():
            return json.loads(call("GET", f"/api/photo/{B}")["edit_ops"] or "{}")

        def apply_():
            pg.click("#btn-dev-apply")
            pg.wait_for_function("!document.querySelector('#btn-dev-apply').classList.contains('dirty')", timeout=20000)
            pg.wait_for_timeout(700)

        def stage_xy(u, v):
            return pg.evaluate("([u,v])=>{const r=document.querySelector('#v-develop').getBoundingClientRect(); const p=imgToStage(u,v); return [r.left+p[0], r.top+p[1]];}", [u, v])

        cell("b_normal.jpg")
        pg.click("#modules [data-mod=develop]")
        pg.wait_for_selector("#d-exp", timeout=10000)
        pg.wait_for_timeout(800)
        check("the new panels exist (Lens Corrections, Geometry, Masking, Spot Removal)", all(pg.locator(f"#right [data-p={p}]").count() == 1 for p in ("lens", "geometry", "mask", "spot")))
        # ---- lens + geometry sliders
        check("the lens and geometry sliders exist", all(pg.locator(f"#d-{k}").count() == 1 for k in ("lnd", "lnv", "car", "cab", "dfr", "pv", "ph", "gas", "gsc", "gx", "gy")))
        check("Scale rests at 100 and the others at 0", pg.input_value("#d-gsc") == "100" and pg.input_value("#d-lnd") == "0")
        setslider("lnd", 30)
        pg.wait_for_function("document.querySelector('#dev-img').src.startsWith('blob:')", timeout=10000)
        check("a lens slider asks the server for a preview", True)
        setslider("pv", 25)
        setslider("lnv", 40)
        pg.check("[data-chk=caa]")
        pg.wait_for_timeout(300)
        shot("loc_lens")
        # ---- upright
        pg.click("[data-up=level]")
        pg.wait_for_timeout(1500)
        check("Upright puts a step in History", "Upright" in pg.inner_text("#p-history"))
        # ---- crop ratio and guides
        pg.click("[data-t=crop]")
        pg.wait_for_timeout(300)
        pg.select_option("select[data-aspect]", "1:1")
        pg.wait_for_timeout(400)
        crop = pg.evaluate("DEV.ops.crop")
        BW, BH = pg.evaluate("devBoxDims()")
        check("a square crop ratio makes a square crop", abs((crop[2] - crop[0]) * BW - (crop[3] - crop[1]) * BH) < 2, crop)
        pg.select_option("select[data-overlay]", "golden")
        pg.wait_for_timeout(200)
        check("the crop guide is switched to the golden ratio", pg.get_attribute("#crop-ov", "data-ovl") == "golden")
        pg.select_option("select[data-aspect]", "free")
        pg.click("[data-t=crop]")
        pg.wait_for_timeout(200)
        # ---- masks: a radial mask with an exposure change, drawn by dragging on the photo
        pg.click("[data-mkadd=radial]")
        pg.wait_for_timeout(600)
        check("adding a mask selects it and shows its handles on the photo", pg.locator("#p-mask .row[data-mk]").count() == 1 and pg.locator("#tool-ov [data-h=rx]").count() == 1)
        x0, y0 = stage_xy(0.2, 0.25)
        x1, y1 = stage_xy(0.4, 0.4)
        pg.mouse.move(x0, y0)
        pg.mouse.down()
        pg.mouse.move(x1, y1, steps=5)
        pg.mouse.up()
        pg.wait_for_timeout(500)
        rr = pg.evaluate("DEV.ops.mk[0].comps[0]")
        check("dragging on the photo draws the ellipse (centre where it started, radius grew)", abs(rr["cx"] - 0.2) < 0.03 and rr["rx"] > 0.1, rr)
        setn("n-mk-0-adj-exposure", 100)
        setn("n-mk-0-adj-saturation", -50)
        check("the exposure slider of the mask shows stops (+1.00)", pg.inner_text("#n-mk-0-adj-exposure + output").strip() == "+1.00")
        pg.wait_for_function("document.querySelector('#mask-tint') && !document.querySelector('#mask-tint').classList.contains('hidden')", timeout=10000)
        check("the selected mask is laid over the photo in red", True)
        shot("loc_mask")
        # a brush mask: paint a stroke with the mouse
        pg.select_option("select[data-mcadd]", "brush")
        pg.wait_for_timeout(500)
        px, py = stage_xy(0.2, 0.3)
        qx, qy = stage_xy(0.4, 0.3)
        pg.mouse.move(px, py)
        pg.mouse.down()
        pg.mouse.move(qx, qy, steps=6)
        pg.mouse.up()
        pg.wait_for_timeout(500)
        strokes = pg.evaluate("DEV.ops.mk[0].comps.find(c=>c.kind==='brush').strokes")
        check("painting with the brush records a stroke with its points", len(strokes) == 1 and len(strokes[0]["pts"]) >= 3, strokes and len(strokes[0]["pts"]))
        pg.select_option("select[data-mcop]", "subtract")
        pg.wait_for_timeout(400)
        check("a component can be set to Subtract", pg.evaluate("DEV.ops.mk[0].comps[1].op") == "subtract")
        # a second mask: sky, then delete it
        pg.click("[data-mkadd=sky]")
        pg.wait_for_timeout(500)
        check("a second mask appears in the list", pg.locator("#p-mask .row[data-mk]").count() == 2)
        pg.click("[data-mkdel='1']")
        pg.wait_for_timeout(400)
        check("...and can be deleted", pg.locator("#p-mask .row[data-mk]").count() == 1)
        # ---- spots and red eye
        pg.click("[data-tool=spot]")
        pg.wait_for_timeout(300)
        sx, sy = stage_xy(0.6, 0.35)
        pg.mouse.click(sx, sy)
        pg.wait_for_timeout(500)
        check("a click with the spot tool adds a spot", pg.evaluate("DEV.ops.sp.length") == 1 and pg.locator("#p-spot [data-sp]").count() == 1)
        pg.click("[data-tool=redeye]")
        pg.wait_for_timeout(300)
        ex_, ey_ = stage_xy(0.3, 0.6)
        pg.mouse.click(ex_, ey_)
        pg.wait_for_timeout(500)
        check("...and with the red-eye tool a red-eye fix", pg.evaluate("DEV.ops.rey.length") == 1)
        pg.click("[data-tool=redeye]")
        shot("loc_spot")
        apply_()
        o = saved()
        check("Apply saved the lens, geometry and ratio settings", o.get("lens_dist") == 30 and o.get("persp_v") == 25 and o.get("lens_vig") == 40 and o.get("ca_auto") is True, {k: o.get(k) for k in ("lens_dist", "persp_v", "lens_vig", "ca_auto")})
        mk = o.get("masks") or []
        check("...the mask (two components, exposure +1, saturation -50)", len(mk) == 1 and len(mk[0]["comps"]) == 2 and mk[0]["adj"].get("exposure") == 1 and mk[0]["adj"].get("saturation") == -50, mk and mk[0]["adj"])
        check("...the spot and the red eye", len(o.get("spots") or []) == 1 and len(o.get("redeye") or []) == 1)
        # reopen: all of it comes back
        pg.click("#modules [data-mod=library]")
        pg.wait_for_timeout(300)
        cell("a_dark.jpg")
        cell("b_normal.jpg")
        pg.click("#modules [data-mod=develop]")
        pg.wait_for_selector("#d-lnd", timeout=10000)
        pg.wait_for_timeout(800)
        check("reopened, the sliders and the lists show the saved values", pg.input_value("#d-lnd") == "30" and pg.evaluate("DEV.ops.mk.length") == 1 and pg.evaluate("DEV.ops.sp.length") == 1)
        pg.click("#btn-dev-reset")
        pg.wait_for_timeout(400)
        check("Reset clears masks and spots too", pg.evaluate("DEV.ops.mk.length") == 0 and pg.evaluate("DEV.ops.sp.length") == 0 and pg.input_value("#d-lnd") == "0")
        check("no JavaScript errors", not errs, errs[:3])
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

n_fail = res.count(False)
print(f"\n{len(res) - n_fail}/{len(res)} passed")
sys.exit(1 if n_fail else 0)
